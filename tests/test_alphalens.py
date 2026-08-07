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
from src.ai import rag
from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.ai.research_layer import AIResearchLayer, validate_ai_output
from src.ingestion.text_import import FIELDS, stage_manifest
from src.pipeline.extract_events_rule_based import _truncate_at_boundary
from src.pipeline.live_analysis import (
    build_entity_consensus,
    build_event_consensus,
    build_predicate_consensus,
    build_rule_explainability,
    evaluate_ai_candidate_rules,
    fuse_predicate_values,
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

    def test_disputed_predicate_audit_gate_blocks_but_fusion_partially_counts(self) -> None:
        """Audit table still gates disputed as not-fully-accepted, while the live
        fusion path lets AI pull a disputed predicate toward its value."""
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
                {"condition": "policy_support_is_clear"},
                gated,
                "policy_support",
            )
        )
        fused = fuse_predicate_values(
            {"policy_support_is_clear": "true"}, ai_rows
        )
        self.assertEqual(fused["policy_support_is_clear"]["source"], "disputed")
        self.assertAlmostEqual(fused["policy_support_is_clear"]["fused"], 0.5)
        fused_map = {name: item["fused"] for name, item in fused.items()}
        self.assertTrue(
            rule_matches({"condition": "policy_support_is_clear"}, fused_map, "policy_support")
        )

    def test_fuse_predicate_values_statuses(self) -> None:
        agreed_true = fuse_predicate_values(
            {"a": "true"}, [{"name": "a", "value": "true", "confidence": 0.9}]
        )["a"]
        self.assertEqual(agreed_true["source"], "agreed_true")
        self.assertEqual(agreed_true["fused"], 1.0)
        agreed_false = fuse_predicate_values(
            {"b": "false"}, [{"name": "b", "value": "false", "confidence": 0.8}]
        )["b"]
        self.assertEqual(agreed_false["fused"], 0.0)
        invalid = fuse_predicate_values({"c": "true"}, [])["c"]
        self.assertEqual(invalid["source"], "rule_only")
        self.assertEqual(invalid["fused"], 1.0)
        score_pull = fuse_predicate_values(
            {"d": "0.90"}, [{"name": "d", "value": "0.50", "confidence": 0.8}]
        )["d"]
        self.assertAlmostEqual(score_pull["fused"], 0.70, places=3)

    def test_ai_candidate_rules_contribute_to_factor(self) -> None:
        fused_map = {"has_policy_support": 1.0, "policy_attention_followup": 1.0, "demand_side_policy": 0.0}
        ai_result = {
            "candidate_rules": [
                {
                    "name": "测试候选",
                    "conditions": ["has_policy_support", "policy_attention_followup"],
                    "target_label": "policy_signal",
                    "confidence": 0.8,
                    "rationale": "测试",
                }
            ]
        }
        rows, total = evaluate_ai_candidate_rules(ai_result, fused_map, gate_open=True)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(total, 0.8 * 0.8 * 1.0, places=6)  # 0.8 * conf * hit_ratio
        self.assertEqual(rows[0]["hit_ratio"], "2/2")
        rows_blocked, total_blocked = evaluate_ai_candidate_rules(ai_result, fused_map, gate_open=False)
        self.assertEqual(total_blocked, 0.0)
        self.assertEqual(rows_blocked, [])

    def test_rag_retrieval_over_ai_cache(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp()) / "ai_annotations.jsonl"
        records = [
            {"cache_key": "k1", "doc_id": "RAG-001", "status": "success",
             "analysis": {"summary": "新型储能政策推动板块关注度上升", "event": {"event_type": "policy_support"}}},
            {"cache_key": "k2", "doc_id": "RAG-002", "status": "success",
             "analysis": {"summary": "宁德时代扩产动力电池项目", "event": {"event_type": "capacity_expansion"}}},
        ]
        tmp.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
        rag.reset()
        rag.load_index(tmp)
        result = rag.retrieve("新型储能 政策", top_k=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["doc_id"], "RAG-001")
        rag.reset()

    def test_rule_explainability_output(self) -> None:
        fused = {
            "has_policy_support": {"fused": 1.0, "source": "agreed_true", "ai_confidence": 0.9, "rule_value": "true", "ai_value": "true"},
            "policy_attention_followup": {"fused": 0.6, "source": "disputed", "ai_confidence": 0.6, "rule_value": "true", "ai_value": "false"},
        }
        consensus = [
            {"name": "has_policy_support", "rationale": "政策原文明确支持"},
            {"name": "policy_attention_followup", "rationale": "AI 修正"},
        ]
        qualified = [{"rule_id": "R001", "condition": "has_policy_support AND policy_attention_followup"}]
        block = build_rule_explainability(
            "has_policy_support AND policy_attention_followup",
            "policy_signal",
            fused,
            consensus,
            "政策原文明确支持补贴政策。",
            qualified,
        )
        self.assertEqual(block["complexity"], 2)
        self.assertTrue(block["traceable"])
        self.assertEqual(block["source"], "frozen")
        self.assertEqual(block["predicates"][0]["name"], "has_policy_support")
        self.assertGreaterEqual(block["similar_to_frozen"][0]["similarity"], 0.0)

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

    def test_default_ai_settings_match_deepseek(self) -> None:
        settings = AISettings.from_environment(environ={})
        self.assertEqual(settings.mode, "off")
        self.assertEqual(settings.chat_model, "deepseek-v4-flash")
        self.assertEqual(settings.base_url, "https://api.deepseek.com")
        self.assertIn("deepseek", settings.provider)

    def test_evidence_sentence_truncates_at_sentence_boundary(self) -> None:
        long_sentence = "政策明确支持新型储能规模化发展。" + ("持续扩大的项目建设规模。") * 20
        short_cut = _truncate_at_boundary(long_sentence, 80)
        self.assertLessEqual(len(short_cut), 80)
        self.assertTrue(short_cut.endswith("。"))
        self.assertIn("持续扩大的项目建设规模", short_cut)
        exact = _truncate_at_boundary("短文本", 80)
        self.assertEqual(exact, "短文本")

    @staticmethod
    def _stock_pool() -> list[dict[str, str]]:
        import csv

        with (SAMPLE_DIR / "stock_pool.csv").open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
