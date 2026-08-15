from __future__ import annotations

import csv
import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from src.lithium.engine import _read_csv, build_main_continuous
from src.lithium.walkforward_v6 import (
    BASELINE_STRATEGY,
    V6_ENHANCED_STRATEGY,
    build_report_v6,
    map_live_prediction_v6,
    quality_text_events_v6,
    strategy_rows_v6,
)
from scripts.lithium_v6_candidate_integrity import verify_manifest


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


class LithiumV6Tests(unittest.TestCase):
    def test_v6_candidate_freeze_matches_current_inputs_and_source(self) -> None:
        result = verify_manifest()
        self.assertTrue(result["verified"], result["mismatches"])

    def test_quality_gate_rejects_weak_exchange_and_uncertain_signals(self) -> None:
        days = ["2026-08-13", "2026-08-14"]
        consensus = json.dumps([
            {"name": "authoritative_source", "status": "agreed_true"}
        ])
        uncertain_consensus = json.dumps([
            {"name": "authoritative_source", "status": "agreed_true"},
            {"name": "uncertainty_high", "status": "agreed_true"},
        ])
        signals = [
            {
                "doc_id": "accepted", "publish_time": "2026-08-14",
                "zero_shot_score": "0.4", "zero_shot_confidence": "0.6",
                "predicate_consensus": consensus,
            },
            {
                "doc_id": "weak", "publish_time": "2026-08-14",
                "zero_shot_score": "0.1", "zero_shot_confidence": "0.6",
                "predicate_consensus": consensus,
            },
            {
                "doc_id": "exchange", "publish_time": "2026-08-14",
                "zero_shot_score": "0.4", "zero_shot_confidence": "0.6",
                "predicate_consensus": consensus,
            },
            {
                "doc_id": "uncertain", "publish_time": "2026-08-14",
                "zero_shot_score": "0.4", "zero_shot_confidence": "0.6",
                "predicate_consensus": uncertain_consensus,
            },
        ]
        texts = [
            {"doc_id": "accepted", "source_name": "中国政府网"},
            {"doc_id": "weak", "source_name": "中国政府网"},
            {"doc_id": "exchange", "source_name": "广州期货交易所"},
            {"doc_id": "uncertain", "source_name": "中国政府网"},
        ]
        events, audit = quality_text_events_v6(days, signals, texts)
        self.assertEqual(audit["accepted_signals"], 1)
        self.assertEqual(events[1][0]["doc_id"], "accepted")
        self.assertEqual(audit["rejected_below_threshold"], 1)
        self.assertEqual(audit["rejected_exchange_derived"], 1)
        self.assertEqual(audit["rejected_uncertainty"], 1)

    def test_inactive_text_alpha_equals_baseline_position_exactly(self) -> None:
        contracts = synthetic_contracts()
        continuous = build_main_continuous(contracts)
        rows, audit = strategy_rows_v6(continuous, contracts, [], [])
        by_key = {
            (row["trade_date"], row["strategy"]): row for row in rows
        }
        for day in {row["trade_date"] for row in rows}:
            baseline = by_key[(day, BASELINE_STRATEGY)]
            enhanced = by_key[(day, V6_ENHANCED_STRATEGY)]
            self.assertEqual(baseline["position"], enhanced["position"])
            self.assertEqual(enhanced["position_delta"], 0.0)
        self.assertEqual(audit["accepted_signals"], 0)

    def test_live_quality_rule_maps_strong_bullish_text_to_incremental_position(self) -> None:
        contracts = synthetic_contracts()
        continuous = build_main_continuous(contracts)
        document = {
            "publish_time": continuous[-2]["trade_date"],
            "source_name": "中国政府网",
        }
        prediction = {
            "zero_shot_score": 0.4,
            "zero_shot_confidence": 0.8,
            "predicate_consensus": [
                {"name": "authoritative_source", "status": "agreed_true"},
                {"name": "uncertainty_high", "status": "agreed_false"},
            ],
        }
        mapping = map_live_prediction_v6(
            document, prediction, continuous, contracts
        )
        self.assertTrue(mapping["quality_rule_active"])
        self.assertTrue(mapping["text_confirmed"])
        self.assertGreater(mapping["position_delta"], 0)

    def test_checked_in_v6_historical_evidence_passes_cost_gate(self) -> None:
        contracts = _read_csv("lithium_contract_daily.csv")
        report, _ = build_report_v6(
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
