"""Versioned prompts and schemas for AI-assisted event research."""

from __future__ import annotations

import json
import re
from typing import Any


PROMPT_VERSION = "alphalens-research-v2.1"

EVENT_TYPES = [
    "policy_support",
    "regulatory_penalty",
    "inquiry_letter_pressure",
    "earnings_quality_anomaly",
    "product_price_increase",
    "supply_chain_disruption",
    "capacity_expansion",
    "investor_question_pressure",
    "attention_spread",
]

# 来源类型不是事件类型的替代标签。把可选集合显式传给模型，并继续由研究层
# 做枚举校验，避免公告被误标为传播事件、新闻被误标为政策事件等跨来源误判。
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

PREDICATE_DEFINITIONS = {
    "has_policy_support": "存在明确政策、补贴、规划或专项行动支持",
    "policy_directly_related_to_business": "事件直接作用于公司主营业务或产业链核心环节",
    "event_mentions_core_product": "原文提及公司的核心产品或关键业务",
    "evidence_from_authoritative_source": "证据来自政府、交易所、公司公告或权威财经媒体",
    "source_government_or_exchange": "来源是政府部门或交易所",
    "source_company_announcement": "来源是上市公司正式公告",
    "source_major_media": "来源是主流财经或综合媒体",
    "social_attention_spikes": "文本体现主题关注度短期上升",
    "policy_attention_followup": "政策发布后存在后续部署、试点或跟进信号",
    "institutional_attention_increases": "文本明确体现机构、研报或调研关注增加",
    "investor_questions_increase": "文本体现投资者提问或集中追问增加",
    "management_response_vague": "管理层回答模糊、回避或缺少实质信息",
    "announcement_contains_uncertainty": "公告包含审批、交付、价格或履约不确定性",
    "risk_or_uncertainty_disclosure": "文本明确披露风险或不确定性",
    "demand_side_policy": "政策主要作用于消费、采购或终端需求",
    "supply_side_policy": "政策主要作用于供给、技术、产能或产业建设",
    "capacity_policy_support": "政策明确支持产能、项目或基础设施建设",
    "event_evidence_strength": "事件证据强度，值为 0 到 1 的数字字符串",
    "event_has_short_term_price_impact": "类似事件潜在短期影响强度，值为 0 到 1 的数字字符串",
}

SCORE_COMPONENTS = ["evidence_grounding", "information_specificity", "business_relevance"]

PREDICATE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "enum": list(PREDICATE_DEFINITIONS)},
        "value": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "required": ["name", "value", "confidence", "rationale"],
}


ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "event": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event_type": {"type": "string", "enum": EVENT_TYPES},
                "subject": {"type": "string"},
                "object": {"type": "string"},
                "impact_path": {"type": "string"},
                "evidence_text": {"type": "string"},
                "evidence_strength": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "event_type",
                "subject",
                "object",
                "impact_path",
                "evidence_text",
                "evidence_strength",
            ],
        },
        "stock_analyses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "relationship_evidence": {"type": "string"},
                    "relationship_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "score_components": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            name: {"type": "number", "minimum": 0, "maximum": 1}
                            for name in SCORE_COMPONENTS
                        },
                        "required": SCORE_COMPONENTS,
                    },
                    "predicates": {"type": "array", "items": PREDICATE_ITEM_SCHEMA},
                },
                "required": [
                    "code",
                    "name",
                    "relationship_evidence",
                    "relationship_confidence",
                    "score_components",
                    "predicates",
                ],
            },
        },
        "candidate_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "conditions": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(PREDICATE_DEFINITIONS)},
                    },
                    "target_label": {"type": "string"},
                    "rationale": {"type": "string"},
                    "evidence_snippet": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["name", "conditions", "target_label", "rationale"],
            },
        },
    },
    "required": ["summary", "event", "stock_analyses", "candidate_rules"],
}


SYSTEM_PROMPT = """你是 AlphaLens 的金融文本研究助手。你的职责是从文本中提出可审计的结构化研究候选，不预测股价，不给出投资建议。

硬性规则：
1. 优先使用 document.fetched_content（正文链接抓取的全文）作判断依据；只有全文为空或不可用时才使用用户提供的 content 摘要。
2. 只能使用输入文本（content / fetched_content）和提供的股票池，不补充外部事实。
3. evidence_text 必须从实际使用文本（全文优先，其次摘要）逐字复制一个连续原文片段，最长 80 个字符；禁止概括、改写、替换标点或拼接多个片段。
4. 股票代码只能从提供的股票池中选择。
5. boolean 谓词的 value 只能是小写字符串 true 或 false；score 谓词使用 0 到 1 的数字字符串。
6. 候选规则只能组合给定谓词，不能包含收益方向、目标价或买卖建议。
7. 候选规则状态永远是待统计验证，不能声称已经有效。
8. 结合 document.source_diagnostics 校准置信度：仅摘要无全文时全部置信度上限 0.6；摘要+全文最高 0.95；来源越权威、文本越完整，置信度可越高；信息不足时降低置信度并明确说明，不得编造公司关联。
9. event.event_type 只能原样复制 allowed_event_types_for_source 中的一个英文值，禁止翻译成中文、自行创建类型或跨来源选类型。
10. stock_analyses 中每只股票的 predicates 必须恰好包含全部 19 个英文键，每个键恰好出现一次。
11. relationship_evidence 必须从实际使用文本逐字复制一个连续原文片段；行业政策只能选择确有业务关系的股票，不能将股票名称或外部事实编入证据。
12. 只输出一个合法 JSON 对象，不要输出 Markdown、代码块或 JSON 之外的说明文字。
"""


