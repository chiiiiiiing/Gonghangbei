from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app.server import app
from scripts.fetch_rates_structured_data import _mlf_amount, _mlf_rate, parse_pboc_afre_stock_text
from src.ai.gateway import AISettings
from src.rates.engine import _structured_context, append_review, load_backtest, load_forecast, load_status
from src.rates.factors import factor_scores, ground_predicates, independent_event_key, merge_llm_predicates
from src.rates.llm import extract_with_llm
from src.rates.modeling import labels_for_market, paired_block_bootstrap
from src.rates.rules import RULES, activate_rules
from src.rates.schema import (
    FLAT_THRESHOLD_BP,
    PREDICATES,
    STRUCTURED_FIELDS,
    direction_label,
    effective_trade_date,
    validate_structured_row,
)


class RatesResearchTests(unittest.TestCase):
    def test_direction_threshold_is_configurable_and_symmetric(self) -> None:
        self.assertEqual(FLAT_THRESHOLD_BP, 2.0)
        self.assertEqual(direction_label(2.01), "up")
        self.assertEqual(direction_label(-2.01), "down")
        self.assertEqual(direction_label(2.0), "flat")
        self.assertEqual(direction_label(-2.0), "flat")

    def test_after_close_and_non_trading_day_map_to_next_market_date(self) -> None:
        dates = ["2026-08-28", "2026-08-31", "2026-09-01"]
        self.assertEqual(effective_trade_date("2026-08-28 17:30:00", dates), "2026-08-28")
        self.assertEqual(effective_trade_date("2026-08-28 17:30:01", dates), "2026-08-31")
        self.assertEqual(effective_trade_date("2026-08-29 09:00:00", dates), "2026-08-31")

    def test_predicate_schema_is_complete_and_evidence_is_grounded(self) -> None:
        document = {
            "title": "公开市场操作",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性，保持流动性合理充裕。",
        }
        rows = ground_predicates(document)
        self.assertEqual({row["predicate_name"] for row in rows}, set(PREDICATES))
        active = [row for row in rows if row["value"]]
        self.assertIn("liquidity_supply_increases", {row["predicate_name"] for row in active})
        full_text = f"{document['title']}。{document['content']}"
        self.assertTrue(all(row["evidence_text"] in full_text for row in active))
        self.assertLess(factor_scores(rows)["liquidity"], 0)

    def test_rules_do_not_fire_without_evidence(self) -> None:
        rows = ground_predicates({"title": "无关文本", "content": "今天公布一份普通说明。"})
        self.assertEqual(activate_rules(rows), [])

    def test_llm_object_map_is_normalized_and_regrounded(self) -> None:
        settings = AISettings(
            mode="api", base_url="https://example.test", api_key="test", chat_model="test-model",
            embedding_model="", timeout_seconds=1, json_mode="object",
        )
        predicate_map = {name: False for name in PREDICATES}
        predicate_map["liquidity_supply_increases"] = True
        raw = {
            "summary": "流动性投放",
            "events": [{
                "subject": "中国人民银行", "action": "开展逆回购操作", "object": "市场",
                "policy_direction": "宽松", "intensity": "中等", "horizon": "短期",
                "transmission_channel": "流动性投放",
                "evidence_text": "中国人民银行开展逆回购操作，向市场投放流动性。",
                "confidence": True,
            }],
            "predicates": predicate_map,
        }
        document = {
            "title": "逆回购", "content": "中国人民银行开展逆回购操作，向市场投放流动性。",
            "source_name": "中国人民银行",
        }
        with patch("src.rates.llm.AISettings.from_environment", return_value=settings), patch(
            "src.rates.llm.OpenAICompatibleGateway.chat_json", return_value=(raw, {"request_id": "REQ-1"})
        ):
            result = extract_with_llm(document)
        self.assertTrue(result["used"])
        self.assertEqual(len(result["predicates"]), len(PREDICATES))
        active = next(row for row in result["predicates"] if row["predicate_name"] == "liquidity_supply_increases")
        self.assertIn(active["evidence_text"], document["content"])
        self.assertEqual(result["events"][0]["transmission_channel"], "liquidity")
        self.assertEqual(result["events"][0]["policy_direction"], -1)

    def test_llm_true_without_exact_evidence_cannot_enter_factor(self) -> None:
        deterministic = ground_predicates({
            "title": "逆回购",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性。",
            "source_name": "中国人民银行",
        })
        llm_rows = [
            {"predicate_name": name, "value": name == "liquidity_supply_increases", "evidence_text": "", "confidence": 0.9}
            for name in PREDICATES
        ]
        merged = merge_llm_predicates(deterministic, llm_rows, "逆回购。中国人民银行开展逆回购操作，向市场投放流动性。")
        liquidity = next(row for row in merged if row["predicate_name"] == "liquidity_supply_increases")
        self.assertFalse(liquidity["value"])
        self.assertEqual(liquidity["consensus"], "agreed_false")
        self.assertEqual(factor_scores(merged)["liquidity"], 0.0)

    def test_labels_only_use_strictly_future_fifth_market_row(self) -> None:
        rows = [
            {"trade_date": f"2026-01-{index + 1:02d}", "cgb_10y_yield": str(1.0 + index * 0.01), "dr007_proxy": "1.5"}
            for index in range(8)
        ]
        labels = labels_for_market(rows)
        self.assertEqual(labels[0], "up")
        self.assertIsNone(labels[-1])
        self.assertEqual(sum(label is None for label in labels), 5)

    def test_independent_event_key_collapses_repeated_calendar_notice(self) -> None:
        base = {
            "subject": "中国人民银行", "action": "投放", "object": "银行间流动性",
            "policy_direction": -1, "transmission_channel": "liquidity",
        }
        first = {**base, "evidence_text": "2026年8月1日开展逆回购投放100亿元。"}
        repeated = {**base, "evidence_text": "2026年8月2日开展逆回购投放100亿元。"}
        changed = {**base, "evidence_text": "2026年8月2日开展逆回购投放200亿元。"}
        self.assertEqual(independent_event_key(first), independent_event_key(repeated))
        self.assertNotEqual(independent_event_key(first), independent_event_key(changed))

    def test_structured_observation_requires_public_release_contract(self) -> None:
        row = dict.fromkeys(STRUCTURED_FIELDS, "")
        row.update({
            "observation_date": "2020-01-31", "release_time": "2020-02-15 23:59:59",
            "period_start": "2020-01-01", "period_end": "2020-01-31",
            "indicator": "cpi_yoy", "value": "5.4", "unit": "%",
            "source_name": "国家统计局", "source_url": "https://www.stats.gov.cn/",
            "source_sha256": "a" * 64, "vintage": "first_publication",
        })
        validate_structured_row(row)
        row["observation_date"] = "1900-01-01"
        with self.assertRaises(ValueError):
            validate_structured_row(row)

    def test_structured_value_is_invisible_before_public_release(self) -> None:
        market = [{"trade_date": "2020-02-14"}, {"trade_date": "2020-02-17"}]
        structured = [{"release_time": "2020-02-15 10:00:00", "indicator": "cpi_yoy", "value": "5.4"}]
        context, coverage = _structured_context(market, structured)
        self.assertNotIn("cpi_yoy", context["2020-02-14"])
        self.assertEqual(context["2020-02-17"]["cpi_yoy"], 5.4)
        self.assertEqual(coverage["cpi_yoy"], 1)

    def test_structured_context_keeps_latest_period_not_latest_file(self) -> None:
        market = [{"trade_date": "2025-10-14"}, {"trade_date": "2025-11-04"}, {"trade_date": "2025-11-06"}]
        structured = [
            {"release_time": "2025-10-15 09:00:00", "observation_date": "2025-10-31", "indicator": "afre_government_bonds", "value": "100"},
            {"release_time": "2025-11-05 09:00:00", "observation_date": "2019-12-31", "indicator": "afre_government_bonds", "value": "999"},
        ]
        context, _coverage = _structured_context(market, structured)
        self.assertEqual(context["2025-11-04"]["afre_government_bonds"], 100.0)
        self.assertEqual(context["2025-11-06"]["afre_government_bonds"], 100.0)

    def test_official_mlf_and_afre_parsers_reject_ambiguous_values(self) -> None:
        notice = (
            "人民银行开展中期借贷便利操作共7330亿元，其中期限6个月3580亿元、"
            "1年期3750亿元，利率分别为2.85%、3.0%。"
        )
        self.assertEqual(_mlf_amount(notice), 7330.0)
        self.assertEqual(_mlf_rate(notice), 3.0)
        self.assertIsNone(_mlf_rate("开展9000亿元MLF操作，采用多重价位中标。"))
        pdf_layout = "\n".join(
            f"2017.{index + 1:02d} 1800000 1000000 1 2 3 4 5 {220000 + index} 7 8 9"
            for index in range(36)
        )
        parsed = parse_pboc_afre_stock_text(pdf_layout)
        self.assertEqual(len(parsed), 36)
        self.assertEqual(parsed[0], ("2017-01-31", 220000.0))

    def test_bootstrap_converts_twenty_days_to_four_non_overlapping_windows(self) -> None:
        timeline = [
            {"as_of": f"2026-01-{index + 1:02d}", "correct": index % 2 == 0}
            for index in range(10)
        ]
        baseline = {"timeline": timeline}
        enhanced = {"timeline": [{**row, "correct": True} for row in timeline]}
        result = paired_block_bootstrap(baseline, enhanced, iterations=20, block_days=20)
        self.assertEqual(result["block_days"], 20)
        self.assertEqual(result["block_observations"], 4)

    def test_official_sample_loads_and_backtest_has_no_lookahead(self) -> None:
        status = load_status()
        self.assertTrue(status["data_ready"], status["data_errors"])
        self.assertGreaterEqual(status["market_rows"], 2000)
        self.assertGreaterEqual(status["text_rows"], 350)
        self.assertGreaterEqual(status["structured_rows"], 500)
        self.assertTrue(status["structured_data_ready"], status["data_errors"])
        self.assertEqual(status["structured_indicator_status"]["mlf_rate"], "sufficient")
        self.assertEqual(status["structured_indicator_status"]["government_bond_issuance"], "sufficient")
        self.assertEqual(status["structured_indicator_status"]["afre_government_bonds"], "sufficient")
        self.assertEqual(status["structured_data_status"], "sufficient")
        self.assertGreaterEqual(len(RULES), 10)
        forecast = load_forecast()
        self.assertEqual(forecast["horizon_trading_days"], 5)
        self.assertAlmostEqual(sum(forecast["probabilities"].values()), 1.0, places=5)
        backtest = load_backtest()
        self.assertEqual({row["route"] for row in backtest["routes"]}, {"market_baseline", "text_only", "fusion", "fusion_rules"})
        for route in backtest["routes"]:
            self.assertIn("macro_precision", route)
            self.assertIn("macro_recall", route)
            self.assertIn("macro_auc_ovr", route)
            self.assertEqual(route["training_policy"]["kind"], "purged_non_overlapping_rolling_window")
            self.assertEqual(route["training_policy"]["evaluation_stride_days"], 5)
            for row in route["timeline"]:
                self.assertLess(row["train_origin_end"], row["as_of"])
                self.assertLessEqual(row["label_known_through"], row["as_of"])
                self.assertLessEqual(row["train_observations"], 756)

    def test_rates_endpoints_and_single_text_marginal_output(self) -> None:
        client = app.test_client()
        for path in (
            "/api/rates/status", "/api/rates/forecast?horizon=5", "/api/rates/backtest",
            "/api/rates/evidence", "/api/rates/reviews", "/api/rates/demo-cases",
            "/api/rates/report",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
        invalid = client.get("/api/rates/forecast?horizon=10")
        self.assertEqual(invalid.status_code, 400)
        with patch("src.rates.engine.extract_with_llm", return_value={
            "used": False, "reason": "test fallback", "events": [], "predicates": [], "metadata": {},
        }):
            response = client.post("/api/rates/analyze", json={
                "title": "逆回购投放",
                "content": "中国人民银行开展逆回购操作，向市场投放流动性，保持流动性合理充裕。",
                "source_name": "中国人民银行",
                "source_url": "https://www.pbc.gov.cn/",
                "publish_time": "2026-08-28T09:30:00",
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["analysis_type"], "incremental_single_text")
        self.assertFalse(payload["llm_analysis"]["used"])
        self.assertAlmostEqual(sum(payload["updated_forecast"].values()), 1.0, places=5)
        self.assertIn("边际影响", payload["interpretation"])
        self.assertIn("processed_at", payload)

    def test_missing_market_snapshot_returns_evidence_insufficient(self) -> None:
        missing = Path("/path/that/does/not/exist.csv")
        with (
            patch("src.rates.engine.MARKET_PATH", missing),
            patch("src.rates.engine.TEXT_PATH", missing),
            patch("src.rates.engine.AUDIT_PATH", missing),
        ):
            status = load_status()
            forecast = load_forecast()
            backtest = load_backtest()
        self.assertFalse(status["data_ready"])
        self.assertEqual(forecast["status"], "research_evidence_insufficient")
        self.assertEqual(backtest["status"], "research_evidence_insufficient")

    def test_submission_exposes_only_rates_apis(self) -> None:
        routes = {
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/api/")
        }
        self.assertEqual(routes, {
            "/api/rates/status",
            "/api/rates/forecast",
            "/api/rates/backtest",
            "/api/rates/evidence",
            "/api/rates/reviews",
            "/api/rates/demo-cases",
            "/api/rates/report",
            "/api/rates/extract-file",
            "/api/rates/analyze",
            "/api/rates/review",
        })
        response = app.test_client().get("/api/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "接口不存在")
        asset_response = app.test_client().get("/vendor/lucide.min.js")
        try:
            self.assertEqual(asset_response.status_code, 200)
        finally:
            asset_response.close()

    def test_text_file_upload_extracts_content_without_analysis(self) -> None:
        client = app.test_client()
        response = client.post(
            "/api/rates/extract-file",
            data={"file": (BytesIO("货币政策保持稳健。".encode("utf-8")), "policy.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["content"], "货币政策保持稳健。")
        self.assertEqual(len(payload["sha256"]), 64)

    def test_review_is_append_only_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "reviews.jsonl"
            with patch("src.rates.engine.REVIEW_PATH", target):
                first = append_review({"document_id": "D1", "decision": "approved", "comment": "证据一致"})
                second = append_review({"document_id": "D1", "decision": "needs_revision", "comment": "补来源"})
            rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(first["review_id"], second["review_id"])
        with self.assertRaises(ValueError):
            append_review({"document_id": "D1", "decision": "unknown"})


if __name__ == "__main__":
    unittest.main()
