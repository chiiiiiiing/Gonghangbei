"""End-to-end tests for the OpenAI-compatible AI research layer."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.ai.gateway import AISettings, OpenAICompatibleGateway
from src.ai.research_layer import AIResearchLayer


MODEL_OUTPUT = {
    "summary": "政策文本提出储能规模化建设安排。",
    "event": {
        "event_type": "policy_support",
        "subject": "国家能源局",
        "object": "新型储能",
        "impact_path": "政策部署→项目建设→产业关注",
        "evidence_text": "国家能源局发布新型储能行动方案",
        "evidence_strength": 0.93,
    },
    "related_stocks": [
        {"code": "300750", "name": "模型错写名称", "confidence": 0.86, "rationale": "储能业务相关"},
        {"code": "999999", "name": "不存在", "confidence": 0.99, "rationale": "不合法股票"},
    ],
    "predicates": [
        {"name": "has_policy_support", "value": "true", "confidence": 0.96, "rationale": "明确政策部署"},
        {"name": "event_evidence_strength", "value": "0.93", "confidence": 0.91, "rationale": "政府来源且证据直接"},
    ],
    "candidate_rules": [
        {
            "name": "储能政策直接支持候选",
            "conditions": ["has_policy_support", "policy_directly_related_to_business"],
            "target_label": "policy_signal",
            "rationale": "等待历史样本检验",
        }
    ],
}


class FakeModelHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append((self.path, payload))
        if self.path.endswith("/embeddings"):
            vectors = []
            for index, _ in enumerate(payload["input"]):
                vector = [1.0, 0.0] if index in {0, 1} else [0.0, 1.0]
                vectors.append({"index": index, "embedding": vector})
            response = {"model": "fake-embedding", "data": vectors, "usage": {"total_tokens": 10}}
        elif self.path.endswith("/chat/completions"):
            response = {
                "id": "fake-request-001",
                "model": "fake-chat",
                "choices": [{"message": {"content": json.dumps(MODEL_OUTPUT, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 80},
            }
        else:
            self.send_error(404)
            return
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class AIResearchLayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        FakeModelHandler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModelHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_chat_embeddings_validation_and_candidate_rules(self) -> None:
        settings = AISettings(
            mode="local",
            base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
            api_key="",
            chat_model="fake-chat",
            embedding_model="fake-embedding",
            timeout_seconds=5,
            json_mode="schema",
        )
        layer = AIResearchLayer(settings, OpenAICompatibleGateway(settings))
        document = {
            "title": "新型储能行动方案",
            "content": "国家能源局发布新型储能行动方案，推动项目建设。",
            "source_type": "policy",
            "source_name": "国家能源局",
            "publish_time": "2026-01-01",
            "url": "https://example.com/detail",
        }
        stock_pool = [
            {"stock_code": "300750", "stock_name": "宁德时代", "industry_sector": "锂电", "market_cap": "100"}
        ]
        rules = [
            {
                "rule_id": "R001",
                "rule_name": "政策规则",
                "condition": "has_policy_support",
                "target_label": "policy_signal",
                "status": "qualified",
            }
        ]

        result = layer.analyze(document, stock_pool, rules)

        self.assertTrue(result["used"])
        self.assertEqual(result["response_format"], "json_schema")
        self.assertTrue(result["embedding_retrieval"]["used"])
        self.assertEqual(result["embedding_retrieval"]["matches"][0]["rule_id"], "R001")
        self.assertEqual(result["result"]["related_stocks"][0]["name"], "宁德时代")
        self.assertFalse(result["result"]["related_stocks"][0]["text_grounded"])
        self.assertEqual(result["result"]["candidate_rules"][0]["status"], "pending_statistical_validation")
        self.assertTrue(result["result"]["event"]["evidence_grounded"])
        self.assertTrue(any("999999" in item for item in result["validation"]["dropped_items"]))
        paths = [path for path, _ in FakeModelHandler.requests]
        self.assertIn("/v1/embeddings", paths)
        self.assertIn("/v1/chat/completions", paths)
        chat_payload = next(payload for path, payload in FakeModelHandler.requests if path.endswith("chat/completions"))
        self.assertEqual(chat_payload["response_format"]["type"], "json_schema")

    def test_unconfigured_layer_returns_safe_fallback(self) -> None:
        settings = AISettings(
            mode="off",
            base_url="https://api.openai.com/v1",
            api_key="",
            chat_model="gpt-5-mini",
            embedding_model="text-embedding-3-small",
            timeout_seconds=5,
            json_mode="schema",
        )
        result = AIResearchLayer(settings).analyze({}, [], [])
        self.assertFalse(result["used"])
        self.assertTrue(result["fallback"])
        self.assertIn("模型未配置", result["reason"])


if __name__ == "__main__":
    unittest.main()
