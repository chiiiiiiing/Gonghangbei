"""AI candidate generation with deterministic validation and retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.ai.prompts import (
    ANALYSIS_SCHEMA,
    EVENT_TYPES,
    PREDICATE_DEFINITIONS,
    PROMPT_VERSION,
    build_analysis_messages,
)


BOOLEAN_PREDICATES = set(PREDICATE_DEFINITIONS) - {
    "event_evidence_strength",
    "event_has_short_term_price_impact",
}


class AIResearchLayer:
    def __init__(
        self,
        settings: AISettings | None = None,
        gateway: OpenAICompatibleGateway | None = None,
    ) -> None:
        self.settings = settings or AISettings.from_environment()
        self.gateway = gateway or OpenAICompatibleGateway(self.settings)

    def status(self) -> dict[str, Any]:
        return {**self.settings.public_status(), "prompt_version": PROMPT_VERSION}

    def skipped(self, reason: str) -> dict[str, Any]:
        return {
            **self.status(),
            "requested": False,
            "used": False,
            "fallback": True,
            "reason": reason,
            "embedding_retrieval": {"used": False, "matches": []},
            "result": None,
        }

    def analyze(
        self,
        document: dict[str, str],
        stock_pool: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        if not self.settings.enabled:
            result = self.skipped("模型未配置，已使用确定性规则流程")
            result["requested"] = True
            return result

        retrieval = self._retrieve_rules(document, rules)
        messages = build_analysis_messages(document, stock_pool, retrieval["matches"])
        try:
            raw, metadata = self.gateway.chat_json(messages, ANALYSIS_SCHEMA, "alphalens_research")
            validated, audit = validate_ai_output(raw, document, stock_pool)
        except AIServiceError as exc:
            return {
                **self.status(),
                "requested": True,
                "used": False,
                "fallback": True,
                "reason": str(exc),
                "embedding_retrieval": retrieval,
                "result": None,
            }
        return {
            **self.status(),
            "requested": True,
            "used": True,
            "fallback": False,
            "reason": "",
            "request_id": metadata["request_id"],
            "usage": metadata["usage"],
            "response_format": metadata["response_format"],
            "embedding_retrieval": retrieval,
            "validation": audit,
            "result": validated,
        }

    def _retrieve_rules(
        self,
        document: dict[str, str],
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        qualified = [rule for rule in rules if rule.get("status") == "qualified"]
        query = f"{document['title']}\n{document['content'][:3000]}"
        rule_texts = [
            f"{rule['rule_id']} {rule['rule_name']} {rule['condition']} {rule['target_label']}"
            for rule in qualified
        ]
        try:
            if self.settings.embedding_model:
                vectors, metadata = self.gateway.embeddings([query, *rule_texts])
            else:
                vectors = [local_text_embedding(text) for text in [query, *rule_texts]]
                metadata = {"model": "local-char-ngram-embedding-v1"}
            query_vector = vectors[0]
            ranked = sorted(
                (
                    {
                        "rule_id": rule["rule_id"],
                        "condition": rule["condition"],
                        "target_label": rule["target_label"],
                        "similarity": round(cosine_similarity(query_vector, vector), 6),
                    }
                    for rule, vector in zip(qualified, vectors[1:])
                ),
                key=lambda item: item["similarity"],
                reverse=True,
            )
            return {
                "used": True,
                "model": metadata["model"],
                "matches": ranked[:5],
                "reason": "",
            }
        except AIServiceError as exc:
            return {"used": False, "matches": [], "reason": str(exc)}


def local_text_embedding(text: str, dimensions: int = 512) -> list[float]:
    """Build a deterministic lexical embedding for offline rule retrieval."""
    normalized = re.sub(r"\s+", "", text.lower())
    features: list[str] = []
    for size in (2, 3, 4):
        features.extend(normalized[index : index + size] for index in range(max(len(normalized) - size + 1, 0)))
    features.extend(re.findall(r"[a-z0-9_]+", text.lower()))
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def validate_ai_output(
    raw: dict[str, Any],
    document: dict[str, str],
    stock_pool: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    dropped: list[str] = []
    stock_by_code = {row["stock_code"]: row for row in stock_pool}
    event_raw = raw.get("event") if isinstance(raw.get("event"), dict) else {}
    event_type = str(event_raw.get("event_type", ""))
    if event_type not in EVENT_TYPES:
        raise AIServiceError("AI 事件类型未通过枚举校验")
    evidence = str(event_raw.get("evidence_text", "")).strip()[:80]
    source_text = f"{document['title']}\n{document['content']}"
    evidence_grounded = bool(evidence and evidence in source_text)
    if not evidence_grounded:
        dropped.append("evidence_text 不是输入文本中的连续片段")
    event = {
        "event_type": event_type,
        "subject": str(event_raw.get("subject", "")).strip()[:100],
        "object": str(event_raw.get("object", "")).strip()[:100],
        "impact_path": str(event_raw.get("impact_path", "")).strip()[:240],
        "evidence_text": evidence,
        "evidence_strength": bounded_float(event_raw.get("evidence_strength"), 0.0),
        "evidence_grounded": evidence_grounded,
    }

    related_stocks: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for item in raw.get("related_stocks", []):
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if code not in stock_by_code or code in seen_codes:
            dropped.append(f"股票代码 {code or '<empty>'} 不在股票池或重复")
            continue
        seen_codes.add(code)
        stock = stock_by_code[code]
        text_grounded = stock["stock_name"] in source_text
        related_stocks.append(
            {
                "code": code,
                "name": stock["stock_name"],
                "sector": stock["industry_sector"],
                "confidence": bounded_float(item.get("confidence"), 0.0),
                "rationale": str(item.get("rationale", "")).strip()[:240],
                "text_grounded": text_grounded,
            }
        )

    predicates: list[dict[str, Any]] = []
    seen_predicates: set[str] = set()
    for item in raw.get("predicates", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip().lower()
        if name not in PREDICATE_DEFINITIONS or name in seen_predicates:
            dropped.append(f"谓词 {name or '<empty>'} 不合法或重复")
            continue
        if name in BOOLEAN_PREDICATES and value not in {"true", "false"}:
            dropped.append(f"谓词 {name} 的 boolean 值不合法")
            continue
        if name not in BOOLEAN_PREDICATES:
            try:
                score = float(value)
            except ValueError:
                dropped.append(f"谓词 {name} 的 score 值不合法")
                continue
            if not 0 <= score <= 1:
                dropped.append(f"谓词 {name} 的 score 超出 0 到 1")
                continue
            value = f"{score:.2f}"
        seen_predicates.add(name)
        predicates.append(
            {
                "name": name,
                "value": value,
                "confidence": bounded_float(item.get("confidence"), 0.0),
                "rationale": str(item.get("rationale", "")).strip()[:240],
            }
        )

    candidate_rules: list[dict[str, Any]] = []
    for item in raw.get("candidate_rules", [])[:3]:
        if not isinstance(item, dict):
            continue
        conditions = [str(value).strip() for value in item.get("conditions", [])]
        conditions = list(dict.fromkeys(value for value in conditions if value in PREDICATE_DEFINITIONS))
        if not conditions:
            dropped.append("候选规则没有合法谓词条件")
            continue
        candidate_rules.append(
            {
                "name": str(item.get("name", "候选规则")).strip()[:100],
                "conditions": conditions[:4],
                "target_label": str(item.get("target_label", "research_candidate")).strip()[:80],
                "rationale": str(item.get("rationale", "")).strip()[:240],
                "status": "pending_statistical_validation",
            }
        )

    return (
        {
            "summary": str(raw.get("summary", "")).strip()[:500],
            "event": event,
            "related_stocks": related_stocks,
            "predicates": predicates,
            "candidate_rules": candidate_rules,
        },
        {
            "accepted_stock_count": len(related_stocks),
            "grounded_stock_count": sum(row["text_grounded"] for row in related_stocks),
            "accepted_predicate_count": len(predicates),
            "accepted_candidate_rule_count": len(candidate_rules),
            "expected_predicate_count": len(PREDICATE_DEFINITIONS),
            "dropped_items": dropped,
        },
    )


def bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return round(min(max(number, 0.0), 1.0), 4)