def output_contract() -> dict[str, Any]:
    """Return a compact concrete template for providers without JSON Schema support."""
    return {
        "summary": "string",
        "event": {
            "event_type": f"one of: {', '.join(EVENT_TYPES)}",
            "subject": "string",
            "object": "string",
            "impact_path": "string",
            "evidence_text": "原文连续片段，最长 80 字符",
            "evidence_strength": "number from 0 to 1",
        },
        "stock_analyses": [
            {
                "code": "股票池中的 6 位代码",
                "name": "股票池中的名称",
                "relationship_evidence": "原文连续片段",
                "relationship_confidence": "number from 0 to 1",
                "score_components": {
                    "evidence_grounding": "number from 0 to 1",
                    "information_specificity": "number from 0 to 1",
                    "business_relevance": "number from 0 to 1",
                },
                "predicates": [
                    {
                        "name": "predicate_definitions 中的英文键",
                        "value": "boolean 谓词为字符串 true/false，score 谓词为 0 到 1 的数字字符串",
                        "confidence": "number from 0 to 1",
                        "rationale": "string",
                    }
                ],
            }
        ],
        "candidate_rules": [
            {
                "name": "string",
                "conditions": ["predicate_definitions 中的英文键"],
                "target_label": "string",
                "rationale": "string",
                "evidence_snippet": "输入原文中的连续依据片段（可选）",
                "confidence": "number from 0 to 1（可选，缺省 0.5）",
            }
        ],
    }


def build_analysis_messages(
    document: dict[str, str],
    stock_pool: list[dict[str, str]],
    similar_rules: list[dict[str, Any]],
    historical_references: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    stock_rows = [
        {
            "code": row["stock_code"],
            "name": row["stock_name"],
            "sector": row["industry_sector"],
        }
        for row in stock_pool
    ]
    payload = {
        "prompt_version": PROMPT_VERSION,
        "document": {
            "title": document["title"],
            "content": document["content"][:8000],
            "fetched_content": (document.get("fetched_content") or "")[:12000],
            "source_diagnostics": document.get("source_diagnostics"),
            "source_type": document["source_type"],
            "source_name": document["source_name"],
            "publish_time": document["publish_time"],
            "url": document["url"],
        },
        "allowed_event_types": EVENT_TYPES,
        "allowed_event_types_for_source": sorted(
            EVENT_TYPES_BY_SOURCE.get(document["source_type"], set(EVENT_TYPES))
        ),
        "predicate_definitions": PREDICATE_DEFINITIONS,
        "stock_pool": stock_rows,
        "semantic_retrieval": similar_rules,
        "historical_ai_references": historical_references or [],
        "task": [
            "抽取一个最主要的金融事件",
            "识别与文本直接相关的股票",
            "对每只直接相关股票分别判断全部 19 个谓词和三个证据评分分项",
            "提出最多 3 条等待历史统计验证的候选组合规则",
        ],
        "output_contract": output_contract(),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def build_repair_messages(
    messages: list[dict[str, str]],
    invalid_output: dict[str, Any],
    validation_error: str,
    evidence_candidates: list[str] | None = None,
    allowed_event_types: list[str] | None = None,
) -> list[dict[str, str]]:
    """Ask the model to repair a parseable JSON result that failed domain validation."""
    repair = {
        "validation_error": validation_error,
        "repair_requirements": [
            "保留对原文的研究判断，但修正所有不符合契约的字段",
            "event.event_type 必须严格选择："
            + ", ".join(allowed_event_types or EVENT_TYPES),
            "stock_analyses 中每只股票的 predicates 必须包含全部 19 个英文键且不重复",
            "每只股票必须返回可在原文中定位的 relationship_evidence，且逐字复制，不得概括或改写",
            "evidence_text 必须逐字复制输入标题或正文中的一个连续片段，不得改写、拼接或替换标点",
            "只返回修复后的完整 JSON object",
        ],
        "evidence_candidates": evidence_candidates or [],
    }
    return [
        *messages,
        {"role": "assistant", "content": json.dumps(invalid_output, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(repair, ensure_ascii=False)},
    ]


def build_evidence_candidates(document: dict[str, str], limit: int = 12) -> list[str]:
    """Return literal candidate snippets for a strict second-pass repair.

    These snippets are only copied from the same title/body that the validator
    accepts.  They make an evidence-repair request more reliable without
    accepting paraphrases or weakening the continuous-substring requirement.
    """
    body = str(document.get("fetched_content") or document.get("content") or "")
    pieces = [str(document.get("title", "")).strip()]
    pieces.extend(re.split(r"(?<=[。！？!?])", body))
    candidates: list[str] = []
    for piece in pieces:
        literal = piece.strip()
        if not literal:
            continue
        # Truncation keeps a prefix of one literal source span, so it remains
        # a continuous substring instead of manufacturing a new sentence.
        literal = literal[:80]
        if literal not in candidates:
            candidates.append(literal)
        if len(candidates) >= limit:
            break
    return candidates
