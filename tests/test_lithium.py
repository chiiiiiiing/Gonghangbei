from __future__ import annotations

import math
import hashlib
import importlib.util
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.generate_lithium_local_annotations import eligible_for_annotation
from scripts.build_lithium_v3_research import (
    extract_direction_scores,
    predicate_schema,
    purged_discovery_records,
    recover_exact_evidence,
    stable_discovery_rulebook,
)
from scripts.fetch_cninfo_lithium_texts import title_selected
from scripts.record_lithium_prospective_decision import FIELDS, append_decision
from scripts.record_lithium_v4_prospective_decision import (
    FIELDS as V4_DECISION_FIELDS,
    SIGNAL_FIELDS as V4_SIGNAL_FIELDS,
    append_decision as append_v4_decision,
    append_signal as append_v4_signal,
)
from scripts.lithium_v4_prospective_integrity import verify_manifest as verify_v4_manifest
from scripts.update_lithium_v4_prospective import latest_decision_day
from src.lithium.engine import (
    PREDICATE_DEFINITIONS,
    RIFT_ADDITIVE_STRATEGY,
    _active_text_score,
    _strategy_rows,
    activated_rules,
    analyze_document,
    block_bootstrap_increment,
    build_main_continuous,
    build_prospective_decision,
    deterministic_predicates,
    evaluate_prospective_decisions,
    forward_label,
    induce_rulebook,
    map_prediction_to_strategy,
    predicate_consensus,
    run_backtest,
    text_provenance_report,
    validate_controlled_data,
)


GFEX_MARKET = "https://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml"
GFEX_WAREHOUSE = "https://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml"


def contract_row(day: date, contract: str, price: float, volume: int, oi: int | str) -> dict[str, str]:
    return {
        "trade_date": day.isoformat(), "contract": contract,
        "open": str(price), "high": str(price + 2), "low": str(price - 2),
        "close": str(price + 1), "settlement": str(price + 0.5),
        "volume": str(volume), "open_interest": str(oi),
        "source_name": "广州期货交易所", "source_url": GFEX_MARKET,
    }


def business_days(start: date, count: int) -> list[date]:
    result = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


