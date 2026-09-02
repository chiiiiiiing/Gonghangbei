"""Frozen, transparent monetary-policy transmission rules."""

from __future__ import annotations

from typing import Any


RULE_VERSION = "rates-rulebook-v1.0-20260902"
RULES = (
    {"rule_id": "R-MP-LIQ-01", "conditions": ("policy_stance_eases", "liquidity_supply_increases"), "yield_direction": -1, "weight": 1.0, "description": "宽松取向叠加流动性投放，收益率下行压力增强"},
    {"rule_id": "R-MP-EASE-02", "conditions": ("policy_stance_eases",), "yield_direction": -1, "weight": 0.65, "description": "货币政策边际宽松，提高收益率下行概率"},
    {"rule_id": "R-MP-TIGHT-01", "conditions": ("policy_stance_tightens",), "yield_direction": 1, "weight": 0.8, "description": "政策边际收紧，提高收益率上行压力"},
    {"rule_id": "R-LIQ-EASE-01", "conditions": ("liquidity_supply_increases", "funding_conditions_ease"), "yield_direction": -1, "weight": 0.9, "description": "资金投放得到资金价格确认，收益率下行压力增强"},
    {"rule_id": "R-LIQ-TIGHT-01", "conditions": ("liquidity_supply_decreases", "funding_conditions_tighten"), "yield_direction": 1, "weight": 0.9, "description": "流动性回笼叠加资金趋紧，收益率上行压力增强"},
    {"rule_id": "R-GROWTH-INF-01", "conditions": ("growth_outlook_strengthens", "inflation_pressure_rises"), "yield_direction": 1, "weight": 1.0, "description": "增长与通胀共振，提高收益率上行压力"},
    {"rule_id": "R-SLOW-INF-01", "conditions": ("growth_outlook_weakens", "inflation_pressure_falls"), "yield_direction": -1, "weight": 1.0, "description": "增长与通胀同步走弱，提高收益率下行压力"},
    {"rule_id": "R-SUPPLY-01", "conditions": ("government_bond_supply_rises",), "yield_direction": 1, "weight": 0.7, "description": "政府债供给扩张，提高期限利率上行压力"},
    {"rule_id": "R-SUPPLY-LIQ-02", "conditions": ("government_bond_supply_rises", "funding_conditions_tighten"), "yield_direction": 1, "weight": 1.0, "description": "政府债集中供给遇到偏紧资金面，上行压力增强"},
    {"rule_id": "R-RISK-01", "conditions": ("risk_aversion_rises",), "yield_direction": -1, "weight": 0.65, "description": "避险需求上升，增加利率债需求"},
    {"rule_id": "R-RISK-GROWTH-02", "conditions": ("risk_aversion_rises", "growth_outlook_weakens"), "yield_direction": -1, "weight": 0.9, "description": "避险上升且增长走弱，收益率下行压力增强"},
    {"rule_id": "R-REFLATION-01", "conditions": ("risk_aversion_falls", "growth_outlook_strengthens"), "yield_direction": 1, "weight": 0.8, "description": "风险偏好与增长预期回升，提高收益率上行压力"},
)


def activate_rules(predicates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = {
        str(row["predicate_name"])
        for row in predicates
        if row.get("value") and row.get("evidence_text") and row.get("consensus") != "disputed"
    }
    result: list[dict[str, Any]] = []
    for rule in RULES:
        if all(name in active for name in rule["conditions"]):
            result.append({**rule, "conditions": list(rule["conditions"]), "status": "activated", "rule_version": RULE_VERSION})
    return result


def rule_pressure(rules: list[dict[str, Any]]) -> float:
    if not rules:
        return 0.0
    numerator = sum(float(row["yield_direction"]) * float(row.get("weight", 1.0)) for row in rules)
    denominator = sum(float(row.get("weight", 1.0)) for row in rules)
    return max(min(numerator / denominator, 1.0), -1.0)
