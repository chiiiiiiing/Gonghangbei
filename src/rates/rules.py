"""Transparent economic transmission rules for rates text factors."""

from __future__ import annotations

from typing import Any


RULES = (
    {"rule_id": "R-MP-LIQ-01", "conditions": ("policy_stance_eases", "liquidity_supply_increases"), "yield_direction": -1, "description": "宽松取向叠加流动性投放，压低收益率压力"},
    {"rule_id": "R-MP-TIGHT-01", "conditions": ("policy_stance_tightens",), "yield_direction": 1, "description": "政策边际收紧，提高收益率上行压力"},
    {"rule_id": "R-GROWTH-INF-01", "conditions": ("growth_outlook_strengthens", "inflation_pressure_rises"), "yield_direction": 1, "description": "增长与通胀共振，提高收益率上行压力"},
    {"rule_id": "R-SUPPLY-01", "conditions": ("government_bond_supply_rises",), "yield_direction": 1, "description": "政府债供给扩张，提高期限利率上行压力"},
    {"rule_id": "R-RISK-01", "conditions": ("risk_aversion_rises",), "yield_direction": -1, "description": "避险需求上升，增加利率债需求"},
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
            result.append({**rule, "conditions": list(rule["conditions"]), "status": "activated"})
    return result


def rule_pressure(rules: list[dict[str, Any]]) -> float:
    if not rules:
        return 0.0
    return max(min(sum(float(row["yield_direction"]) for row in rules) / len(rules), 1.0), -1.0)
