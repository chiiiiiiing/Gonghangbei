"""Local OpenAI-compatible service used only by browser integration tests."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.ai.prompts import PREDICATE_DEFINITIONS


BOOLEAN_PREDICATES = set(PREDICATE_DEFINITIONS) - {
    "event_evidence_strength",
    "event_has_short_term_price_impact",
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path.endswith("/embeddings"):
            body = {
                "model": "fake-embedding",
                "data": [
                    {"index": index, "embedding": [1.0, 0.1 + index / 100]}
                    for index, _ in enumerate(payload["input"])
                ],
                "usage": {"total_tokens": 42},
            }
        elif self.path.endswith("/chat/completions"):
            predicates = [
                {
                    "name": name,
                    "value": "true" if name in BOOLEAN_PREDICATES else "0.90",
                    "confidence": 0.88,
                    "rationale": "模拟模型输出，用于结构化接口测试",
                }
                for name in PREDICATE_DEFINITIONS
            ]
            result = {
                "summary": "模型从政策原文中抽取储能规模化建设事件，并提出待验证规则。",
                "event": {
                    "event_type": "policy_support",
                    "subject": "国家发展改革委、国家能源局",
                    "object": "新型储能规模化建设",
                    "impact_path": "政策部署→项目建设→产业研究关注",
                    "evidence_text": "为推动新型储能高质量发展",
                    "evidence_strength": 0.94,
                },
                "related_stocks": [
                    {"code": "300750", "name": "宁德时代", "confidence": 0.84, "rationale": "储能产业链候选实体"}
                ],
                "predicates": predicates,
                "candidate_rules": [
                    {
                        "name": "储能政策跟进候选",
                        "conditions": ["has_policy_support", "policy_attention_followup"],
                        "target_label": "policy_signal",
                        "rationale": "需在历史样本和样本外区间继续检验",
                    }
                ],
            }
            body = {
                "id": "fake-browser-request",
                "model": "fake-chat",
                "choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 120},
            }
        else:
            self.send_error(404)
            return
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    port = int(os.getenv("ALPHALENS_FAKE_AI_PORT", "8799"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
