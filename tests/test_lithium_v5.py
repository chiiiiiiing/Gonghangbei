from __future__ import annotations

import csv
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
import tempfile

from src.lithium.engine import _read_csv, build_main_continuous
from src.lithium.walkforward import (
    BASELINE_STRATEGY,
    ENHANCED_STRATEGY,
    build_report,
    evaluate_prospective_decisions,
    map_live_prediction,
    mature_baseline_positions,
    quality_text_events,
    strategy_rows,
)
from scripts.lithium_v5_candidate_integrity import verify_manifest
from src.lithium.live_signal_ledger import (
    FIELDS as LIVE_SIGNAL_FIELDS,
    append_signal as append_live_signal,
    record_if_eligible,
)


GFEX_MARKET = "https://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml"


def business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def synthetic_contracts(count: int = 170) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, day in enumerate(business_days(date(2025, 1, 2), count)):
        price = 100_000 + index * 80 + ((index % 11) - 5) * 120
        rows.append({
            "trade_date": day.isoformat(),
            "contract": "LC3001",
            "open": str(price),
            "high": str(price + 500),
            "low": str(price - 500),
            "close": str(price + 100),
            "settlement": str(price + 50),
            "volume": "1000",
            "open_interest": "2000",
            "source_name": "广州期货交易所",
            "source_url": GFEX_MARKET,
        })
    return rows


