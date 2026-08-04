from __future__ import annotations

import hashlib
import unittest
import csv
from copy import deepcopy
from datetime import datetime

from app.server import SAMPLE_DIR, app, load_replay_cases
from src.ai.gateway import AIServiceError
from src.ai.research_layer import validate_ai_output
from src.pipeline.live_analysis import build_predicate_consensus, rule_matches


class AlphaLensAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = app.test_client()
        cls.replay_case = load_replay_cases()["storage-policy"]

    def test_public_endpoints_and_replay_label(self) -> None:
        for path in ("/api/status", "/api/backtest", "/api/audit"):
            self.assertEqual(self.client.get(path).status_code, 200)
        response = self.client.get("/api/replay/storage-policy")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["is_replay"])
        self.assertEqual(payload["replay_metadata"]["provenance"], "curated_demo_fixture_not_live_api")

    def test_ai_check_requires_key_without_retaining_credentials(self) -> None:
        response = self.client.post("/api/ai/check", json={})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_live_analysis_cannot_bypass_ai(self) -> None:
        request = dict(self.replay_case["request"])
        request["analysis_mode"] = "rule"
        response = self.client.post("/api/analyze", json=request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("hybrid", response.get_json()["error"])

    def test_manual_review_audit_matches_recorded_state(self) -> None:
        payload = self.client.get("/api/audit").get_json()
        self.assertEqual(payload["event_review"]["reviewed_count"], 10)
        self.assertEqual(payload["event_review"]["remediated_drop_count"], 10)
        self.assertEqual(payload["predicate_review"]["reviewed_count"], 0)

    def test_ai_output_requires_all_19_predicates(self) -> None:
        case = deepcopy(self.replay_case)
        case["ai_output"]["predicates"].pop()
        with self.assertRaises(AIServiceError):
            validate_ai_output(
                case["ai_output"],
                case["request"],
                self._stock_pool(),
            )

    def test_disputed_predicate_cannot_trigger_rule(self) -> None:
        deterministic = [
            {
                "predicate_name": "policy_support_is_clear",
                "value": "true",
                "confidence": "0.90",
                "rationale": "test",
            }
        ]
        ai_rows = [
            {
                "name": "policy_support_is_clear",
                "value": False,
                "confidence": 0.90,
                "rationale": "test",
            }
        ]
        consensus, gated = build_predicate_consensus(deterministic, ai_rows)
        self.assertEqual(consensus[0]["status"], "disputed")
        self.assertFalse(consensus[0]["accepted_for_rule"])
        self.assertFalse(
            rule_matches(
                {"condition": "policy_support_is_clear=true"},
                gated,
                "policy_support",
            )
        )

    def test_oos_is_separate_and_truthfully_insufficient(self) -> None:
        payload = self.client.get("/api/backtest").get_json()
        self.assertIn("discovery", payload["splits"])
        self.assertIn("oos", payload["splits"])
        self.assertEqual(payload["splits"]["oos"]["metrics"]["evidence_status"], "insufficient")
        self.assertIn("factor_coverage_rate", payload["splits"]["oos"]["metrics"])
        self.assertEqual(payload["decay_assessment"]["status"], "insufficient_evidence")

    def test_rule_support_is_independent_document_count(self) -> None:
        payload = self.client.get("/api/backtest").get_json()
        for rule in payload["qualified_rules"]:
            self.assertEqual(rule["support_count"], rule["independent_document_count"])

    def test_read_only_endpoints_preserve_raw_input(self) -> None:
        raw_path = SAMPLE_DIR / "raw_documents.csv"
        before = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        self.client.get("/api/replay/storage-policy")
        self.client.get("/api/backtest")
        after = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_locked_csv_headers_and_basic_formats(self) -> None:
        schemas = {
            "stock_pool.csv": ["stock_code", "stock_name", "industry_sector", "market_cap"],
            "raw_documents.csv": ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"],
            "entity_links.csv": ["doc_id", "stock_code", "stock_name", "industry", "confidence", "evidence"],
            "events.csv": ["event_id", "doc_id", "stock_code", "event_type", "event_time", "subject", "object", "impact_path", "evidence_text", "evidence_strength"],
            "predicates.csv": ["event_id", "predicate_name", "value", "confidence", "rationale"],
            "market_data.csv": ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"],
        }
        for filename, expected in schemas.items():
            with (SAMPLE_DIR / filename).open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, expected, filename)
                for row in reader:
                    self.assertNotIn("NaN", row.values(), filename)
                    if "stock_code" in row:
                        self.assertRegex(row["stock_code"], r"^\d{6}$", filename)
                    for field in ("publish_time", "event_time", "trade_date"):
                        if field in row:
                            datetime.strptime(row[field], "%Y-%m-%d")
                    if filename == "predicates.csv" and row["value"].lower() in {"true", "false"}:
                        self.assertIn(row["value"], {"true", "false"})

    def test_future_return_entry_is_after_event_time(self) -> None:
        with (SAMPLE_DIR / "event_forward_returns.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                self.assertEqual(row["future_info_ok"], "true")
                self.assertGreater(row["entry_trade_date"], row["event_time"])

    @staticmethod
    def _stock_pool() -> list[dict[str, str]]:
        import csv

        with (SAMPLE_DIR / "stock_pool.csv").open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
