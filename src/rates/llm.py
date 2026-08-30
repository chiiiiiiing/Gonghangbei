"""Optional LLM extraction for rates text, with strict evidence validation."""

from __future__ import annotations

from typing import Any

from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.rates.schema import PREDICATES


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "events", "predicates"],
    "properties": {
        "summary": {"type": "string"},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["subject", "action", "object", "stance", "intensity", "horizon", "evidence_text", "confidence"],
                "properties": {
                    "subject": {"type": "string"}, "action": {"type": "string"}, "object": {"type": "string"},
                    "stance": {"type": "integer", "minimum": -1, "maximum": 1},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "horizon": {"type": "string"}, "evidence_text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "predicates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["predicate_name", "value", "evidence_text", "confidence"],
                "properties": {
                    "predicate_name": {"type": "string", "enum": list(PREDICATES)},
                    "value": {"type": "boolean"}, "evidence_text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}


def extract_with_llm(document: dict[str, str], api_key: str = "") -> dict[str, Any]:
    settings = AISettings.from_environment()
    if api_key:
        settings = AISettings(
            mode="api", base_url=settings.base_url, api_key=api_key,
            chat_model=settings.chat_model, embedding_model=settings.embedding_model,
            timeout_seconds=settings.timeout_seconds, json_mode=settings.json_mode,
        )
    if not settings.enabled:
        return {"used": False, "reason": "未配置模型API，使用确定性谓词作为透明降级", "events": [], "predicates": [], "metadata": {}}
    source_text = f"标题：{document['title']}\n正文：{document['content']}"
    prompt = (
        "你是银行利率债研究助手。只根据给定原文抽取结构化事件和固定谓词。"
        "evidence_text 必须逐字出现在原文；无法确认时 value=false 且证据留空。"
        "不得直接给交易指令。\n\n" + source_text
    )
    raw, metadata = OpenAICompatibleGateway(settings).chat_json(
        [{"role": "system", "content": "输出严格JSON，不补写原文没有的信息。"}, {"role": "user", "content": prompt}],
        SCHEMA,
        "rates_text_factors",
    )
    full_text = f"{document['title']}。{document['content']}"
    events = [row for row in raw.get("events", []) if str(row.get("evidence_text", "")) in full_text]
    predicates: list[dict[str, Any]] = []
    for row in raw.get("predicates", []):
        name = str(row.get("predicate_name", ""))
        evidence = str(row.get("evidence_text", ""))
        if name not in PREDICATES:
            continue
        value = bool(row.get("value"))
        if value and (not evidence or evidence not in full_text):
            continue
        predicates.append({**row, "evidence_text": evidence})
    return {"used": True, "reason": "", "summary": str(raw.get("summary", "")), "events": events, "predicates": predicates, "metadata": metadata}
