"""Ground policy text into a complete, evidence-linked rates predicate schema."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from src.rates.schema import FACTOR_NAMES, PREDICATES


PATTERNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "policy_stance_eases": (("降准", "降息", "适度宽松", "加大支持", "降低利率"),),
    "policy_stance_tightens": (("加息", "从紧", "防止资金空转", "抑制过度融资", "收紧"),),
    "liquidity_supply_increases": (("逆回购", "MLF", "流动性", "资金面"), ("开展", "投放", "净投放", "充裕", "合理充裕", "增加")),
    "liquidity_supply_decreases": (("逆回购", "MLF", "流动性", "资金面"), ("回笼", "净回笼", "到期", "偏紧", "减少")),
    "growth_outlook_strengthens": (("经济增长", "经济运行", "景气", "需求", "PMI"), ("回升", "改善", "扩张", "增强", "加快")),
    "growth_outlook_weakens": (("经济增长", "经济运行", "景气", "需求", "PMI"), ("放缓", "下行", "偏弱", "收缩", "不足")),
    "inflation_pressure_rises": (("通胀", "物价", "CPI", "PPI"), ("上涨", "回升", "压力", "走高")),
    "inflation_pressure_falls": (("通胀", "物价", "CPI", "PPI"), ("下降", "回落", "低位", "走低")),
    "government_bond_supply_rises": (("国债", "地方债", "政府债券", "特别国债"), ("增发", "发行增加", "供给增加", "加快发行", "发行规模")),
    "government_bond_supply_falls": (("国债", "地方债", "政府债券", "特别国债"), ("发行减少", "供给下降", "发行放缓", "缩量")),
    "risk_aversion_rises": (("风险", "避险", "不确定性", "波动"), ("上升", "加剧", "升温", "增加")),
    "risk_aversion_falls": (("风险", "避险", "不确定性", "波动"), ("下降", "缓解", "回落", "改善")),
}


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？!?；;])", text) if item.strip()]


def _match(sentence: str, groups: tuple[tuple[str, ...], ...]) -> list[str]:
    matches: list[str] = []
    for group in groups:
        hit = next((word for word in group if word.lower() in sentence.lower()), "")
        if not hit:
            return []
        matches.append(hit)
    return matches


def ground_predicates(document: dict[str, str], source: str = "deterministic") -> list[dict[str, Any]]:
    text = f"{document.get('title', '')}。{document.get('content', '')}".strip("。")
    rows: list[dict[str, Any]] = []
    for name, definition in PREDICATES.items():
        evidence = ""
        words: list[str] = []
        for sentence in _sentences(text):
            words = _match(sentence, PATTERNS[name])
            if words:
                evidence = sentence[:220]
                break
        active = bool(evidence)
        rows.append({
            "predicate_name": name,
            "factor": definition.factor,
            "value": active,
            "yield_direction": definition.yield_direction if active else 0,
            "intensity": min(0.55 + 0.1 * len(words), 0.9) if active else 0.0,
            "confidence": min(0.62 + 0.08 * len(words), 0.9) if active else 0.65,
            "evidence_text": evidence,
            "source": source,
        })
    return rows


def factor_scores(predicates: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in predicates:
        if row.get("value") and row.get("evidence_text"):
            values[str(row["factor"])].append(
                float(row["yield_direction"]) * float(row["intensity"]) * float(row["confidence"])
            )
    return {
        name: round(sum(values[name]) / max(len(values[name]), 1), 4) if values[name] else 0.0
        for name in FACTOR_NAMES
    }


def merge_llm_predicates(
    deterministic: list[dict[str, Any]], llm_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Use only LLM claims grounded by an exact substring of the source text.

    Disagreements are preserved as disputed and do not enter rule scores.
    """
    by_name = {str(row.get("predicate_name")): row for row in llm_rows}
    merged: list[dict[str, Any]] = []
    for base in deterministic:
        candidate = by_name.get(str(base["predicate_name"]))
        if not candidate:
            merged.append({**base, "consensus": "deterministic_only"})
            continue
        llm_value = bool(candidate.get("value"))
        evidence = str(candidate.get("evidence_text", "")).strip()
        grounded = bool(evidence)
        if llm_value != bool(base["value"]):
            merged.append({**base, "value": False, "yield_direction": 0, "consensus": "disputed"})
        elif llm_value and grounded:
            merged.append({
                **base,
                "confidence": min(max(float(candidate.get("confidence", base["confidence"])), 0), 1),
                "evidence_text": evidence,
                "source": "deterministic+llm",
                "consensus": "agreed_true",
            })
        else:
            merged.append({**base, "consensus": "agreed_false"})
    return merged
