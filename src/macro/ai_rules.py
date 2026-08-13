"""LLM-generated macro transmission rules with strict evidence validation."""

from __future__ import annotations

from typing import Any

from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.macro.schema import MACRO_PREDICATES, period_for_date
from src.research.scoring import source_reliability


MACRO_PROMPT_VERSION = "alphalens-macro-rules-v1.2"

MACRO_PREDICATE_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value": {"type": "string", "enum": ["true", "false"]},
        "direction": {"type": "integer", "enum": [-1, 0, 1]},
        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "expected_lag_months": {"type": "integer", "minimum": 0, "maximum": 12},
        "evidence_text": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": [
        "value",
        "direction",
        "intensity",
        "confidence",
        "expected_lag_months",
        "evidence_text",
        "rationale",
    ],
}

MACRO_AI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "predicates": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                name: MACRO_PREDICATE_VALUE_SCHEMA for name in MACRO_PREDICATES
            },
            "required": list(MACRO_PREDICATES),
        },
        "candidate_rules": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "conditions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {"type": "string", "enum": list(MACRO_PREDICATES)},
                    },
                    "direction": {"type": "integer", "enum": [-1, 1]},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "expected_lag_months": {"type": "integer", "minimum": 0, "maximum": 12},
                    "evidence_text": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": [
                    "conditions",
                    "direction",
                    "intensity",
                    "confidence",
                    "expected_lag_months",
                    "evidence_text",
                    "rationale",
                ],
            },
        },
    },
    "required": ["summary", "predicates", "candidate_rules"],
}

SYSTEM_PROMPT = """你是 AlphaLens 的新能源产业景气研究助手。你的任务是从输入文本中识别对“电气机械和器材制造业增加值同比增速”的可审计传导证据，不预测股票价格，不提供投资建议。

硬性规则：
1. 只能使用 document.title 和 document.content，不能使用外部事实、官方目标真实值、回测误差或策略收益。
2. predicates 必须是 JSON object，恰好以全部 12 个 macro_predicate_definitions 英文键为键；每个值对象必须包含 value、direction、intensity、confidence、expected_lag_months、evidence_text、rationale。
3. value=false 时 direction 必须为 0、intensity 必须为 0、evidence_text 必须为空。
4. value=true 时 direction 只能为 -1 或 1，evidence_text 必须是输入标题或正文中的连续原文片段。
5. candidate_rules 最多 3 条，只能组合合法宏观谓词；证据必须可逐字回溯。
6. expected_lag_months 表示从文本公开到可能影响工业生产的月数，范围 0—12。
7. 信息不足时输出 false，不得为了凑规则编造证据。
8. 只输出一个合法 JSON object，不要输出 Markdown 或额外说明。
"""


def build_macro_messages(document: dict[str, str]) -> list[dict[str, str]]:
    definitions = {
        name: {
            "description": definition.description,
            "default_direction": definition.default_direction,
            "default_lag_months": definition.default_lag_months,
        }
        for name, definition in MACRO_PREDICATES.items()
    }
    user_payload = {
        "prompt_version": MACRO_PROMPT_VERSION,
        "target": "国家统计局电气机械和器材制造业增加值同比增速 Nowcast",
        "document": {
            "doc_id": document["doc_id"],
            "title": document.get("title", ""),
            "content": document.get("content", "")[:12000],
            "publish_time": document["publish_time"],
            "source_type": document.get("source_type", ""),
            "source_name": document.get("source_name", ""),
        },
        "macro_predicate_definitions": definitions,
        "output_contract": {
            "summary": "string",
            "predicates": {
                name: {
                    "value": "true or false",
                    "direction": "-1, 0 or 1",
                    "intensity": "0 to 1",
                    "confidence": "0 to 1",
                    "expected_lag_months": "0 to 12",
                    "evidence_text": "continuous source snippet or empty when false",
                    "rationale": "short explanation",
                }
                for name in MACRO_PREDICATES
            },
            "candidate_rules": "0 to 3 evidence-grounded rule objects",
        },
    }
    import json

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def build_macro_repair_messages(
    messages: list[dict[str, str]],
    raw: dict[str, Any],
    validation_error: str,
) -> list[dict[str, str]]:
    import json

    repair = {
        "validation_error": validation_error,
        "invalid_output": raw,
        "repair_requirements": [
            "顶层只能包含 summary、predicates、candidate_rules",
            "predicates 必须是以 12 个合法谓词英文名为键的 JSON object",
            "candidate_rules 必须是数组且最多 3 条",
            "所有 true 谓词和候选规则证据必须是输入原文连续片段",
            "只输出修复后的一个 JSON object",
        ],
    }
    return [
        *messages,
        {
            "role": "user",
            "content": "上一次结构化输出未通过校验。请严格按要求完整重做："
            + json.dumps(repair, ensure_ascii=False),
        },
    ]


