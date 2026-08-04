from __future__ import annotations

import hashlib
import unittest
import csv
import json
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.server import SAMPLE_DIR, app, load_replay_cases
from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.ai.research_layer import AIResearchLayer, validate_ai_output
from src.ingestion.text_import import FIELDS, stage_manifest
from src.pipeline.live_analysis import (
    build_entity_consensus,
    build_event_consensus,
    build_predicate_consensus,
    rule_matches,
)
from src.research.scoring import beta_impact_probability, evidence_score_breakdown


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
        self.assertTrue(payload["consensus_gate_passed"])
        self.assertEqual(payload["disputed_predicates"], [])

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

    def test_live_ai_requires_per_stock_predicates(self) -> None:
        case = deepcopy(self.replay_case)
        case["ai_output"].pop("stock_analyses")
        with self.assertRaises(AIServiceError):
            validate_ai_output(
                case["ai_output"],
                case["request"],
                self._stock_pool(),
                require_stock_level=True,
            )

    def test_model_identity_mismatch_is_rejected(self) -> None:
        settings = AISettings(
            mode="api",
            base_url="https://api.deepseek.com",
            api_key="test-key",
            chat_model="deepseek-v4-flash",
            embedding_model="",
            timeout_seconds=1,
            json_mode="object",
        )

        class MismatchGateway(OpenAICompatibleGateway):
            def _get(self, path):
                return {"data": [{"id": "deepseek-v4-flash", "owned_by": "deepseek"}]}

            def _post(self, path, payload):
                return {"id": "request-test", "model": "deepseek-v4-pro"}

        with self.assertRaises(AIServiceError):
            MismatchGateway(settings).check_connection()

    def test_invalid_event_type_is_repaired_by_second_ai_call(self) -> None:
        valid_output = deepcopy(self.replay_case["ai_output"])
        invalid_output = deepcopy(valid_output)
        invalid_output["event"]["event_type"] = "政策支持"

        class RepairingGateway:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def chat_json(self, messages, schema, schema_name):
                self.calls.append(messages)
                output = invalid_output if len(self.calls) == 1 else valid_output
                return output, {
                    "request_id": f"request-{len(self.calls)}",
                    "usage": {},
                    "response_format": "json_object",
                }

        gateway = RepairingGateway()
        layer = AIResearchLayer(gateway=gateway)
        layer.settings = type(
            "EnabledSettings",
            (),
            {
                "enabled": True,
                "embedding_model": "",
                "public_status": lambda self: {
                    "configured": True,
                    "chat_model": "deepseek-v4-flash",
                },
            },
        )()
        request = self.replay_case["request"]
        document = {
            "doc_id": "TEST-DOC",
            "source_type": request["source_type"],
            "title": request["title"],
            "content": request["content"],
            "publish_time": request["event_date"],
            "source_name": request["source_name"],
            "url": request["source_url"],
        }
        result = layer.analyze(document, self._stock_pool(), [])
        self.assertTrue(result["used"])
        self.assertTrue(result["repair_attempted"])
        self.assertEqual(result["initial_request_id"], "request-1")
        self.assertEqual(result["request_id"], "request-2")
        self.assertEqual(result["result"]["event"]["event_type"], "policy_support")
        self.assertEqual(len(gateway.calls), 2)
        self.assertIn("收到：政策支持", gateway.calls[1][-1]["content"])

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

    def test_event_and_entity_gates_fail_closed(self) -> None:
        event_gate = build_event_consensus(
            "policy_support",
            {"event_type": "attention_spread", "evidence_grounded": True},
        )
        self.assertEqual(event_gate["status"], "disputed")
        self.assertFalse(event_gate["accepted"])
        entity_gate = build_entity_consensus(
            {
                "stock_code": "605117",
                "evidence": "产业主题映射",
            },
            {
                "relationship_grounded": False,
                "relationship_confidence": 0.99,
                "relationship_evidence": "不存在于原文",
            },
        )
        self.assertFalse(entity_gate["accepted"])

    def test_evidence_score_uses_conservative_ai_minimum(self) -> None:
        document = {
            "source_type": "policy",
            "source_name": "国家能源局",
            "title": "关于印发储能建设行动方案的通知",
            "content": "国家能源局印发储能建设行动方案，项目规模为 10GWh。",
        }
        event = {"evidence_text": "国家能源局印发储能建设行动方案"}
        entity = {"stock_name": "德业股份", "evidence": "产业主题映射\"储能\"→储能"}
        result = evidence_score_breakdown(
            document,
            event,
            entity,
            {"evidence_grounding": 0.2, "information_specificity": 1, "business_relevance": 1},
        )
        self.assertEqual(result["final_components"]["evidence_grounding"], 0.2)
        self.assertEqual(beta_impact_probability(1, 17), 3 / 21)

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

    def test_text_manifest_stages_without_touching_raw_input(self) -> None:
        before = hashlib.sha256((SAMPLE_DIR / "raw_documents.csv").read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "REAL-001",
                        "source_type": "policy",
                        "title": "关于新能源项目建设的正式通知",
                        "content": "原文摘要：主管部门发布新能源项目建设正式通知，并明确项目范围、执行日期和责任单位。",
                        "publish_time": "2025-01-02",
                        "source_name": "政府网站",
                        "url": "https://example.gov.cn/policy/real-001.html",
                    }
                )
            staged = stage_manifest(manifest, root / "staging")
            self.assertTrue(staged.exists())
            report = json.loads((root / "staging" / "文本导入校验报告.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
        after = hashlib.sha256((SAMPLE_DIR / "raw_documents.csv").read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_homepage_does_not_show_audit_versions(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Git", html)
        self.assertNotIn("Prompt", html)
        self.assertNotIn("原始文本", html)

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