class LithiumDataTests(unittest.TestCase):
    def test_v3_evidence_recovery_preserves_original_pdf_whitespace(self) -> None:
        source = "设计年产电池级碳酸锂 9,600 吨\n并逐步投产。"
        recovered = recover_exact_evidence("电池级碳酸锂9,600吨", source)
        self.assertEqual(recovered, "电池级碳酸锂 9,600 吨")

    def test_v3_stability_gate_uses_both_2024_halves_and_deduplicates_coverage(self) -> None:
        records = []
        for half, month in (("H1", 2), ("H2", 8)):
            for index in range(6):
                active = index < 3
                records.append({
                    "doc_id": f"{half}-{index}",
                    "publish_time": f"2024-{month:02d}-{index + 1:02d}",
                    "exit_trade_date": f"2024-{month:02d}-{index + 8:02d}",
                    "direction_label": "bullish" if index < 3 else "bearish",
                    "predicate_status": {
                        "warehouse_receipt_decline": "agreed_true" if active else "agreed_false",
                        "authoritative_source": "agreed_true" if active else "agreed_false",
                    },
                })
        candidates = [
            {
                "rule_id": "LC-BULL-01", "target_label": "bullish",
                "conditions": ["warehouse_receipt_decline"], "score": 0.5,
                "coverage_positive": 0.5, "coverage_negative": 0.0,
                "support_documents": 6, "support_dates": 6, "status": "qualified",
            },
            {
                "rule_id": "LC-BULL-02", "target_label": "bullish",
                "conditions": ["warehouse_receipt_decline", "authoritative_source"],
                "score": 0.5, "coverage_positive": 0.5, "coverage_negative": 0.0,
                "support_documents": 6, "support_dates": 6, "status": "qualified",
            },
        ]
        stable, diagnostics = stable_discovery_rulebook(records, candidates)
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(len(stable), 1)
        self.assertEqual(stable[0]["conditions"], ["warehouse_receipt_decline"])
        self.assertEqual(stable[0]["status"], "qualified_stable_discovery")

    def test_v3_predicate_schema_requires_every_fixed_predicate(self) -> None:
        schema = predicate_schema()
        self.assertEqual(set(schema["required"]), set(PREDICATE_DEFINITIONS))
        self.assertEqual(set(schema["properties"]), set(PREDICATE_DEFINITIONS))

    def test_v3_discovery_purge_excludes_labels_crossing_freeze_date(self) -> None:
        records = [
            {"doc_id": "kept", "publish_time": "2024-12-20", "exit_trade_date": "2024-12-30"},
            {"doc_id": "purged", "publish_time": "2024-12-30", "exit_trade_date": "2025-01-07"},
            {"doc_id": "validation", "publish_time": "2025-01-02", "exit_trade_date": "2025-01-09"},
        ]
        self.assertEqual(
            [row["doc_id"] for row in purged_discovery_records(records)],
            ["kept"],
        )

    def test_v4_zero_shot_and_rift_direction_calls_are_separate(self) -> None:
        class FakeGateway:
            settings = SimpleNamespace(chat_model="deepseek-v4-flash")

            def __init__(self) -> None:
                self.calls = []

            def chat_json(self, messages, schema, schema_name):
                self.calls.append((messages, schema_name))
                score = 0.2 if "zero_shot" in schema_name else 0.6
                return {
                    "direction_score": score,
                    "confidence": 0.7,
                    "evidence_text": "仓单减少100手",
                }, {"model": "deepseek-v4-flash", "request_id": schema_name}

        document = {
            "doc_id": "D1", "publish_time": "2025-01-02", "title": "仓单日报",
            "content": "碳酸锂仓单减少100手。", "source_name": "广州期货交易所",
            "predicate_consensus": [{
                "name": "warehouse_receipt_decline", "status": "agreed_true",
                "evidence_text": "仓单减少100手",
            }],
        }
        rulebook = [{
            "rule_id": "LC-BULL-01", "target_label": "bullish",
            "conditions": ["warehouse_receipt_decline"], "score": 0.06,
        }]
        gateway = FakeGateway()
        output, _ = extract_direction_scores(document, gateway, rulebook, [])
        self.assertEqual(len(gateway.calls), 2)
        zero_payload = gateway.calls[0][0][1]["content"]
        enhanced_payload = gateway.calls[1][0][1]["content"]
        self.assertNotIn("frozen_rulebook", zero_payload)
        self.assertIn("frozen_rulebook", enhanced_payload)
        self.assertEqual(output["zero_shot_score"], 0.2)
        self.assertEqual(output["rule_enhanced_score"], 0.6)

    def test_cninfo_selection_excludes_governance_and_keeps_lithium_projects(self) -> None:
        self.assertFalse(title_selected("002460", "第六届董事会决议公告"))
        self.assertFalse(title_selected("002460", "锂项目募集资金核查意见"))
        self.assertTrue(title_selected("002460", "Goulamina锂矿项目投产进展公告"))

    def test_rule_induction_requires_an_economic_anchor_when_configured(self) -> None:
        records = [
            {
                "doc_id": f"D{index}", "publish_time": f"2024-01-{index + 1:02d}",
                "direction_label": "bullish",
                "predicate_status": {"authoritative_source": "agreed_true"},
            }
            for index in range(5)
        ]
        self.assertEqual(
            induce_rulebook(records, anchor_predicates={"supply_expansion"}),
            [],
        )

    def test_controlled_data_validation_reports_schema_and_format_errors(self) -> None:
        texts = [{
            "doc_id": "D1", "source_type": "news", "title": "仓单", "content": "仓单增加",
            "publish_time": "2026/01/01", "source_name": "交易所", "url": "bad-url",
            "review_status": "maybe",
        }]
        contracts = [contract_row(date(2026, 1, 5), "BAD", -1, 1, 1)]
        contracts[0]["source_url"] = "bad-url"
        warehouse = [{
            "trade_date": "2026-01-05", "variety": "碳酸锂", "warehouse_receipt": "-1",
            "change": "0", "source_name": "交易所", "source_url": GFEX_WAREHOUSE,
        }]
        errors = validate_controlled_data(texts, contracts, warehouse)
        combined = "\n".join(errors)
        self.assertIn("publish_time", combined)
        self.assertIn("review_status", combined)
        self.assertIn("来源 url", combined)
        self.assertIn("contract 必须匹配", combined)
        self.assertIn("open 必须大于 0", combined)
        self.assertIn("warehouse_receipt 不能小于 0", combined)

    def test_text_provenance_verifies_content_hash_and_source(self) -> None:
        text = {
            "doc_id": "D1", "content": "广期所碳酸锂仓单减少100手。",
            "source_name": "广州期货交易所", "url": GFEX_WAREHOUSE,
        }
        audit = {
            "doc_id": "D1", "content_sha256": hashlib.sha256(text["content"].encode()).hexdigest(),
            "selected_chars": str(len(text["content"])), "source_name": text["source_name"],
            "url": text["url"],
            "provenance": "gfex_warehouse_receipt_structured_fact:discovery_abs_change_q75_ge_819",
            "fetch_status": "derived_from_audited_official_json",
        }
        valid = text_provenance_report([text], [audit])
        self.assertTrue(valid["verified"])
        self.assertEqual(valid["quality_counts"]["derived_official_fact"], 1)
        audit["content_sha256"] = "0" * 64
        invalid = text_provenance_report([text], [audit])
        self.assertFalse(invalid["verified"])
        self.assertIn("SHA-256", "\n".join(invalid["errors"]))

    def test_main_contract_uses_same_day_open_interest_and_volume_fallback(self) -> None:
        first = date(2026, 1, 5)
        rows = [
            contract_row(first, "LC2605", 100, 1000, 2000),
            contract_row(first, "LC2607", 110, 5000, 1500),
            contract_row(first + timedelta(days=1), "LC2605", 101, 1000, ""),
            contract_row(first + timedelta(days=1), "LC2607", 111, 5000, ""),
        ]
        continuous = build_main_continuous(rows)
        self.assertEqual(continuous[0]["contract"], "LC2605")
        self.assertEqual(continuous[0]["selection_basis"], "same_day_open_interest")
        self.assertEqual(continuous[1]["contract"], "LC2607")
        self.assertEqual(continuous[1]["selection_basis"], "same_day_volume_fallback")

    def test_forward_label_enters_after_publish_and_holds_known_contract_through_roll(self) -> None:
        days = business_days(date(2026, 1, 5), 9)
        rows = []
        for index, day in enumerate(days):
            # LC2605 rises 2% over the label horizon; LC2607 has a large level
            # difference and becomes dominant after entry.
            rows.append(contract_row(day, "LC2605", 100 + index * 0.4, 1000, 3000 if index < 2 else 1000))
            rows.append(contract_row(day, "LC2607", 150 + index, 1000, 1000 if index < 2 else 4000))
        continuous = build_main_continuous(rows)
        label = forward_label(days[0].isoformat(), continuous, contracts=rows)
        self.assertIsNotNone(label)
        assert label is not None
        self.assertEqual(label["entry_trade_date"], days[1].isoformat())
        self.assertEqual(label["exit_trade_date"], days[6].isoformat())
        expected = (100 + 6 * 0.4) / (100 + 1 * 0.4) - 1
        self.assertAlmostEqual(label["forward_open_return"], expected)
        self.assertEqual(label["direction_label"], "bullish")
        self.assertTrue(label["future_info_ok"])

    def test_ten_day_auxiliary_label_uses_same_entry_boundary(self) -> None:
        days = business_days(date(2026, 2, 2), 14)
        rows = [contract_row(day, "LC2701", 100 + index * 0.2, 1000, 5000) for index, day in enumerate(days)]
        continuous = build_main_continuous(rows)
        label = forward_label(days[0].isoformat(), continuous, horizon=10, contracts=rows)
        self.assertIsNotNone(label)
        assert label is not None
        self.assertEqual(label["horizon_days"], 10)
        self.assertEqual(label["entry_trade_date"], days[1].isoformat())
        self.assertEqual(label["exit_trade_date"], days[11].isoformat())

    def test_prospective_text_is_annotated_before_forward_label_exists(self) -> None:
        day = date(2026, 8, 14)
        contracts = [contract_row(day, "LC2701", 100, 1000, 5000)]
        continuous = build_main_continuous(contracts)
        prospective = {
            "doc_id": "P1", "publish_time": day.isoformat(), "review_status": "accepted",
        }
        historical = {
            "doc_id": "H1", "publish_time": "2026-08-13", "review_status": "accepted",
        }
        self.assertTrue(eligible_for_annotation(prospective, continuous, contracts))
        self.assertFalse(eligible_for_annotation(historical, continuous, contracts))


