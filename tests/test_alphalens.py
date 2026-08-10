from __future__ import annotations

import hashlib
import unittest
import csv
import json
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.server import SAMPLE_DIR, app, load_replay_cases
from src.ai import rag
from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.ai.prompts import build_analysis_messages
from src.ai.research_layer import AIResearchLayer, validate_ai_output
from src.ai.source_quality import assess_source, fetch_full_text
from src.ingestion.text_import import FIELDS, stage_manifest
from src.ingestion.discovery_ir_qa import collect_candidates
from src.pipeline.extract_events_rule_based import _truncate_at_boundary
from src.pipeline.live_analysis import (
    _apply_confidence_calibration,
    build_entity_consensus,
    build_event_consensus,
    build_predicate_consensus,
    build_rule_explainability,
    evaluate_ai_candidate_rules,
    fuse_predicate_values,
    matched_frozen_rules,
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

    def test_live_endpoint_does_not_persist_unvalidated_ai_candidate_rules(self) -> None:
        """Real-time input may display candidates, but must not alter sample research data."""
        fake_result = {"ai_analysis": {}, "stock_results": [], "qualified_rules": []}
        request = {
            "title": "实时政策验收文本",
            "content": "主管部门发布新型储能支持政策，明确项目建设要求。",
            "source_type": "policy",
            "source_name": "测试来源",
            "event_date": "2025-01-02",
            "analysis_mode": "hybrid",
            "api_key": "transient-test-key",
        }
        with patch("app.server.request_ai_layer", return_value=object()), patch(
            "app.server.analyze_new_document", return_value=fake_result
        ) as analyze_mock, patch("app.server.generate_report", return_value="report"):
            response = self.client.post("/api/analyze", json=request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(analyze_mock.call_args.kwargs["persist_ai_candidates"])

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

    def test_frozen_rule_match_reads_gated_predicates_not_fusion(self) -> None:
        """A conflict may be displayed as 0.5, but cannot trigger a frozen rule."""
        deterministic = [{"predicate_name": "has_policy_support", "value": "true", "confidence": "0.9", "rationale": "x"}]
        ai_rows = [{"name": "has_policy_support", "value": False, "confidence": 1.0, "rationale": "x"}]
        _consensus, gated = build_predicate_consensus(deterministic, ai_rows)
        fused = fuse_predicate_values({"has_policy_support": "true"}, ai_rows)
        frozen = [{"rule_id": "R-TEST", "condition": "has_policy_support"}]
        self.assertEqual(fused["has_policy_support"]["fused"], 0.5)
        self.assertEqual(matched_frozen_rules(frozen, gated, "policy_support"), [])

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


class SourceQualityAndCalibrationTests(unittest.TestCase):
    """R4：正文链接全文抓取 + 来源/完整度驱动的 AI 置信度校准。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = app.test_client()
        cls.replay_case = load_replay_cases()["storage-policy"]

    def _serve_bytes(self, body: bytes, content_type: str) -> str:
        import http.server
        import socketserver
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # noqa: D102
                pass

        server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_address[1]}/page"

    def _serve_html(self, body: str) -> str:
        html = (
            "<html><head><style>body{}</style><script>var x=1;</script></head>"
            f"<body><nav>导航</nav><h1>标题</h1><p>{body}</p><footer>版权</footer></body></html>"
        )
        return self._serve_bytes(html.encode("utf-8"), "text/html; charset=utf-8")

    @staticmethod
    def _make_pdf(text: str) -> bytes:
        content = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET\n"
        content_bytes = content.encode()
        objs = {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            4: b"<< /Length " + str(len(content_bytes)).encode() + b" >>\nstream\n" + content_bytes + b"endstream",
            5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
        out = b"%PDF-1.4\n"
        offsets: dict[int, int] = {}
        for num in range(1, 6):
            offsets[num] = len(out)
            out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
        xref_pos = len(out)
        out += b"xref\n0 6\n" + b"0000000000 65535 f \n"
        for num in range(1, 6):
            out += b"%010d 00000 n \n" % offsets[num]
        out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_pos
        return out

    def test_fetch_full_text_html_extracts_visible_text(self) -> None:
        body = "国家发展改革委印发新型储能规模化建设专项行动方案。" * 10
        result = fetch_full_text(self._serve_html(body))
        self.assertEqual(result["status"], "ok")
        self.assertIn("新型储能", result["text"])
        self.assertNotIn("导航", result["text"])
        self.assertNotIn("版权", result["text"])

    def test_fetch_full_text_failed_degrades_gracefully(self) -> None:
        result = fetch_full_text("http://127.0.0.1:1/nope", timeout=1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["text"], "")

    def test_fetch_full_text_decodes_chinese_without_charset(self) -> None:
        # 部分站点（如 gov.cn）响应头不带 charset，requests 默认按 ISO-8859-1
        # 解码会导致中文乱码；这里模拟无 charset 的 UTF-8 页面验证可靠解码。
        body = "国家发展改革委印发新型储能规模化建设专项行动方案。" * 15
        url = self._serve_bytes(body.encode("utf-8"), "text/html")
        result = fetch_full_text(url)
        self.assertEqual(result["status"], "ok")
        self.assertIn("新型储能规模化建设专项行动方案", result["text"])

    def test_fetch_full_text_pdf_extracts_text(self) -> None:
        url = self._serve_bytes(self._make_pdf("Energy Storage Policy Full Text"), "application/pdf")
        result = fetch_full_text(url)
        self.assertIn(result["status"], {"ok", "partial"})
        self.assertIn("Energy Storage", result["text"])

    def test_assess_source_classification_and_caps(self) -> None:
        gov = assess_source(
            {"url": "https://www.gov.cn/zhengce/x.htm", "content": "摘要", "source_type": "policy", "source_name": "中国政府网"},
            {"status": "ok", "fetched_chars": 500, "text": "x" * 500, "error": ""},
        )
        self.assertEqual(gov["link_type"], "government")
        self.assertEqual(gov["completeness"], "full")
        self.assertEqual(gov["confidence_cap"], 0.95)

        media = assess_source(
            {"url": "https://news.qq.com/rain/a/x", "content": "摘要", "source_type": "news"},
            {"status": "ok", "fetched_chars": 500, "text": "x" * 500, "error": ""},
        )
        self.assertEqual(media["link_type"], "media")
        self.assertEqual(media["confidence_cap"], 0.85)

        no_url = assess_source({"url": "", "content": "直接给的正文", "source_type": "news"}, None)
        self.assertEqual(no_url["completeness"], "summary_only")
        self.assertEqual(no_url["confidence_cap"], 0.7)

        failed = assess_source(
            {"url": "https://x.com/fail", "content": "摘要", "source_type": "announcement"},
            {"status": "failed", "fetched_chars": 0, "error": "timeout"},
        )
        self.assertEqual(failed["completeness"], "summary_only")
        self.assertEqual(failed["confidence_cap"], 0.6)

        cninfo = assess_source(
            {"url": "http://static.cninfo.com.cn/finalpage/x.pdf", "content": "摘要", "source_type": "announcement"},
            None,
        )
        self.assertEqual(cninfo["link_type"], "cninfo")
        self.assertEqual(cninfo["confidence_cap"], 0.6)

    def test_apply_confidence_calibration_caps_low_completeness(self) -> None:
        ai_result = {
            "stock_analyses": [
                {
                    "relationship_confidence": 0.9,
                    "predicates": [{"name": "has_policy_support", "value": "true", "confidence": 0.9}],
                }
            ],
            "candidate_rules": [{"name": "r", "conditions": ["x"], "target_label": "y", "confidence": 0.9}],
        }
        count = _apply_confidence_calibration(ai_result, {"confidence_cap": 0.6})
        self.assertEqual(count, 3)
        self.assertEqual(ai_result["stock_analyses"][0]["relationship_confidence"], 0.6)
        self.assertEqual(ai_result["stock_analyses"][0]["predicates"][0]["confidence"], 0.6)
        self.assertEqual(ai_result["candidate_rules"][0]["confidence"], 0.6)

    def test_apply_confidence_calibration_high_cap_no_change(self) -> None:
        ai_result = {
            "stock_analyses": [
                {"relationship_confidence": 0.9, "predicates": [{"name": "x", "value": "true", "confidence": 0.9}]}
            ],
            "candidate_rules": [],
        }
        count = _apply_confidence_calibration(ai_result, {"confidence_cap": 0.95})
        self.assertEqual(count, 0)
        self.assertEqual(ai_result["stock_analyses"][0]["predicates"][0]["confidence"], 0.9)

    def test_build_analysis_messages_include_source_quality(self) -> None:
        doc = {
            "title": "储能政策",
            "content": "摘要",
            "fetched_content": "全文" * 200,
            "source_diagnostics": {"confidence_cap": 0.6, "completeness": "summary_only"},
            "source_type": "policy",
            "source_name": "中国政府网",
            "publish_time": "2025-08-27",
            "url": "https://www.gov.cn/x",
        }
        messages = build_analysis_messages(
            doc, [{"stock_code": "000001", "stock_name": "平安银行", "industry_sector": "银行"}], []
        )
        payload = json.loads(messages[-1]["content"])
        self.assertIn("fetched_content", payload["document"])
        self.assertIn("source_diagnostics", payload["document"])
        self.assertEqual(payload["document"]["source_diagnostics"]["confidence_cap"], 0.6)
        self.assertEqual(payload["allowed_event_types_for_source"], ["policy_support"])

    def test_fetched_full_text_is_strict_evidence_source(self) -> None:
        """全文链接正文可作为严格连续证据，不回退为摘要外的宽松匹配。"""
        case = deepcopy(self.replay_case)
        output = case["ai_output"]
        literal_parts = [output["event"]["evidence_text"]]
        literal_parts.extend(
            row["relationship_evidence"] for row in output["stock_analyses"]
        )
        document = {
            "doc_id": "FULL-TEXT-001",
            "source_type": "policy",
            "title": "仅用于构造严格全文校验的标题",
            "content": "这是不能作为模型证据的摘要。",
            "fetched_content": "\n".join(literal_parts),
            "publish_time": "2025-08-27",
            "source_name": "中国政府网",
            "url": "https://example.gov.cn/full-text",
        }
        validated, audit = validate_ai_output(
            output,
            document,
            AlphaLensAcceptanceTests._stock_pool(),
            require_stock_level=True,
        )
        self.assertTrue(validated["event"]["evidence_grounded"])
        self.assertEqual(audit["grounded_stock_count"], len(validated["stock_analyses"]))

    def test_ai_annotation_audit_exposes_strict_failure_categories(self) -> None:
        payload = self.client.get("/api/audit").get_json()["ai_annotation_cache"]
        self.assertEqual(payload["success_count"], 163)
        self.assertEqual(payload["failed_count"], 33)
        categories = {row["category"]: row["count"] for row in payload["failure_categories"]}
        self.assertEqual(categories["事件或关系证据不是原文连续片段"], 23)

    def test_analyze_endpoint_url_only_fails_fetch_returns_400(self) -> None:
        request = {
            "title": "储能政策测试",
            "content": "",
            "source_type": "policy",
            "source_name": "中国政府网",
            "event_date": "2025-08-27",
            "source_url": "http://127.0.0.1:1/nope",
            "analysis_mode": "hybrid",
        }
        response = self.client.post("/api/analyze", json=request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("无法从链接抓取正文", response.get_json()["error"])

    def test_analyze_endpoint_url_only_fills_content_from_fetch(self) -> None:
        # 本地可抓取页面：只给链接也能走到 AI（无 Key 时如实报 ai_required，
        # 证明抓取已把全文填进正文并通过校验，而不是卡在"请提供正文内容"）。
        body = "国家发展改革委印发新型储能规模化建设专项行动方案。" * 20
        url = self._serve_html(body)
        request = {
            "title": "储能政策测试",
            "content": "",
            "source_type": "policy",
            "source_name": "中国政府网",
            "event_date": "2025-08-27",
            "source_url": url,
            "analysis_mode": "hybrid",
        }
        response = self.client.post("/api/analyze", json=request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error_code"], "ai_required")

    def test_replay_has_no_source_audit_and_no_calibration(self) -> None:
        response = self.client.get("/api/replay/storage-policy")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsNone(payload.get("source_audit"))
        self.assertEqual(payload.get("confidence_calibrated_count", 0), 0)

    def test_per_stock_factor_relevance_differs(self) -> None:
        """同一文本下的不同股票因子应通过相关性系数产生区分，而不是完全相同。"""
        payload = self.client.get("/api/replay/storage-policy").get_json()
        stocks = payload["stock_results"]
        self.assertGreaterEqual(len(stocks), 2)
        factors = [round(stock["candidate_factor"], 4) for stock in stocks]
        self.assertGreater(len(set(factors)), 1, "不同股票的候选因子不应完全相同")
        for stock in stocks:
            formula = stock["factor_formula"]
            self.assertIn("stock_relevance", formula)
            self.assertTrue(0.5 <= formula["stock_relevance"] <= 1.0)
            self.assertIn("relevance_signals", stock)

    def test_discovery_ir_candidates_recheck_listing_and_detail_dates(self) -> None:
        class FakeIRMClient:
            def resolve_org_id(self, stock_code):
                return "ORG-1"

            def list_questions(self, stock_code, org_id, page_num, start, end):
                if page_num > 1:
                    return []
                return [
                    {"indexId": "CURRENT", "stockCode": stock_code, "pubDate": 1770000000000},
                    {"indexId": "DISCOVERY", "stockCode": stock_code, "pubDate": 1735689600000},
                ]

            def question_detail(self, question_id):
                if question_id == "CURRENT":
                    raise AssertionError("当前日期记录不应请求详情")
                return {
                    "data": {
                        "stockCode": "300750",
                        "questionDate": 1735689600000,
                        "questionContent": "请问公司储能电池业务进展如何？",
                        "replyContent": "您好，公司相关业务进展请以公开披露信息为准。",
                    }
                }

        candidates, report = collect_candidates(
            [{"stock_code": "300750", "stock_name": "宁德时代"}],
            FakeIRMClient(),
            pages_per_stock=2,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["publish_time"], "2025-01-01")
        self.assertEqual(candidates[0]["review_status"], "pending_manual_review")
        self.assertIn("投资者提问原文", candidates[0]["content"])
        self.assertEqual(report["audit_counts"]["listing_out_of_window"], 1)

    def test_discovery_ir_candidates_fail_closed_on_detail_stock_or_date(self) -> None:
        class FakeIRMClient:
            def resolve_org_id(self, stock_code):
                return "ORG-1"

            def list_questions(self, stock_code, org_id, page_num, start, end):
                return [{"indexId": "BAD", "stockCode": stock_code, "pubDate": 1735689600000}] if page_num == 1 else []

            def question_detail(self, question_id):
                return {
                    "data": {
                        "stockCode": "000001",
                        "questionDate": 1735689600000,
                        "questionContent": "问题",
                        "replyContent": "回复",
                    }
                }

        candidates, report = collect_candidates(
            [{"stock_code": "300750", "stock_name": "宁德时代"}], FakeIRMClient()
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["status"], "no_verified_candidates")
        self.assertEqual(report["audit_counts"]["detail_rejected"], 1)

    def test_discovery_ir_candidates_record_network_failure_without_importing(self) -> None:
        class FailingIRMClient:
            def resolve_org_id(self, stock_code):
                raise OSError("temporary network failure")

        candidates, report = collect_candidates(
            [{"stock_code": "300750", "stock_name": "宁德时代"}], FailingIRMClient()
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["failed_stock_codes"], ["300750"])
        self.assertEqual(report["audit_counts"]["org_lookup_failed"], 1)


if __name__ == "__main__":
    unittest.main()
