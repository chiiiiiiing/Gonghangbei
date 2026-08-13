"""Deterministic macro transmission predicates with traceable evidence."""

from __future__ import annotations

import re
from typing import Any

from src.macro.schema import MACRO_PREDICATES, period_for_date
from src.research.scoring import source_reliability


PREDICATE_PATTERNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "policy_expands_effective_demand": (
        ("补贴", "以旧换新", "购置税", "采购", "消费", "需求"),
        ("支持", "扩大", "提升", "促进", "实施", "印发"),
    ),
    "policy_accelerates_project_implementation": (
        ("项目", "工程", "基地", "建设"),
        ("开工", "落地", "审批", "核准", "提速", "加快", "推进"),
    ),
    "capacity_under_construction": (
        ("产能", "生产线", "基地", "项目"),
        ("拟建", "在建", "建设", "扩产", "投资", "规划"),
    ),
    "capacity_enters_production": (
        ("产能", "生产线", "项目", "基地"),
        ("投产", "达产", "量产", "正式生产", "投入运营"),
    ),
    "order_demand_improves": (
        ("订单", "中标", "排产", "交付", "合同"),
        ("增长", "增加", "饱满", "提升", "签署", "中标"),
    ),
    "inventory_pressure_increases": (
        ("库存", "去库", "积压"),
        ("上升", "增加", "高位", "压力", "放缓", "困难"),
    ),
    "product_price_recovers": (
        ("价格", "报价", "加工费"),
        ("上涨", "上调", "回升", "修复", "改善", "提价"),
    ),
    "raw_material_cost_pressure": (
        ("原材料", "锂", "硅料", "铜", "铝", "碳酸锂"),
        ("涨价", "上涨", "成本压力", "成本增加", "挤压"),
    ),
    "export_demand_improves": (
        ("出口", "海外", "境外"),
        ("增长", "增加", "订单", "需求", "交付", "拓展"),
    ),
    "grid_investment_accelerates": (
        ("电网", "特高压", "配电网", "输变电"),
        ("投资", "招标", "建设", "改造", "开工", "提速"),
    ),
    "financing_constraint_eases": (
        ("融资", "授信", "贷款", "债券", "资金"),
        ("获批", "增加", "支持", "到位", "发行", "降低"),
    ),
    "industry_competition_intensifies": (
        ("竞争", "价格战", "产能过剩", "供过于求", "内卷"),
        ("加剧", "激烈", "降价", "过剩", "淘汰", "承压"),
    ),
}


def _source_text(document: dict[str, str]) -> str:
    return f"{document.get('title', '')}。{document.get('content', '').split('项目关联：', 1)[0]}"


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])", text) if part.strip()]


def _match_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> tuple[bool, list[str]]:
    matches: list[str] = []
    for group in groups:
        found = next((word for word in group if word in text), "")
        if not found:
            return False, []
        matches.append(found)
    return True, matches


def _evidence(text: str, groups: tuple[tuple[str, ...], ...], limit: int = 160) -> str:
    for sentence in _sentences(text):
        matched, _ = _match_groups(sentence, groups)
        if matched:
            return sentence[:limit]
    matches = [word for group in groups for word in group if word in text]
    if not matches:
        return ""
    start = max(text.find(matches[0]) - 40, 0)
    return text[start : start + limit]


def ground_macro_predicates(document: dict[str, str]) -> list[dict[str, Any]]:
    """Return the complete 12-predicate macro schema for one document."""
    text = _source_text(document)
    period = period_for_date(document["publish_time"])
    rows: list[dict[str, Any]] = []
    reliability = source_reliability(document.get("source_type", ""), document.get("source_name", ""))
    for name, definition in MACRO_PREDICATES.items():
        groups = PREDICATE_PATTERNS[name]
        matched, words = _match_groups(text, groups)
        evidence = _evidence(text, groups) if matched else ""
        specificity = min(len(set(words)) / len(groups), 1.0) if matched else 0.0
        intensity = 0.55 + 0.15 * specificity + 0.10 * (len(evidence) >= 40) if matched else 0.0
        confidence = min(0.45 + 0.35 * reliability + 0.15 * specificity, 0.95) if matched else min(0.70, reliability)
        rows.append(
            {
                "doc_id": document["doc_id"],
                **period,
                "predicate_name": name,
                "value": "true" if matched else "false",
                "direction": str(definition.default_direction if matched else 0),
                "intensity": f"{min(intensity, 1.0):.4f}",
                "confidence": f"{confidence:.4f}",
                "expected_lag_months": str(definition.default_lag_months),
                "evidence_text": evidence,
                "source": "deterministic",
            }
        )
    return rows


def ground_all_macro_predicates(documents: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [row for document in documents for row in ground_macro_predicates(document)]