class LithiumRiftTests(unittest.TestCase):
    def _document(self) -> dict[str, str]:
        return {
            "doc_id": "D1", "title": "碳酸锂仓单增加",
            "content": "广州期货交易所仓单日报显示，碳酸锂仓单增加，临近交割可交割货源上升。",
            "publish_time": "2026-01-28", "source_name": "广州期货交易所", "url": "",
        }

    def _ai_predicates(self, document: dict[str, str], *, dispute_delivery: bool = False) -> list[dict]:
        deterministic = deterministic_predicates(document)
        rows = []
        for name, item in deterministic.items():
            value = bool(item["value"])
            if dispute_delivery and name == "delivery_pressure":
                value = False
            rows.append({
                "name": name, "value": value, "confidence": 0.8,
                "evidence_text": item["evidence_text"] if value else "",
            })
        return rows

    def test_warehouse_direction_predicates_are_mutually_exclusive(self) -> None:
        increase = deterministic_predicates({
            "title": "广期所碳酸锂仓单增加970手",
            "content": "广州期货交易所仓单日报显示，碳酸锂仓单增加970手。",
            "source_name": "广州期货交易所",
        })
        decline = deterministic_predicates({
            "title": "广期所碳酸锂仓单减少970手",
            "content": "广州期货交易所仓单日报显示，碳酸锂仓单减少970手。",
            "source_name": "广州期货交易所",
        })
        self.assertTrue(increase["warehouse_receipt_increase"]["value"])
        self.assertFalse(increase["warehouse_receipt_decline"]["value"])
        self.assertTrue(decline["warehouse_receipt_decline"]["value"])
        self.assertFalse(decline["warehouse_receipt_increase"]["value"])

    def test_only_agreed_true_predicates_activate_rules(self) -> None:
        document = self._document()
        consensus = predicate_consensus(
            deterministic_predicates(document),
            self._ai_predicates(document, dispute_delivery=True),
            f"{document['title']}\n{document['content']}",
        )
        rules = [{"rule_id": "LC-B01", "target_label": "bearish", "conditions": ["warehouse_receipt_increase", "delivery_pressure"]}]
        self.assertEqual(activated_rules(rules, consensus), [])
        statuses = {row["name"]: row["status"] for row in consensus}
        self.assertEqual(statuses["delivery_pressure"], "disputed")

    def test_rule_induction_is_discovery_only_and_enforces_support(self) -> None:
        records = []
        for index in range(6):
            records.append({
                "doc_id": f"B{index}", "publish_time": f"2024-01-{index + 1:02d}",
                "direction_label": "bullish",
                "predicate_status": {name: "agreed_true" if name == "inventory_drawdown" else "agreed_false" for name in PREDICATE_DEFINITIONS},
            })
        for index in range(6):
            records.append({
                "doc_id": f"N{index}", "publish_time": f"2024-02-{index + 1:02d}",
                "direction_label": "bearish",
                "predicate_status": {name: "agreed_true" if name == "inventory_build" else "agreed_false" for name in PREDICATE_DEFINITIONS},
            })
        records.append({
            "doc_id": "OOS", "publish_time": "2026-01-02", "direction_label": "bearish",
            "predicate_status": {name: "agreed_true" if name == "inventory_drawdown" else "agreed_false" for name in PREDICATE_DEFINITIONS},
        })
        rules = induce_rulebook(records)
        bullish = next(rule for rule in rules if rule["target_label"] == "bullish")
        self.assertEqual(len({rule["rule_id"] for rule in rules}), len(rules))
        self.assertTrue(all(rule["rule_id"].startswith("LC-BULL-") for rule in rules if rule["target_label"] == "bullish"))
        self.assertTrue(all(rule["rule_id"].startswith("LC-BEAR-") for rule in rules if rule["target_label"] == "bearish"))
        self.assertEqual(bullish["conditions"], ["inventory_drawdown"])
        self.assertEqual(bullish["coverage_positive"], 1.0)
        self.assertEqual(bullish["coverage_negative"], 0.0)
        self.assertEqual(bullish["support_documents"], 6)

    def test_inactive_rulebook_forces_neutral_instead_of_zero_shot_fallback(self) -> None:
        document = self._document()
        raw = {
            "direction_label": "bearish", "direction_score": -0.8, "zero_shot_score": -0.7,
            "confidence": 0.85, "horizon_days": 5, "evidence_text": "碳酸锂仓单增加",
            "predicates": self._ai_predicates(document),
        }

        class FakeGateway:
            settings = SimpleNamespace(chat_model="fake-model")

            def chat_json(self, messages, schema, schema_name):
                return raw, {"model": "fake-model", "request_id": "REQ-1", "usage": {}}

        result = analyze_document(document, FakeGateway(), [], [])
        self.assertEqual(result["direction_label"], "neutral")
        self.assertEqual(result["direction_score"], 0.0)
        self.assertEqual(result["inference_mode"], "rulebook_inactive")
        self.assertEqual(result["zero_shot_score"], -0.7)


