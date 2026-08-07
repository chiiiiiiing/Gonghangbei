"""AI candidate generation with deterministic validation and retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from src.ai.embeddings import bge_embeddings
from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway
from src.ai.rag import retrieve as rag_retrieve
from src.ai.prompts import (
    ANALYSIS_SCHEMA,
    EVENT_TYPES,
    PREDICATE_DEFINITIONS,
    PROMPT_VERSION,
    SCORE_COMPONENTS,
    build_analysis_messages,
    build_repair_messages,
)


BOOLEAN_PREDICATES = set(PREDICATE_DEFINITIONS) - {
    "event_evidence_strength",
    "event_has_short_term_price_impact",
}
EVENT_TYPES_BY_SOURCE = {
    "policy": {"policy_support"},
    "announcement": {
        "regulatory_penalty",
        "inquiry_letter_pressure",
        "earnings_quality_anomaly",
        "product_price_increase",
        "supply_chain_disruption",
        "capacity_expansion",
    },
    "news": {
        "regulatory_penalty",
        "inquiry_letter_pressure",
        "earnings_quality_anomaly",
        "product_price_increase",
        "supply_chain_disruption",
        "capacity_expansion",
        "attention_spread",
    },
    "ir_qa": {"investor_question_pressure"},
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
        public_status = self.status()
        return {
            **public_status,
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
        messages = build_analysis_messages(
            document,
            stock_pool,
            retrieval["matches"],
            retrieval.get("historical_references", []),
        )
        try:
            raw, metadata = self.gateway.chat_json(messages, ANALYSIS_SCHEMA, "alphalens_research")
            self._validate_returned_model(metadata)
            repair_attempted = False
            first_request_id = ""
            try:
                validated, audit = validate_ai_output(
                    raw,
                    document,
                    stock_pool,
                    require_stock_level=True,
                )
            except AIServiceError as first_error:
                repair_attempted = True
                first_request_id = str(metadata.get("request_id", ""))
                repair_messages = build_repair_messages(messages, raw, str(first_error))
                raw, metadata = self.gateway.chat_json(
                    repair_messages,
                    ANALYSIS_SCHEMA,
                    "alphalens_research_repair",
                )
                self._validate_returned_model(metadata)
                try:
                    validated, audit = validate_ai_output(
                        raw,
                        document,
                        stock_pool,
                        require_stock_level=True,
                    )
                except AIServiceError as second_error:
                    raise AIServiceError(
                        f"AI 结构化结果校验连续失败：{second_error}"
                    ) from second_error
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
        public_status = self.status()
        requested_model = str(public_status.get("chat_model", ""))
        return {
            **public_status,
            "requested": True,
            "used": True,
            "fallback": False,
            "reason": "",
            "request_id": metadata["request_id"],
            "initial_request_id": first_request_id,
            "requested_model": requested_model,
            "returned_model": metadata.get("model", requested_model),
            "system_fingerprint": metadata.get("system_fingerprint", ""),
            "usage": metadata["usage"],
            "response_format": metadata["response_format"],
            "repair_attempted": repair_attempted,
            "embedding_retrieval": retrieval,
            "validation": audit,
            "result": validated,
        }

    def _validate_returned_model(self, metadata: dict[str, Any]) -> None:
        requested = str(self.status().get("chat_model", ""))
        returned = str(metadata.get("model", requested))
        if returned and requested and returned != requested:
            raise AIServiceError(
                f"请求模型为 {requested}，接口实际返回 {returned}，已拒绝该结果"
            )

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
        historical = rag_retrieve(query, top_k=3)
        try:
            if self.settings.embedding_model:
                vectors, metadata = self.gateway.embeddings([query, *rule_texts])
            else:
                try:
                    vectors, metadata = bge_embeddings([query, *rule_texts])
                except RuntimeError as exc:
                    vectors = [local_text_embedding(text) for text in [query, *rule_texts]]
                    metadata = {
                        "model": "local-char-ngram-embedding-v1",
                        "backend": "deterministic-char-ngram",
                        "fallback": True,
                        "reason": str(exc),
                    }
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
                "backend": metadata.get("backend", "api-embedding"),
                "fallback": bool(metadata.get("fallback", False)),
                "matches": ranked[:5],
                "historical_references": historical,
                "reason": metadata.get("reason", ""),
            }
        except AIServiceError as exc:
            return {
                "used": bool(historical),
                "matches": [],
                "historical_references": historical,
                "reason": str(exc),
            }


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
    *,
    require_stock_level: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dropped: list[str] = []
    stock_by_code = {row["stock_code"]: row for row in stock_pool}
    event_raw = raw.get("event") if isinstance(raw.get("event"), dict) else {}
    event_type = str(event_raw.get("event_type", "")).strip()
    if event_type not in EVENT_TYPES:
        display_value = event_type[:80] or "<empty>"
        raise AIServiceError(
            f"AI 事件类型未通过枚举校验（收到：{display_value}）"
        )
    source_type = str(document.get("source_type", ""))
    if event_type not in EVENT_TYPES_BY_SOURCE.get(source_type, set(EVENT_TYPES)):
        raise AIServiceError(
            f"AI 事件类型 {event_type} 与来源类型 {source_type or '<empty>'} 不相容"
        )
    evidence = str(event_raw.get("evidence_text", "")).strip()[:80]
    source_text = f"{document['title']}\n{document['content']}"
    evidence_grounded = bool(evidence and evidence in source_text)
    if not evidence_grounded:
        raise AIServiceError("AI 证据文本无法回溯到输入原文")
    required_event_text = {
        "subject": str(event_raw.get("subject", "")).strip(),
        "object": str(event_raw.get("object", "")).strip(),
        "impact_path": str(event_raw.get("impact_path", "")).strip(),
    }
    if any(not value for value in required_event_text.values()):
        raise AIServiceError("AI 事件缺少主体、客体或影响路径")
    event = {
        "event_type": event_type,
        "subject": str(event_raw.get("subject", "")).strip()[:100],
        "object": str(event_raw.get("object", "")).strip()[:100],
        "impact_path": str(event_raw.get("impact_path", "")).strip()[:240],
        "evidence_text": evidence,
        "evidence_strength": bounded_float(event_raw.get("evidence_strength"), 0.0),
        "evidence_grounded": evidence_grounded,
    }

    def validate_predicates(items: Any, scope: str) -> list[dict[str, Any]]:
        predicates: list[dict[str, Any]] = []
        seen_predicates: set[str] = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            value = str(item.get("value", "")).strip().lower()
            if name not in PREDICATE_DEFINITIONS or name in seen_predicates:
                dropped.append(f"{scope} 谓词 {name or '<empty>'} 不合法或重复")
                continue
            if name in BOOLEAN_PREDICATES and value not in {"true", "false"}:
                dropped.append(f"{scope} 谓词 {name} 的 boolean 值不合法")
                continue
            if name not in BOOLEAN_PREDICATES:
                try:
                    score = float(value)
                except ValueError:
                    dropped.append(f"{scope} 谓词 {name} 的 score 值不合法")
                    continue
                if not 0 <= score <= 1:
                    dropped.append(f"{scope} 谓词 {name} 的 score 超出 0 到 1")
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
        missing = sorted(set(PREDICATE_DEFINITIONS) - seen_predicates)
        if missing:
            raise AIServiceError(f"{scope} 未返回完整 19 个谓词，缺少：" + "、".join(missing))
        return predicates

    legacy_predicates: list[dict[str, Any]] = []
    if raw.get("predicates") is not None:
        legacy_predicates = validate_predicates(raw.get("predicates"), "AI 文档级")

    stock_analyses_raw = raw.get("stock_analyses", [])
    if not isinstance(stock_analyses_raw, list):
        raise AIServiceError("AI stock_analyses 必须是数组")
    if require_stock_level and not stock_analyses_raw:
        raise AIServiceError("实时模式必须返回逐股票 stock_analyses 和每只股票的完整 19 个谓词")
    if not require_stock_level and not stock_analyses_raw and raw.get("related_stocks"):
        stock_analyses_raw = [
            {
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "relationship_evidence": item.get("rationale", ""),
                "relationship_confidence": item.get("confidence", 0.0),
                "score_components": {},
                "predicates": raw.get("predicates", []),
            }
            for item in raw.get("related_stocks", [])
            if isinstance(item, dict)
        ]

    stock_analyses: list[dict[str, Any]] = []
    related_stocks: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for item in stock_analyses_raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if code not in stock_by_code or code in seen_codes:
            dropped.append(f"股票代码 {code or '<empty>'} 不在股票池或重复")
            continue
        seen_codes.add(code)
        stock = stock_by_code[code]
        relationship_evidence = str(item.get("relationship_evidence", "")).strip()[:80]
        relationship_grounded = bool(
            relationship_evidence and relationship_evidence in source_text
        )
        if not relationship_grounded:
            dropped.append(f"股票 {code} 的关系证据无法回溯到原文")
        predicates = validate_predicates(item.get("predicates"), f"股票 {code}")
        raw_components = item.get("score_components")
        if require_stock_level and (
            not isinstance(raw_components, dict)
            or set(raw_components) != set(SCORE_COMPONENTS)
        ):
            raise AIServiceError(f"股票 {code} 缺少完整的三项辅助评分")
        components = {
            name: bounded_float(raw_components.get(name), 0.0)
            for name in SCORE_COMPONENTS
        } if isinstance(raw_components, dict) else {}
        analysis = {
            "code": code,
            "name": stock["stock_name"],
            "sector": stock["industry_sector"],
            "relationship_evidence": relationship_evidence,
            "relationship_confidence": bounded_float(item.get("relationship_confidence"), 0.0),
            "relationship_grounded": relationship_grounded,
            "score_components": components,
            "predicates": predicates,
        }
        stock_analyses.append(analysis)
        related_stocks.append(
            {
                "code": code,
                "name": stock["stock_name"],
                "sector": stock["industry_sector"],
                "confidence": analysis["relationship_confidence"],
                "rationale": relationship_evidence,
                "text_grounded": relationship_grounded,
            }
        )

    if require_stock_level and not stock_analyses:
        raise AIServiceError("实时模式没有通过股票池和关系证据校验的逐股票结果")

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
                "evidence_snippet": str(item.get("evidence_snippet", "")).strip()[:120],
                "confidence": bounded_float(item.get("confidence"), 0.5),
                "status": "pending_statistical_validation",
            }
        )

    return (
        {
            "summary": str(raw.get("summary", "")).strip()[:500],
            "event": event,
            "related_stocks": related_stocks,
            "stock_analyses": stock_analyses,
            "predicates": legacy_predicates,
            "candidate_rules": candidate_rules,
        },
        {
            "accepted_stock_count": len(related_stocks),
            "grounded_stock_count": sum(row["text_grounded"] for row in related_stocks),
            "accepted_predicate_count": len(legacy_predicates) or (19 if stock_analyses else 0),
            "accepted_stock_predicate_sets": len(stock_analyses),
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
