"""Optional LLM extraction for rates text, with strict evidence validation."""

from __future__ import annotations

from typing import Any

from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.rates.factors import ground_predicates
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
                "required": ["subject", "action", "object", "policy_direction", "intensity", "horizon", "transmission_channel", "evidence_text", "confidence"],
                "properties": {
                    "subject": {"type": "string"}, "action": {"type": "string"}, "object": {"type": "string"},
                    "policy_direction": {"type": "integer", "minimum": -1, "maximum": 1},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "horizon": {"type": "string"},
                    "transmission_channel": {"type": "string", "enum": ["monetary_policy", "liquidity", "growth", "inflation", "bond_supply", "risk_appetite"]},
                    "evidence_text": {"type": "string"},
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
    predicate_names = ", ".join(PREDICATES)
    prompt = (
        "你是银行利率债研究助手。只根据给定原文抽取结构化事件和固定谓词。"
        "事件必须包含主体、动作、作用对象、政策方向、强度、影响期限和传导渠道。"
        "evidence_text 必须逐字出现在原文；无法确认时 value=false 且证据留空。"
        "不得直接给交易指令。必须只返回以下字段：summary、events、predicates。"
        "events每项必须严格使用subject、action、object、policy_direction、intensity、horizon、"
        "transmission_channel、evidence_text、confidence，不得使用predicate、direction、strength、duration或channel等别名。"
        "predicates必须恰好返回下列每个固定谓词各一次，value为布尔值："
        f"{predicate_names}。不得遗漏，不得增加其他谓词。\n\n" + source_text
    )
    raw, metadata = OpenAICompatibleGateway(settings).chat_json(
        [{"role": "system", "content": "输出严格JSON，不补写原文没有的信息。"}, {"role": "user", "content": prompt}],
        SCHEMA,
        "rates_text_factors",
    )
    full_text = f"{document['title']}。{document['content']}"
    direction_map = {"宽松": -1, "下行": -1, "利多债券": -1, "中性": 0, "震荡": 0, "收紧": 1, "上行": 1, "利空债券": 1}
    intensity_map = {"低": 0.35, "较低": 0.35, "弱": 0.35, "中": 0.6, "中等": 0.6, "高": 0.85, "较高": 0.85, "强": 0.85}
    channel_aliases = {
        "货币": "monetary_policy", "流动性": "liquidity", "资金": "liquidity",
        "增长": "growth", "经济": "growth", "通胀": "inflation", "物价": "inflation",
        "债券供给": "bond_supply", "发行": "bond_supply", "风险": "risk_appetite", "避险": "risk_appetite",
    }
    channels = {"monetary_policy", "liquidity", "growth", "inflation", "bond_supply", "risk_appetite"}
    events = []
    for row in raw.get("events", []):
        evidence = str(row.get("evidence_text", "")).strip()
        if not evidence or evidence not in full_text:
            continue
        try:
            raw_direction = row.get("policy_direction", row.get("stance", row.get("direction", 0)))
            direction = direction_map.get(str(raw_direction), int(raw_direction) if str(raw_direction) in {"-1", "0", "1"} else 0)
            raw_intensity = row.get("intensity", row.get("strength", 0.6))
            intensity = intensity_map.get(str(raw_intensity), float(raw_intensity) if not isinstance(raw_intensity, str) else 0.6)
            raw_confidence = row.get("confidence", 0.7)
            confidence = 0.7 if isinstance(raw_confidence, bool) else float(raw_confidence)
        except (TypeError, ValueError):
            continue
        raw_channel = str(row.get("transmission_channel", row.get("channel", "")))
        channel = raw_channel if raw_channel in channels else next(
            (value for keyword, value in channel_aliases.items() if keyword in raw_channel), ""
        )
        action = str(row.get("action", row.get("predicate", ""))).strip()
        object_name = str(row.get("object", "")).strip()
        horizon = str(row.get("horizon", row.get("duration", ""))).strip()
        if not action or not object_name or not horizon or direction not in {-1, 0, 1}:
            continue
        if not 0 <= intensity <= 1 or not 0 <= confidence <= 1 or channel not in channels:
            continue
        events.append({
            "subject": str(row.get("subject", "")).strip(), "action": action, "object": object_name,
            "policy_direction": direction, "intensity": intensity, "horizon": horizon,
            "transmission_channel": channel, "evidence_text": evidence, "confidence": confidence,
        })
    predicates: list[dict[str, Any]] = []
    raw_predicates = raw.get("predicates", [])
    if isinstance(raw_predicates, dict):
        grounded = {row["predicate_name"]: row for row in ground_predicates(document)}
        raw_predicates = [
            {
                "predicate_name": name, "value": bool(raw_predicates.get(name)),
                "evidence_text": grounded[name]["evidence_text"] if raw_predicates.get(name) and grounded[name]["value"] else "",
                "confidence": 0.75,
            }
            for name in PREDICATES
        ]
    for row in raw_predicates:
        name = str(row.get("predicate_name", ""))
        evidence = str(row.get("evidence_text", ""))
        if name not in PREDICATES:
            continue
        value = bool(row.get("value"))
        predicates.append({
            **row,
            "evidence_text": evidence if evidence and evidence in full_text else "",
            "grounded": not value or bool(evidence and evidence in full_text),
        })
    complete = {str(row.get("predicate_name")) for row in raw_predicates} == set(PREDICATES)
    usable = bool(events or any(row.get("value") for row in predicates)) and complete
    return {
        "used": usable,
        "reason": "" if usable else "模型输出未通过固定字段、完整谓词或原文证据校验，已使用确定性降级",
        "summary": str(raw.get("summary", "")), "events": events,
        "predicates": predicates if complete else [], "metadata": metadata,
    }
