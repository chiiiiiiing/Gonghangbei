from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.server import app
from src.rates.engine import append_review, load_backtest, load_forecast, load_status
from src.rates.factors import factor_scores, ground_predicates
from src.rates.modeling import labels_for_market
from src.rates.rules import activate_rules
from src.rates.schema import (
    FLAT_THRESHOLD_BP,
    PREDICATES,
    direction_label,
    effective_trade_date,
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

    def test_labels_only_use_strictly_future_fifth_market_row(self) -> None:
        rows = [
            {"trade_date": f"2026-01-{index + 1:02d}", "cgb_10y_yield": str(1.0 + index * 0.01), "dr007_proxy": "1.5"}
            for index in range(8)
        ]
        labels = labels_for_market(rows)
        self.assertEqual(labels[0], "up")
        self.assertIsNone(labels[-1])
        self.assertEqual(sum(label is None for label in labels), 5)

    def test_official_sample_loads_and_backtest_has_no_lookahead(self) -> None:
        status = load_status()
        self.assertTrue(status["data_ready"], status["data_errors"])
        self.assertEqual(status["market_rows"], 163)
        forecast = load_forecast()
        self.assertEqual(forecast["horizon_trading_days"], 5)
        self.assertAlmostEqual(sum(forecast["probabilities"].values()), 1.0, places=5)
        backtest = load_backtest()
        self.assertEqual({row["route"] for row in backtest["routes"]}, {"market_baseline", "text_only", "fusion", "fusion_rules"})
        for route in backtest["routes"]:
            for row in route["timeline"]:
                self.assertLess(row["train_end"], row["as_of"])

    def test_rates_endpoints_and_single_text_marginal_output(self) -> None:
        client = app.test_client()
        for path in ("/api/rates/status", "/api/rates/forecast?horizon=5", "/api/rates/backtest"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
        invalid = client.get("/api/rates/forecast?horizon=10")
        self.assertEqual(invalid.status_code, 400)
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
