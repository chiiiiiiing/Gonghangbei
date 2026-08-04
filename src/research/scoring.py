"""Transparent evidence, impact and rule scoring used by batch and live research."""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
SCORING_VERSION = "transparent-score-v2.0"

SOURCE_RELIABILITY = {
    "policy": 0.95,
    "announcement": 0.95,
    "ir_qa": 0.85,
    "news": 0.60,
}
MAJOR_MEDIA = {"证券时报", "上海证券报", "中国证券报", "21 世纪经济报道", "证券日报"}


def bounded(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, 0.0), 1.0)


def source_reliability(source_type: str, source_name: str) -> float:
    if source_type == "news" and source_name in MAJOR_MEDIA:
        return 0.80
    return SOURCE_RELIABILITY.get(source_type, 0.60)


def specificity_breakdown(title: str, content: str) -> dict[str, float]:
    text = f"{title} {content.split('项目关联：', 1)[0]}"
    flags = {
        "explicit_action": any(
            word in text
            for word in [
                "印发", "公告", "签署", "中标", "投资", "建设", "处罚", "问询", "计提", "投产", "发布",
            ]
        ),
        "named_subject": bool(
            re.search(r"(?:部|委|局|政府|交易所|公司|集团|股份有限公司)", text)
        ),
        "quantified_fact": bool(
            re.search(r"\d+(?:\.\d+)?\s*(?:%|亿元|万元|万千瓦|GW|GWh|万吨|亿平方米|个)", text, re.I)
        ),
        "dated_or_project_fact": bool(
            re.search(r"20\d{2}年|20\d{2}-\d{2}-\d{2}|项目|方案|合同|规划", text)
        ),
    }
    return {name: 1.0 if value else 0.0 for name, value in flags.items()}


def information_specificity(title: str, content: str) -> float:
    values = specificity_breakdown(title, content).values()
    return sum(values) / 4.0


def deterministic_relationship_score(
    document: dict[str, str],
    entity: dict[str, Any],
) -> float:
    text = f"{document.get('title', '')}\n{document.get('content', '').split('项目关联：', 1)[0]}"
    stock_name = str(entity.get("stock_name") or entity.get("name") or "")
    evidence = str(entity.get("evidence") or entity.get("relationship_evidence") or "")
    if stock_name and stock_name in text:
        return 1.0
    if "产业主题映射" in evidence:
        return 0.80
    if "宽主题映射" in evidence:
        return 0.60
    return 0.0


def evidence_score_breakdown(
    document: dict[str, str],
    event: dict[str, Any],
    entity: dict[str, Any],
    ai_components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_text = f"{document.get('title', '')}\n{document.get('content', '')}"
    evidence_text = str(event.get("evidence_text", "")).strip()
    deterministic = {
        "source_reliability": source_reliability(
            str(document.get("source_type", "")), str(document.get("source_name", ""))
        ),
        "evidence_grounding": 1.0 if evidence_text and evidence_text in source_text else 0.0,
        "information_specificity": information_specificity(
            str(document.get("title", "")), str(document.get("content", ""))
        ),
        "business_relevance": deterministic_relationship_score(document, entity),
    }
    ai_components = ai_components or {}
    final = {"source_reliability": deterministic["source_reliability"]}
    for name in ("evidence_grounding", "information_specificity", "business_relevance"):
        if name in ai_components:
            final[name] = min(deterministic[name], bounded(ai_components[name]))
        else:
            final[name] = deterministic[name]
    weights = {
        "source_reliability": 0.30,
        "evidence_grounding": 0.25,
        "information_specificity": 0.25,
        "business_relevance": 0.20,
    }
    score = sum(final[name] * weights[name] for name in weights)
    return {
        "version": SCORING_VERSION,
        "weights": weights,
        "deterministic": {key: round(value, 4) for key, value in deterministic.items()},
        "ai": {key: round(bounded(value), 4) for key, value in ai_components.items() if key in weights},
        "final_components": {key: round(value, 4) for key, value in final.items()},
        "score": round(score, 4),
        "grounded": deterministic["evidence_grounding"] == 1.0,
    }


def beta_impact_probability(hit_count: int, sample_count: int) -> float:
    return (hit_count + 2.0) / (sample_count + 4.0)


def load_impact_priors(path: Path | None = None) -> dict[str, float]:
    target = path or SAMPLE_DIR / "event_type_impact_priors.csv"
    if not target.exists():
        return {}
    with target.open(encoding="utf-8", newline="") as handle:
        return {
            row["event_type"]: float(row["posterior_impact_probability"])
            for row in csv.DictReader(handle)
        }


def logistic_percentile(value: float, center: float, scale: float) -> float:
    safe_scale = max(abs(scale), 0.0025)
    exponent = min(max(-(value - center) / safe_scale, -40.0), 40.0)
    return 1.0 / (1.0 + math.exp(exponent))


def transparent_rule_score(
    *,
    positive_count: int,
    support_count: int,
    avg_return: float,
    global_return: float,
    global_return_scale: float,
    positive_period_count: int,
    period_count: int,
    document_count: int,
    stock_count: int,
    avg_evidence: float,
    term_count: int,
) -> dict[str, float]:
    posterior_win_rate = (positive_count + 2.0) / (support_count + 4.0)
    shrunk_return = (
        support_count / (support_count + 10.0) * avg_return
        + 10.0 / (support_count + 10.0) * global_return
    )
    return_component = logistic_percentile(shrunk_return, global_return, global_return_scale)
    stability = positive_period_count / period_count if period_count else 0.0
    coverage = 0.5 * min(document_count / 20.0, 1.0) + 0.5 * min(stock_count / 10.0, 1.0)
    complexity_penalty = 0.03 * max(term_count - 1, 0)
    score = (
        0.30 * posterior_win_rate
        + 0.25 * return_component
        + 0.20 * stability
        + 0.15 * coverage
        + 0.10 * bounded(avg_evidence)
        - complexity_penalty
    )
    return {
        "posterior_win_rate": round(posterior_win_rate, 6),
        "shrunk_return": round(shrunk_return, 6),
        "return_component": round(return_component, 6),
        "stability": round(stability, 6),
        "coverage": round(coverage, 6),
        "evidence_component": round(bounded(avg_evidence), 6),
        "complexity_penalty": round(complexity_penalty, 6),
        "score": round(min(max(score, 0.0), 1.0), 6),
    }