def read_csv(path: str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class LithiumV5Tests(unittest.TestCase):
    def test_v5_candidate_freeze_matches_current_inputs_and_source(self) -> None:
        result = verify_manifest()
        self.assertTrue(result["verified"], result["mismatches"])

    def test_mature_baseline_does_not_read_future_entry_or_exit_opens(self) -> None:
        contracts = synthetic_contracts()
        continuous = build_main_continuous(contracts)
        original = mature_baseline_positions(continuous, contracts)
        signal_index = 145
        changed = [dict(row) for row in contracts]
        for row in changed:
            if row["trade_date"] in {
                continuous[signal_index + 1]["trade_date"],
                continuous[signal_index + 2]["trade_date"],
            }:
                row["open"] = str(float(row["open"]) * 1.8)
        mutated = mature_baseline_positions(
            build_main_continuous(changed), changed
        )
        self.assertAlmostEqual(
            original[signal_index]["position"], mutated[signal_index]["position"]
        )
        self.assertAlmostEqual(
            original[signal_index]["realized_volatility"],
            mutated[signal_index]["realized_volatility"],
        )

    def test_quality_gate_requires_bullish_authoritative_non_exchange_text(self) -> None:
        days = ["2026-08-13", "2026-08-14"]
        consensus = json.dumps([{
            "name": "authoritative_source", "status": "agreed_true"
        }])
        signals = [
            {
                "doc_id": "accepted", "publish_time": "2026-08-14",
                "zero_shot_score": "0.4", "zero_shot_confidence": "0.6",
                "predicate_consensus": consensus,
            },
            {
                "doc_id": "exchange", "publish_time": "2026-08-14",
                "zero_shot_score": "0.4", "zero_shot_confidence": "0.6",
                "predicate_consensus": consensus,
            },
            {
                "doc_id": "bearish", "publish_time": "2026-08-14",
                "zero_shot_score": "-0.4", "zero_shot_confidence": "0.6",
                "predicate_consensus": consensus,
            },
        ]
        texts = [
            {"doc_id": "accepted", "source_name": "中国政府网"},
            {"doc_id": "exchange", "source_name": "广州期货交易所"},
            {"doc_id": "bearish", "source_name": "中国政府网"},
        ]
        events, audit = quality_text_events(days, signals, texts)
        self.assertEqual(audit["accepted_signals"], 1)
        self.assertEqual(events[1][0]["doc_id"], "accepted")

    def test_live_signal_ledger_is_append_only_and_rejects_mutation(self) -> None:
        signal = {field: "" for field in LIVE_SIGNAL_FIELDS}
        signal.update({
            "signal_id": "V5-S1",
            "recorded_at": "2026-08-15T08:00:00+08:00",
            "doc_id": "V5-LIVE-S1",
            "publish_time": "2026-08-15",
            "model": "deepseek-v4-flash",
            "zero_shot_score": "0.4",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            self.assertEqual(append_live_signal(path, signal), "recorded")
            replay = {**signal, "recorded_at": "2026-08-15T08:05:00+08:00"}
            self.assertEqual(append_live_signal(path, replay), "already_recorded")
            with self.assertRaisesRegex(ValueError, "已冻结 V5 实时信号发生变化"):
                append_live_signal(path, {**replay, "zero_shot_score": "0.5"})

    def test_live_signal_recording_requires_new_dated_source_and_v4_model(self) -> None:
        document = {
            "publish_time": "2026-08-15", "source_name": "中国政府网",
            "url": "https://example.test/policy", "title": "储能政策",
            "content": "政策支持储能需求。", "source_type": "policy",
        }
        result = {
            "model": "deepseek-v4-flash", "zero_shot_score": 0.4,
            "zero_shot_confidence": 0.8, "predicate_consensus": [{
                "name": "authoritative_source", "status": "agreed_true"
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            status = record_if_eligible(path, document, result, True)
            self.assertEqual(status["status"], "recorded")
            self.assertTrue(status["quality_rule_active"])
            old = record_if_eligible(
                path, {**document, "publish_time": "2026-08-14"}, result, True
            )
            self.assertEqual(old["status"], "not_eligible_historical_date")

    def test_inactive_text_alpha_equals_baseline_position_exactly(self) -> None:
        contracts = synthetic_contracts()
        continuous = build_main_continuous(contracts)
        rows, audit = strategy_rows(continuous, contracts, [], [])
        by_key = {
            (row["trade_date"], row["strategy"]): row for row in rows
        }
        for day in {row["trade_date"] for row in rows}:
            baseline = by_key[(day, BASELINE_STRATEGY)]
            enhanced = by_key[(day, ENHANCED_STRATEGY)]
            self.assertEqual(baseline["position"], enhanced["position"])
            self.assertEqual(enhanced["position_delta"], 0.0)
        self.assertEqual(audit["accepted_signals"], 0)

    def test_live_quality_rule_maps_bullish_text_to_incremental_position(self) -> None:
        contracts = synthetic_contracts()
        continuous = build_main_continuous(contracts)
        document = {
            "publish_time": continuous[-2]["trade_date"],
            "source_name": "中国政府网",
        }
        prediction = {
            "zero_shot_score": 0.4,
            "zero_shot_confidence": 0.8,
            "predicate_consensus": [{
                "name": "authoritative_source", "status": "agreed_true"
            }],
        }
        mapping = map_live_prediction(
            document, prediction, continuous, contracts
        )
        self.assertTrue(mapping["quality_rule_active"])
        self.assertTrue(mapping["text_confirmed"])
        self.assertGreater(mapping["position_delta"], 0)

    def test_v5_prospective_settlement_rejects_post_entry_records(self) -> None:
        contracts = synthetic_contracts()
        continuous = build_main_continuous(contracts)
        signal_index = 145
        signal_day = continuous[signal_index]["trade_date"]
        entry_day = continuous[signal_index + 1]["trade_date"]
        decision = {
            "signal_date": signal_day,
            "recorded_at": signal_day + "T18:00:00+08:00",
            "selected_contract": "LC3001",
            "baseline_position": "0.2",
            "enhanced_position": "0.3",
            "position_delta": "0.1",
            "active_text_score": "0.4",
            "cost_bps": "5",
        }
        rows, audit = evaluate_prospective_decisions([decision], contracts)
        self.assertEqual(audit["settled_decisions"], 1)
        self.assertEqual(len(rows), 2)
        late = {**decision, "recorded_at": entry_day + "T09:31:00+08:00"}
        late_rows, late_audit = evaluate_prospective_decisions([late], contracts)
        self.assertEqual(late_rows, [])
        self.assertEqual(
            late_audit["invalid_decisions"][0]["reason"],
            "recorded_after_entry_open",
        )

    def test_checked_in_v5_historical_evidence_passes_cost_gate(self) -> None:
        contracts = _read_csv("lithium_contract_daily.csv")
        report, _ = build_report(
            build_main_continuous(contracts),
            contracts,
            read_csv("data/research/lithium_v3_signals.csv"),
            read_csv("data/research/lithium_v3_texts.csv"),
        )
        self.assertTrue(report["retrospective_increment_evidence"])
        self.assertGreater(
            report["oos_stress_bootstrap"]["ci_lower_95"], 0
        )
        self.assertFalse(report["strict_increment_established"])
        by_cost = {row["cost_bps"]: row for row in report["cost_sensitivity"]}
        self.assertGreater(
            by_cost[10.0]["oos_stress_bootstrap"]["ci_lower_95"], 0
        )


if __name__ == "__main__":
    unittest.main()