class LithiumBacktestTests(unittest.TestCase):
    def _market(self) -> tuple[list[dict], list[dict[str, str]]]:
        days = business_days(date(2025, 1, 2), 285)
        rows = []
        for index, day in enumerate(days):
            price = 100 + 0.06 * index + 2.0 * math.sin(index / 11)
            rows.append(contract_row(day, "LC2711", price, 1000 + index, 5000 + index))
        return build_main_continuous(rows), rows

    def test_weekend_text_signal_starts_on_next_trading_day(self) -> None:
        days = ["2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15"]
        signals = [{"publish_time": "2026-01-10", "direction_score": 1.0, "confidence": 1.0}]
        self.assertEqual(_active_text_score(0, days, signals, "direction_score"), 0.0)
        self.assertEqual(_active_text_score(1, days, signals, "direction_score"), 1.0)

    def test_zero_shot_uses_its_own_confidence(self) -> None:
        days = ["2026-01-09"]
        signals = [{
            "publish_time": "2026-01-09",
            "zero_shot_score": 1.0,
            "zero_shot_confidence": 0.0,
            "confidence": 1.0,
        }]
        self.assertEqual(_active_text_score(0, days, signals, "zero_shot_score"), 0.0)

    def test_strategy_executes_at_next_open_and_uses_frozen_validation_scale(self) -> None:
        continuous, contracts = self._market()
        signals = [{"publish_time": "2026-01-05", "direction_score": 0.8, "zero_shot_score": 0.5, "confidence": 1.0}]
        rows = _strategy_rows(continuous, signals, 5.0, contracts)
        self.assertTrue(rows)
        for row in rows:
            self.assertGreater(row["trade_date"], row["signal_date"])
            self.assertGreaterEqual(row["position"], -1.0)
            self.assertLessEqual(row["position"], 1.0)
            self.assertGreater(row["validation_std"], 0.0)

    def test_prospective_overlay_only_adds_when_text_confirms_trend(self) -> None:
        continuous, contracts = self._market()
        signals = [{"publish_time": "2026-01-05", "direction_score": 0.8, "zero_shot_score": 0.0, "confidence": 1.0}]
        rows = [row for row in _strategy_rows(continuous, signals, 5.0, contracts) if row["strategy"] == "prospective_rule_confirmed_trend"]
        self.assertTrue(rows)
        for row in rows:
            trend = float(row["trend_score"])
            text = float(row["active_text_score"])
            if trend * text <= 0:
                self.assertAlmostEqual(float(row["position"]), trend)
            else:
                expected = max(-1.0, min(1.0, trend + text))
                self.assertAlmostEqual(float(row["position"]), expected)

    def test_additive_alpha_uses_frozen_weight_even_against_trend(self) -> None:
        continuous, contracts = self._market()
        signals = [{
            "publish_time": "2026-01-05", "direction_score": 0.05,
            "zero_shot_score": 0.0, "confidence": 1.0,
        }]
        rows = [
            row for row in _strategy_rows(continuous, signals, 5.0, contracts)
            if row["strategy"] == RIFT_ADDITIVE_STRATEGY
        ]
        active = next(row for row in rows if float(row["active_text_score"]) > 0)
        expected = max(-1.0, min(
            1.0,
            float(active["trend_score"]) + 4.0 * float(active["active_text_score"]),
        ))
        self.assertAlmostEqual(float(active["position"]), expected)

    def test_single_text_prediction_maps_to_same_date_trend_strategy(self) -> None:
        continuous, contracts = self._market()
        publish_time = continuous[-3]["trade_date"]
        baseline = map_prediction_to_strategy(publish_time, 0.0, continuous, contracts)
        self.assertEqual(baseline["status"], "mapped")
        direction = 0.4 if baseline["baseline_position"] >= 0 else -0.4
        confirmed = map_prediction_to_strategy(publish_time, direction, continuous, contracts)
        opposed = map_prediction_to_strategy(publish_time, -direction, continuous, contracts)
        self.assertEqual(confirmed["signal_market_date"], publish_time)
        self.assertGreater(confirmed["execution_trade_date"], publish_time)
        self.assertTrue(confirmed["text_confirmed_trend"])
        self.assertNotEqual(confirmed["position_delta"], 0.0)
        self.assertFalse(opposed["text_confirmed_trend"])
        self.assertAlmostEqual(opposed["position_delta"], 0.0)

    def test_prospective_decision_is_settled_only_from_frozen_position(self) -> None:
        continuous, contracts = self._market()
        signal_date = continuous[-3]["trade_date"]
        signals = [{
            "publish_time": signal_date, "direction_score": 0.8,
            "zero_shot_score": 0.0, "confidence": 1.0,
        }]
        decision = build_prospective_decision(signal_date, continuous, signals, contracts)
        decision["recorded_at"] = f"{signal_date}T18:00:00+08:00"
        rows, audit = evaluate_prospective_decisions(
            [{key: str(value) for key, value in decision.items()}],
            contracts,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(audit["settled_decisions"], 1)
        self.assertEqual(audit["invalid_decisions"], [])
        enhanced = next(row for row in rows if row["strategy"] == "prospective_rule_confirmed_trend")
        self.assertAlmostEqual(enhanced["position"], decision["enhanced_position"])

    def test_prospective_decision_ledger_rejects_same_day_rewrite(self) -> None:
        decision = {field: f"value-{field}" for field in FIELDS}
        decision.update({
            "signal_date": "2026-08-14",
            "recorded_at": "2026-08-14T18:00:00+08:00",
            "baseline_position": "0.1",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.csv"
            self.assertEqual(append_decision(path, decision), "recorded")
            replay = {**decision, "recorded_at": "2026-08-14T18:05:00+08:00"}
            self.assertEqual(append_decision(path, replay), "already_recorded")
            with self.assertRaisesRegex(ValueError, "已冻结决策发生变化"):
                append_decision(path, {**replay, "baseline_position": "0.2"})

    def test_v4_decision_ledger_is_append_only(self) -> None:
        decision = {field: "" for field in V4_DECISION_FIELDS}
        decision.update({
            "decision_id": "V4-D1",
            "recorded_at": "2026-08-14T20:00:00+08:00",
            "signal_date": "2026-08-14",
            "direction_score": 0.0,
            "strategy_version": "lithium-v4-rift-prospective-v1",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v4-decisions.csv"
            self.assertEqual(append_v4_decision(path, decision), "recorded")
            replay = {**decision, "recorded_at": "2026-08-14T20:05:00+08:00"}
            self.assertEqual(append_v4_decision(path, replay), "already_recorded")
            with self.assertRaisesRegex(ValueError, "已冻结 V4 决策发生变化"):
                append_v4_decision(path, {**replay, "direction_score": 0.2})

    def test_v4_signal_ledger_is_append_only(self) -> None:
        signal = {field: "" for field in V4_SIGNAL_FIELDS}
        signal.update({
            "doc_id": "GFEX-WR-LIVE-20260814",
            "recorded_at": "2026-08-14T20:00:00+08:00",
            "publish_time": "2026-08-14",
            "model": "deepseek-v4-flash",
            "direction_score": "0",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v4-signals.csv"
            self.assertEqual(append_v4_signal(path, signal), "recorded")
            replay = {**signal, "recorded_at": "2026-08-14T20:05:00+08:00"}
            self.assertEqual(append_v4_signal(path, replay), "already_recorded")
            with self.assertRaisesRegex(ValueError, "已冻结 V4 文本信号发生变化"):
                append_v4_signal(path, {**replay, "direction_score": "0.2"})

    def test_prospective_report_does_not_relabel_pre_freeze_history(self) -> None:
        continuous, contracts = self._market()
        result = run_backtest(continuous, [], contracts)["prospective_candidate"]
        self.assertEqual(result["prospective_start"], "2026-08-14")
        self.assertEqual(result["status"], "awaiting_new_oos_data")
        self.assertFalse(result["increment_established"])
        self.assertEqual(result["prospective_bootstrap"]["observations"], 0)
        self.assertIn("Validation 通过不等于", result["research_boundary"])

    def test_bootstrap_requires_positive_lower_bound(self) -> None:
        rows = []
        start = date(2026, 1, 2)
        for index in range(130):
            day = (start + timedelta(days=index)).isoformat()
            rows.append({"trade_date": day, "strategy": "pure_trend", "split": "oos", "net_return": 0.0})
            rows.append({"trade_date": day, "strategy": "rift_enhanced_trend", "split": "oos", "net_return": 0.001})
        result = block_bootstrap_increment(rows, samples=200)
        self.assertEqual(result["conclusion"], "positive_increment_established")
        self.assertGreater(result["ci_lower_95"], 0)

    def test_prospective_freeze_manifest_matches_current_frozen_inputs(self) -> None:
        path = "scripts/lithium_prospective_integrity.py"
        spec = importlib.util.spec_from_file_location("lithium_prospective_integrity", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.verify_manifest()
        self.assertTrue(result["verified"], result["mismatches"])

    def test_v4_prospective_prefix_matches_frozen_inputs(self) -> None:
        result = verify_v4_manifest()
        self.assertTrue(result["verified"], result["mismatches"])

    def test_v4_latest_market_day_has_a_frozen_decision(self) -> None:
        self.assertEqual(latest_decision_day(), "2026-08-14")


@unittest.skipUnless(importlib.util.find_spec("flask"), "Flask dependency is not installed")
class LithiumApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.server import app

        cls.client = app.test_client()

    def test_lithium_endpoints_disclose_boundary_and_real_data_status(self) -> None:
        for path in (
            "/api/lithium/status", "/api/lithium/forecast",
            "/api/lithium/backtest", "/api/lithium/research-v3",
            "/api/lithium/research-v4",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            payload = response.get_json()
            self.assertEqual(payload["disclaimer"], "本报告仅供研究参考，不构成投资建议")
            self.assertIn("不提供买卖建议", payload["research_boundary"])
        status = self.client.get("/api/lithium/status").get_json()
        self.assertEqual(status["target_name"], "碳酸锂主力连续合约未来5个交易日价格方向")
        self.assertEqual(status["status"], "ready")
        self.assertTrue(status["data_ready"])
        self.assertTrue(status["text_provenance"]["verified"])
        self.assertEqual(status["text_provenance"]["audited_documents"], status["counts"]["texts"])
        self.assertGreaterEqual(status["counts"]["continuous_days"], 743)
        self.assertGreater(status["counts"]["signals"], 0)
        self.assertGreater(status["counts"]["qualified_rules"], 0)
        v3 = self.client.get("/api/lithium/research-v3").get_json()
        self.assertEqual(v3["model"], "deepseek-v4-flash")
        self.assertEqual(v3["version"], "lithium-v3-rift-v4-direction")
        self.assertEqual(v3["counts"]["predicate_annotations"], 449)
        self.assertEqual(v3["counts"]["direction_annotations"], 259)
        self.assertEqual(v3["counts"]["rules"], 1)
        self.assertFalse(v3["increment_established"])
        self.assertGreater(
            v3["old_oos_stress_bootstrap"]["annualized_net_return_difference"],
            0,
        )
        self.assertLess(v3["old_oos_stress_bootstrap"]["ci_lower_95"], 0)
        self.assertLess(v3["old_oos_confirmed_trend_bootstrap"]["ci_upper_95"], 0)
        v4 = self.client.get("/api/lithium/research-v4").get_json()
        self.assertEqual(v4["latest_decision"]["model"], "deepseek-v4-flash")
        self.assertEqual(v4["decision_ledger"]["recorded_decisions"], 1)
        self.assertEqual(v4["decision_ledger"]["settled_decisions"], 0)
        self.assertEqual(v4["decision_ledger"]["invalid_decisions"], [])
        self.assertTrue(v4["prefix_integrity"]["verified"])
        self.assertEqual(v4["signal_audit"]["signals"], 1)
        self.assertEqual(v4["signal_audit"]["complete"], 0)
        self.assertEqual(v4["signal_audit"]["partial"], 1)
        self.assertEqual(v4["latest_update_run"]["status"], "no_new_market_data")
        self.assertFalse(v4["additive_candidate"]["increment_established"])
        backtest = self.client.get("/api/lithium/backtest").get_json()
        self.assertEqual(backtest["engine_version"], "lithium-backtest-v3-decision-ledger-20260814")
        self.assertFalse(backtest["increment_established"])
        self.assertEqual(backtest["conclusion"], "交易增量未建立")
        self.assertEqual(backtest["prospective_candidate"]["status"], "awaiting_new_oos_data")
        self.assertFalse(backtest["prospective_candidate"]["increment_established"])
        self.assertEqual(backtest["prospective_candidate"]["validation_bootstrap"]["conclusion"], "positive_increment_established")
        self.assertGreater(backtest["prospective_candidate"]["validation_bootstrap"]["ci_lower_95"], 0)
        self.assertLess(backtest["prospective_candidate"]["historical_oos_stress_bootstrap"]["ci_upper_95"], 0)
        ledger = backtest["prospective_candidate"]["decision_ledger"]
        self.assertGreaterEqual(ledger["recorded_decisions"], 1)
        self.assertEqual(ledger["invalid_decisions"], [])

    def test_lithium_analyze_rejects_missing_key_without_keyword_fallback(self) -> None:
        response = self.client.post("/api/lithium/analyze", json={
            "title": "碳酸锂仓单增加",
            "content": "广州期货交易所仓单日报显示，碳酸锂仓单增加。",
            "source_type": "news", "source_name": "广州期货交易所",
            "event_date": "2026-01-28", "source_url": "",
        })
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "ai_required")
        self.assertIn("不会用关键词结果冒充", payload["error"])

    def test_single_text_api_returns_prediction_and_strategy_increment_mapping(self) -> None:
        from app import server

        document = {
            "title": "碳酸锂仓单减少1000手",
            "content": "广州期货交易所仓单日报显示，碳酸锂仓单减少1000手。",
            "source_name": "广州期货交易所",
        }
        deterministic = deterministic_predicates(document)
        predicates = [
            {
                "name": name, "value": bool(item["value"]), "confidence": 0.9,
                "evidence_text": item["evidence_text"] if item["value"] else "",
            }
            for name, item in deterministic.items()
        ]

        class FakeGateway:
            settings = SimpleNamespace(chat_model="fake-model")

            def chat_json(self, messages, schema, schema_name):
                if schema_name == "lithium_v3_predicates":
                    return {
                        row["name"]: {
                            "value": row["value"],
                            "confidence": row["confidence"],
                            "evidence_text": row["evidence_text"],
                        }
                        for row in predicates
                    }, {"model": "fake-model", "request_id": "REQ-PREDICATES"}
                return {
                    "direction_score": 0.4 if "zero_shot" in schema_name else 0.7,
                    "confidence": 0.9,
                    "evidence_text": "碳酸锂仓单减少1000手",
                }, {"model": "fake-model", "request_id": schema_name}

        layer = SimpleNamespace(settings=SimpleNamespace(enabled=True), gateway=FakeGateway())
        with patch.object(server, "request_ai_layer", return_value=layer):
            response = self.client.post("/api/lithium/analyze", json={
                **document, "source_type": "announcement", "event_date": "2026-08-14",
                "source_url": "https://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml",
                "api_key": "test-key",
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["predicted_variable"]["name"],
            "lc_main_5d_open_to_open_direction_score",
        )
        self.assertEqual(payload["strategy_mapping"]["baseline_strategy"], "pure_trend_20d")
        self.assertEqual(payload["research_type"], "lithium_v4_rift_direction")
        self.assertEqual(payload["model"], "fake-model")
        self.assertEqual(payload["direction_score"], 0.7)
        self.assertIn(payload["strategy_mapping"]["status"], {"mapped", "awaiting_next_trading_day"})
        self.assertIn("position_delta", payload["strategy_mapping"])
        self.assertEqual(payload["increment_evidence"]["prospective_observations"], 0)

    def test_homepage_is_lithium_first_and_has_no_trading_promise(self) -> None:
        from app.server import APP_DIR

        html = (APP_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("碳酸锂文本规则预测", html)
        self.assertIn("研究验证", html)
        script = (APP_DIR / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("DeepSeek V4 规则增强方向推理", script)
        self.assertIn("V4 前瞻决策账本", script)
        self.assertIn("前瞻候选 v2", script)
        self.assertIn("不回填已观察的旧 OOS", script)
        self.assertNotIn("保证收益", html)
        self.assertNotIn("目标价", html)


if __name__ == "__main__":
    unittest.main()