def _bounded(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def validate_macro_ai_output(raw: dict[str, Any], document: dict[str, str]) -> dict[str, Any]:
    source_text = f"{document.get('title', '')}\n{document.get('content', '')}"
    confidence_cap = source_reliability(
        document.get("source_type", ""), document.get("source_name", "")
    )
    raw_predicates = raw.get("predicates")
    if isinstance(raw_predicates, dict):
        if set(raw_predicates) != set(MACRO_PREDICATES):
            raise AIServiceError("宏观 AI predicates 对象必须恰好包含完整 12 个谓词键")
        normalized_predicates = [
            {"name": name, **item}
            for name, item in raw_predicates.items()
            if isinstance(item, dict)
        ]
        if len(normalized_predicates) != len(MACRO_PREDICATES):
            raise AIServiceError("宏观 AI predicates 对象的值必须全部是对象")
    elif isinstance(raw_predicates, list):
        normalized_predicates = raw_predicates
    else:
        raise AIServiceError("宏观 AI predicates 必须是对象或兼容数组")
    seen: set[str] = set()
    predicates: list[dict[str, Any]] = []
    period = period_for_date(document["publish_time"])
    for item in normalized_predicates:
        if not isinstance(item, dict):
            raise AIServiceError("宏观 AI 谓词项必须是对象")
        name = str(item.get("name", ""))
        if name not in MACRO_PREDICATES or name in seen:
            raise AIServiceError(f"宏观 AI 谓词非法或重复：{name or '<empty>'}")
        seen.add(name)
        value = str(item.get("value", "")).lower()
        if value not in {"true", "false"}:
            raise AIServiceError(f"宏观 AI 谓词 {name} 的 value 非法")
        evidence = str(item.get("evidence_text", "")).strip()[:240]
        try:
            direction = int(item.get("direction", 0))
            lag = int(item.get("expected_lag_months", 0))
        except (TypeError, ValueError) as exc:
            raise AIServiceError(f"宏观 AI 谓词 {name} 的方向或时滞非法") from exc
        intensity = _bounded(item.get("intensity"))
        if "confidence" not in item or "rationale" not in item:
            raise AIServiceError(f"宏观 AI 谓词 {name} 缺少 confidence 或 rationale")
        if value == "true":
            if direction not in {-1, 1} or not evidence or evidence not in source_text:
                raise AIServiceError(f"宏观 AI 谓词 {name} 缺少可回溯证据或合法方向")
        elif direction != 0 or intensity != 0.0 or evidence:
            raise AIServiceError(f"宏观 AI 谓词 {name} 为 false 时必须零方向、零强度且无证据")
        if not 0 <= lag <= 12:
            raise AIServiceError(f"宏观 AI 谓词 {name} 的时滞超出 0—12")
        predicates.append(
            {
                "doc_id": document["doc_id"],
                **period,
                "predicate_name": name,
                "value": value,
                "direction": str(direction),
                "intensity": f"{intensity:.4f}",
                "confidence": f"{min(_bounded(item.get('confidence')), confidence_cap):.4f}",
                "expected_lag_months": str(lag),
                "evidence_text": evidence,
                "source": "ai",
                "rationale": str(item.get("rationale", "")).strip()[:300],
            }
        )
    missing = sorted(set(MACRO_PREDICATES) - seen)
    if missing:
        raise AIServiceError("宏观 AI 未返回完整 12 个谓词，缺少：" + "、".join(missing))

    candidate_rules: list[dict[str, Any]] = []
    dropped_candidate_rules: list[str] = []
    for item in raw.get("candidate_rules", [])[:3]:
        if not isinstance(item, dict):
            dropped_candidate_rules.append("候选规则不是对象")
            continue
        conditions = list(dict.fromkeys(str(value) for value in item.get("conditions", [])))
        if not 1 <= len(conditions) <= 4 or any(value not in MACRO_PREDICATES for value in conditions):
            dropped_candidate_rules.append("候选规则缺少合法宏观谓词条件")
            continue
        evidence = str(item.get("evidence_text", "")).strip()[:240]
        if not evidence or evidence not in source_text:
            dropped_candidate_rules.append("候选规则证据无法回溯原文")
            continue
        try:
            direction = int(item.get("direction", 0))
            lag = int(item.get("expected_lag_months", 0))
        except (TypeError, ValueError):
            dropped_candidate_rules.append("候选规则方向或时滞不是整数")
            continue
        if direction not in {-1, 1} or not 0 <= lag <= 12:
            dropped_candidate_rules.append("候选规则方向或时滞非法")
            continue
        candidate_rules.append(
            {
                "conditions": conditions,
                "condition_signature": " AND ".join(sorted(conditions)),
                "direction": direction,
                "intensity": _bounded(item.get("intensity")),
                "confidence": min(_bounded(item.get("confidence")), confidence_cap),
                "expected_lag_months": lag,
                "lag_bucket": "0" if lag == 0 else "1_3" if lag <= 3 else "4_6" if lag <= 6 else "7_12",
                "evidence_text": evidence,
                "rationale": str(item.get("rationale", "")).strip()[:300],
                "status": "pending_macro_validation",
            }
        )
    if not candidate_rules:
        active = [row for row in predicates if row["value"] == "true"]
        active.sort(
            key=lambda row: float(row["intensity"]) * float(row["confidence"]),
            reverse=True,
        )
        # The model's accepted predicate judgments remain the source of this
        # fallback. We only canonicalize them into a bounded stable signature.
        for row in active[:3]:
            lag = int(row["expected_lag_months"])
            candidate_rules.append(
                {
                    "conditions": [row["predicate_name"]],
                    "condition_signature": row["predicate_name"],
                    "direction": int(row["direction"]),
                    "intensity": float(row["intensity"]),
                    "confidence": float(row["confidence"]),
                    "expected_lag_months": lag,
                    "lag_bucket": "0" if lag == 0 else "1_3" if lag <= 3 else "4_6" if lag <= 6 else "7_12",
                    "evidence_text": row["evidence_text"],
                    "rationale": row.get("rationale", ""),
                    "status": "pending_macro_validation",
                    "proposal_source": "canonicalized_ai_predicate",
                }
            )
    return {
        "summary": str(raw.get("summary", "")).strip()[:500],
        "predicates": predicates,
        "candidate_rules": candidate_rules,
        "validation": {
            "dropped_candidate_rule_count": len(dropped_candidate_rules),
            "dropped_candidate_rules": dropped_candidate_rules,
            "candidate_fallback_used": bool(dropped_candidate_rules) and bool(candidate_rules),
        },
    }


class MacroAIRuleLayer:
    def __init__(
        self,
        settings: AISettings | None = None,
        gateway: OpenAICompatibleGateway | None = None,
    ) -> None:
        self.settings = settings or AISettings.from_environment()
        self.gateway = gateway or OpenAICompatibleGateway(self.settings)

    def status(self) -> dict[str, Any]:
        return {**self.settings.public_status(), "prompt_version": MACRO_PROMPT_VERSION}

    def analyze(self, document: dict[str, str]) -> dict[str, Any]:
        if not self.settings.enabled:
            return {**self.status(), "used": False, "reason": "模型未配置", "result": None}
        messages = build_macro_messages(document)
        repair_attempted = False
        initial_request_id = ""
        try:
            raw, metadata = self.gateway.chat_json(
                messages,
                MACRO_AI_SCHEMA,
                "alphalens_macro_rules",
            )
            returned = str(metadata.get("model", self.settings.chat_model))
            if returned != self.settings.chat_model:
                raise AIServiceError(
                    f"宏观规则请求模型为 {self.settings.chat_model}，实际返回 {returned}，已拒绝"
                )
            try:
                result = validate_macro_ai_output(raw, document)
            except AIServiceError as first_error:
                repair_attempted = True
                initial_request_id = str(metadata.get("request_id", ""))
                raw, metadata = self.gateway.chat_json(
                    build_macro_repair_messages(messages, raw, str(first_error)),
                    MACRO_AI_SCHEMA,
                    "alphalens_macro_rules_repair",
                )
                returned = str(metadata.get("model", self.settings.chat_model))
                if returned != self.settings.chat_model:
                    raise AIServiceError(
                        f"宏观规则修复请求模型为 {self.settings.chat_model}，实际返回 {returned}，已拒绝"
                    )
                result = validate_macro_ai_output(raw, document)
        except (AIServiceError, TypeError, ValueError) as exc:
            return {**self.status(), "used": False, "reason": str(exc), "result": None}
        return {
            **self.status(),
            "used": True,
            "reason": "",
            "request_id": metadata.get("request_id", ""),
            "initial_request_id": initial_request_id,
            "returned_model": metadata.get("model", self.settings.chat_model),
            "usage": metadata.get("usage", {}),
            "repair_attempted": repair_attempted,
            "result": result,
        }


def fuse_macro_predicates(
    deterministic_rows: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Conservatively fuse complete deterministic and AI macro predicate sets."""
    deterministic = {str(row["predicate_name"]): row for row in deterministic_rows}
    ai = {str(row["predicate_name"]): row for row in ai_rows}
    fused: list[dict[str, Any]] = []
    for name in MACRO_PREDICATES:
        rule = deterministic[name]
        model = ai.get(name)
        if model is None:
            fused.append({**rule, "source": "deterministic"})
            continue
        rule_score = float(rule["direction"]) * float(rule["intensity"])
        ai_score = float(model["direction"]) * float(model["intensity"])
        confidence = min(float(model["confidence"]), 0.5)
        score = rule_score + (ai_score - rule_score) * confidence
        active = abs(score) > 1e-12
        fused.append(
            {
                **rule,
                "value": "true" if active else "false",
                "direction": str(1 if score > 0 else -1 if score < 0 else 0),
                "intensity": f"{abs(score):.4f}",
                "confidence": f"{max(float(rule['confidence']), float(model['confidence'])):.4f}",
                "expected_lag_months": model["expected_lag_months"] if model["value"] == "true" else rule["expected_lag_months"],
                "evidence_text": model["evidence_text"] if model["value"] == "true" else rule["evidence_text"],
                "source": "fused",
            }
        )
    return fused
