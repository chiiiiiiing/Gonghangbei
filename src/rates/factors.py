"""Ground policy text into evidence-linked events, predicates and factors."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from src.rates.schema import FACTOR_NAMES, PREDICATES, SOURCE_WEIGHTS


PATTERNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "policy_stance_eases": (("降准", "降息", "适度宽松", "加大逆周期调节", "降低政策利率", "支持性货币政策"),),
    "policy_stance_tightens": (("加息", "从紧", "收紧货币政策", "提高政策利率", "抑制过度融资"),),
    "liquidity_supply_increases": (
        ("逆回购", "MLF", "中期借贷便利", "流动性", "资金面"),
        ("开展", "投放", "净投放", "充裕", "合理充裕", "增加", "续作"),
    ),
    "liquidity_supply_decreases": (
        ("逆回购", "MLF", "中期借贷便利", "流动性", "资金面"),
        ("回笼", "净回笼", "缩量", "减少投放", "到期未续作"),
    ),
    "funding_conditions_tighten": (
        ("DR007", "资金利率", "资金面", "融资条件"),
        ("上行", "偏紧", "趋紧", "收敛"),
    ),
    "funding_conditions_ease": (
        ("DR007", "资金利率", "资金面", "融资条件"),
        ("下行", "宽松", "偏松", "趋松"),
    ),
    "growth_outlook_strengthens": (
        ("经济增长", "经济运行", "景气", "需求", "PMI", "社会融资规模"),
        ("回升", "改善", "扩张", "增强", "加快", "超预期", "稳中有进"),
    ),
    "growth_outlook_weakens": (
        ("经济增长", "经济运行", "景气", "需求", "PMI", "社会融资规模"),
        ("放缓", "下行", "偏弱", "收缩", "不足", "低于预期", "承压"),
    ),
    "inflation_pressure_rises": (
        ("通胀", "物价", "CPI", "PPI", "价格水平", "居民消费价格", "工业生产者出厂价格"),
        ("上涨", "回升", "压力上升", "走高", "超预期"),
    ),
    "inflation_pressure_falls": (
        ("通胀", "物价", "CPI", "PPI", "价格水平", "居民消费价格", "工业生产者出厂价格"),
        ("下降", "回落", "低位", "走低", "低于预期"),
    ),
    "government_bond_supply_rises": (
        ("国债", "地方债", "政府债券", "特别国债", "专项债"),
        (
            "增发", "拟发行", "续发行", "发行增加", "供给增加", "加快发行",
            "发行规模", "集中发行", "面值总额", "计划发行", "决定发行",
            "最大发行总额", "发行额",
        ),
    ),
    "government_bond_supply_falls": (
        ("国债", "地方债", "政府债券", "特别国债", "专项债"),
        ("发行减少", "供给下降", "发行放缓", "缩量", "暂停发行"),
    ),
    "risk_aversion_rises": (
        ("风险", "避险", "不确定性", "市场波动", "地缘政治"),
        ("上升", "加剧", "升温", "增加", "冲击"),
    ),
    "risk_aversion_falls": (
        ("风险", "避险", "不确定性", "市场波动"),
        ("下降", "缓解", "回落", "改善", "稳定"),
    ),
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).strip().lower()


def document_fingerprint(document: dict[str, str]) -> str:
    payload = normalize_text(f"{document.get('title', '')}|{document.get('content', '')}")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def independent_event_key(event: dict[str, Any]) -> str:
    """Return a semantic key so repeated notices do not inflate evidence.

    Dates and whitespace are removed from the evidence sentence, while
    quantities remain part of the key: a repeated routine OMO notice collapses
    to one event, but a materially different operation amount remains distinct.
    """
    evidence = re.sub(r"\d{4}年?\d{1,2}月?\d{1,2}日?|\d{4}-\d{1,2}-\d{1,2}|\s+", "", str(event.get("evidence_text", "")))
    payload = "|".join(
        str(event.get(name, "")).strip().lower()
        for name in ("subject", "action", "object", "policy_direction", "transmission_channel")
    ) + "|" + evidence
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_weight(source_name: str) -> float:
    return SOURCE_WEIGHTS.get(source_name.strip(), 0.75)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？!?；;])", text) if item.strip()]


def _match(sentence: str, groups: tuple[tuple[str, ...], ...]) -> list[str]:
    lowered = sentence.lower()
    matches: list[str] = []
    for group in groups:
        hit = next((word for word in group if word.lower() in lowered), "")
        if not hit:
            return []
        matches.append(hit)
    return matches


def ground_predicates(document: dict[str, str], source: str = "deterministic") -> list[dict[str, Any]]:
    text = f"{document.get('title', '')}。{document.get('content', '')}".strip("。")
    reliability = source_weight(document.get("source_name", ""))
    rows: list[dict[str, Any]] = []
    for name, definition in PREDICATES.items():
        evidence = ""
        words: list[str] = []
        for sentence in _sentences(text):
            words = _match(sentence, PATTERNS[name])
            if words:
                evidence = sentence[:300]
                break
        active = bool(evidence)
        confidence = min((0.62 + 0.08 * len(words)) * reliability, 0.95) if active else 0.65 * reliability
        rows.append({
            "predicate_name": name,
            "factor": definition.factor,
            "description": definition.description,
            "value": active,
            "yield_direction": definition.yield_direction if active else 0,
            "intensity": min(0.55 + 0.1 * len(words), 0.9) if active else 0.0,
            "confidence": round(confidence, 4),
            "source_weight": reliability,
            "evidence_text": evidence,
            "source": source,
        })
    return rows


def events_from_predicates(document: dict[str, str], predicates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in predicates:
        if not row.get("value") or not row.get("evidence_text"):
            continue
        definition = PREDICATES[str(row["predicate_name"])]
        identity = f"{document.get('doc_id', '')}|{row['predicate_name']}|{row['evidence_text']}"
        events.append({
            "event_id": "RATE-E-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
            "subject": definition.subject,
            "action": definition.action,
            "object": definition.object,
            "policy_direction": int(row["yield_direction"]),
            "intensity": float(row["intensity"]),
            "horizon": definition.horizon,
            "transmission_channel": definition.factor,
            "evidence_text": row["evidence_text"],
            "confidence": float(row["confidence"]),
        })
    return events


def factor_scores(predicates: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in predicates:
        if row.get("value") and row.get("evidence_text") and row.get("consensus") != "disputed":
            values[str(row["factor"])].append(
                float(row["yield_direction"]) * float(row["intensity"]) * float(row["confidence"])
            )
    return {
        name: round(sum(values[name]) / len(values[name]), 6) if values[name] else 0.0
        for name in FACTOR_NAMES
    }


def merge_llm_predicates(
    deterministic: list[dict[str, Any]], llm_rows: list[dict[str, Any]], source_text: str = ""
) -> list[dict[str, Any]]:
    """Fuse the two extractors while retaining disagreements for audit."""
    by_name = {str(row.get("predicate_name")): row for row in llm_rows}
    merged: list[dict[str, Any]] = []
    for base in deterministic:
        candidate = by_name.get(str(base["predicate_name"]))
        if not candidate:
            merged.append({**base, "consensus": "deterministic_only"})
            continue
        llm_value = bool(candidate.get("value"))
        evidence = str(candidate.get("evidence_text", "")).strip()
        grounded = bool(evidence) and (not source_text or evidence in source_text)
        if llm_value != bool(base["value"]):
            merged.append({
                **base, "value": False, "yield_direction": 0,
                "llm_evidence_text": evidence if grounded else "", "consensus": "disputed",
            })
        elif llm_value and grounded:
            merged.append({
                **base,
                "confidence": min(max(float(candidate.get("confidence", base["confidence"])), 0), 1),
                "evidence_text": evidence,
                "source": "deterministic+llm",
                "consensus": "agreed_true",
            })
        else:
            merged.append({
                **base, "value": False, "yield_direction": 0, "intensity": 0.0,
                "evidence_text": "", "consensus": "agreed_false",
            })
    return merged
