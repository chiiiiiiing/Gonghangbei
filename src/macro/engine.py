"""Deterministic macro layer built after the existing candidate-factor pipeline.

The module deliberately does not replace the fixed 19-predicate AI/rule workflow.
It consumes that workflow's audited outputs, aggregates them by target period, and
then performs release-date-aware nowcasting and a separate strategy experiment.
"""

from __future__ import annotations

import csv
import json
import math
import re
import random
import statistics
import subprocess
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.backtest.demo_engine import PREDICATE_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
TARGET_NAME = "电气机械和器材制造业增加值同比增速"
RISK_CODE = "516160"
DEFENSIVE_CODE = "511010"
PROXY_CODE = "399808"
TEXT_MIN_TRAIN_MONTHS = 24
TEXT_MIN_VALIDATION_MONTHS = 12

FEATURE_FIELDS = [
    "period_start", "period_end", "split", "document_count", "unique_source_count",
    "event_count", "stock_count", "predicate_count", "true_predicate_count",
    "positive_predicate_rate", "avg_predicate_confidence", "avg_event_evidence",
    "avg_entity_confidence", "qualified_rule_hits", "rule_supporting_document_count",
    "policy_count", "announcement_count", "news_count", "ir_qa_count",
    "battery_exposure", "solar_exposure", "storage_exposure", "grid_exposure",
    "wind_exposure", "factor_mean", "factor_p25", "factor_median", "factor_p75",
    "factor_std", "factor_positive_rate", "ai_annotation_coverage",
    "fulltext_completeness_rate", "coverage_status",
    *[f"predicate_{name}" for name in PREDICATE_COLUMNS],
]

MODEL_NAMES = ("persistence", "seasonal", "ar1", "no_text_ridge", "ridge_text", "elastic_net_text")
PREDICTOR_FIELDS = [
    "document_count", "unique_source_count", "event_count", "stock_count",
    "true_predicate_count", "positive_predicate_rate", "avg_predicate_confidence",
    "avg_event_evidence", "avg_entity_confidence", "qualified_rule_hits",
    "policy_count", "announcement_count", "news_count", "ir_qa_count",
    "battery_exposure", "solar_exposure", "storage_exposure", "grid_exposure",
    "wind_exposure", "factor_mean", "factor_std", "factor_positive_rate",
]

EVENT_TYPES = (
    "policy_support", "capacity_expansion", "attention_spread", "regulatory_penalty",
    "inquiry_letter_pressure", "earnings_quality_anomaly", "supply_chain_disruption",
    "product_price_increase", "investor_question_pressure",
)
SINGLE_TEXT_FIELDS = [
    "source_policy", "source_announcement", "source_news", "source_ir_qa",
    *[f"event_{name}" for name in EVENT_TYPES],
    "stock_count", "avg_event_evidence", "avg_entity_confidence",
    *[f"predicate_{name}" for name in PREDICATE_COLUMNS],
    "latest_published_yoy", "season_sin", "season_cos",
]
SINGLE_TEXT_AUDIT_FIELDS = [
    "latest_published_yoy", "stock_count", "avg_event_evidence", "avg_entity_confidence",
    *[f"predicate_{name}" for name in PREDICATE_COLUMNS],
]
STRATEGY_VALIDATION_START = "2022-01-01"
STRATEGY_VALIDATION_END = "2023-12-31"
MONTHLY_NOWCAST_FEATURE_SETS = {
    "audit_predicates_state": [
        "document_count", "unique_source_count", "event_count", "stock_count",
        "avg_predicate_confidence", "avg_event_evidence", "avg_entity_confidence",
        *[f"predicate_{name}" for name in PREDICATE_COLUMNS],
    ],
    "audit_counts": [
        "document_count", "unique_source_count", "event_count", "stock_count",
        "true_predicate_count", "positive_predicate_rate", "avg_predicate_confidence",
        "avg_event_evidence", "avg_entity_confidence", "qualified_rule_hits",
        "rule_supporting_document_count", "policy_count", "announcement_count",
        "news_count", "ir_qa_count", "battery_exposure", "solar_exposure",
        "storage_exposure", "grid_exposure", "wind_exposure", "ai_annotation_coverage",
    ],
}


def _read_csv(name: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(name: str, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path = SAMPLE_DIR / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for field in fields:
                value = row.get(field, "")
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif isinstance(value, float):
                    value = f"{value:.6f}"
                rendered[field] = value
            writer.writerow(rendered)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _split(period_end: str) -> str:
    if period_end <= "2021-12-31":
        return "train"
    if period_end <= "2023-12-31":
        return "validation"
    return "oos"


def _canonical_document(row: dict[str, str]) -> str:
    url = row.get("url", "").strip().lower().split("#", 1)[0]
    if url:
        return url
    text = re.sub(r"\s+", "", f"{row.get('title', '')}{row.get('content', '')}").lower()
    return text[:800]


def _target_periods() -> list[dict[str, str]]:
    rows = _read_csv("macro_target_history.csv")
    rows.sort(key=lambda item: item["period_end"])
    if not rows:
        return []
    last_end = datetime.strptime(rows[-1]["period_end"], "%Y-%m-%d").date()
    next_start = last_end + timedelta(days=1)
    today = date.today()
    if next_start <= today:
        if next_start.month == 12:
            next_end = date(next_start.year, 12, 31)
        else:
            next_end = date(next_start.year, next_start.month + 1, 1) - timedelta(days=1)
        rows.append({
            "period_start": next_start.isoformat(), "period_end": next_end.isoformat(),
            "period_kind": "monthly", "actual_yoy": "", "release_date": "",
            "source_name": "待国家统计局发布", "source_url": "", "vintage": "nowcast",
        })
    return rows


def aggregate_monthly_features() -> list[dict[str, Any]]:
    """Aggregate unique verified documents and downstream audited results by period."""
    documents = [*_read_csv("raw_documents.csv"), *_read_csv("macro_historical_documents.csv")]
    events = [*_read_csv("events.csv"), *_read_csv("macro_historical_events.csv")]
    predicates = [*_read_csv("predicates.csv"), *_read_csv("macro_historical_predicates.csv")]
    links = [*_read_csv("entity_links.csv"), *_read_csv("macro_historical_entity_links.csv")]
    factors = _read_csv("factors.csv")
    rules = {row["rule_id"]: row for row in _read_csv("rules.csv") if row.get("status") == "qualified"}

    unique_docs: dict[str, dict[str, str]] = {}
    for row in documents:
        key = _canonical_document(row)
        previous = unique_docs.get(key)
        if previous is None or row.get("publish_time", "") < previous.get("publish_time", ""):
            unique_docs[key] = row
    docs = list(unique_docs.values())
    event_by_id = {row["event_id"]: row for row in events}
    events_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in events:
        events_by_doc[row["doc_id"]].append(row)
    predicates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in predicates:
        predicates_by_event[row["event_id"]].append(row)
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in links:
        links_by_doc[row["doc_id"]].append(row)
    factors_by_doc: dict[str, list[float]] = defaultdict(list)
    rule_hits_by_doc: dict[str, set[str]] = defaultdict(set)
    for factor in factors:
        event_ids = [value for value in factor.get("trigger_event_ids", "").split("|") if value]
        doc_ids = {event_by_id[event_id]["doc_id"] for event_id in event_ids if event_id in event_by_id}
        for doc_id in doc_ids:
            factors_by_doc[doc_id].append(_f(factor.get("factor_value")))
            for rule_id in factor.get("trigger_rule_ids", "").split("|"):
                if rule_id in rules:
                    rule_hits_by_doc[doc_id].add(rule_id)

    annotation_success: set[str] = set()
    annotation_path = SAMPLE_DIR / "ai_annotations.jsonl"
    if annotation_path.exists():
        latest: dict[str, dict[str, Any]] = {}
        for line in annotation_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            doc_id = str(record.get("doc_id", ""))
            if doc_id and (doc_id not in latest or str(record.get("generated_at", "")) >= str(latest[doc_id].get("generated_at", ""))):
                latest[doc_id] = record
        annotation_success = {doc_id for doc_id, row in latest.items() if row.get("status") == "success"}

    topic_patterns = {
        "battery_exposure": ("电池", "锂电", "隔膜", "正极", "负极"),
        "solar_exposure": ("光伏", "太阳能", "硅片", "组件"),
        "storage_exposure": ("储能", "新型储能"),
        "grid_exposure": ("电网", "输电", "配电", "特高压"),
        "wind_exposure": ("风电", "风机", "风力"),
    }
    output: list[dict[str, Any]] = []
    for target in _target_periods():
        start, end = target["period_start"], target["period_end"]
        period_docs = [row for row in docs if start <= row.get("publish_time", "") <= end]
        doc_ids = {row["doc_id"] for row in period_docs}
        period_events = [event for doc_id in doc_ids for event in events_by_doc.get(doc_id, [])]
        event_ids = {row["event_id"] for row in period_events}
        period_predicates = [row for event_id in event_ids for row in predicates_by_event.get(event_id, [])]
        period_links = [row for doc_id in doc_ids for row in links_by_doc.get(doc_id, [])]
        period_factors = [value for doc_id in doc_ids for value in factors_by_doc.get(doc_id, [])]
        true_count = sum(row.get("value", "").lower() == "true" for row in period_predicates)
        boolean_count = sum(row.get("value", "").lower() in {"true", "false"} for row in period_predicates)
        text_by_doc = {row["doc_id"]: f"{row.get('title', '')} {row.get('content', '')}" for row in period_docs}
        source_counts = Counter(row.get("source_type", "") for row in period_docs)
        rule_hit_pairs = {(doc_id, rule_id) for doc_id in doc_ids for rule_id in rule_hits_by_doc.get(doc_id, set())}
        row: dict[str, Any] = {
            "period_start": start, "period_end": end, "split": _split(end),
            "document_count": len(period_docs),
            "unique_source_count": len({row.get("source_name", "") for row in period_docs if row.get("source_name")}),
            "event_count": len({row["event_id"] for row in period_events}),
            "stock_count": len({row["stock_code"] for row in period_events}),
            "predicate_count": len(period_predicates), "true_predicate_count": true_count,
            "positive_predicate_rate": true_count / boolean_count if boolean_count else 0.0,
            "avg_predicate_confidence": _mean([_f(row.get("confidence")) for row in period_predicates]),
            "avg_event_evidence": _mean([_f(row.get("evidence_strength")) for row in period_events]),
            "avg_entity_confidence": _mean([_f(row.get("confidence")) for row in period_links]),
            "qualified_rule_hits": len(rule_hit_pairs),
            "rule_supporting_document_count": len({pair[0] for pair in rule_hit_pairs}),
            "policy_count": source_counts["policy"], "announcement_count": source_counts["announcement"],
            "news_count": source_counts["news"], "ir_qa_count": source_counts["ir_qa"],
            "factor_mean": _mean(period_factors), "factor_p25": _quantile(period_factors, .25),
            "factor_median": _quantile(period_factors, .5), "factor_p75": _quantile(period_factors, .75),
            "factor_std": statistics.pstdev(period_factors) if len(period_factors) > 1 else 0.0,
            "factor_positive_rate": sum(value > 0 for value in period_factors) / len(period_factors) if period_factors else 0.0,
            "ai_annotation_coverage": len(doc_ids & annotation_success) / len(doc_ids) if doc_ids else 0.0,
            "fulltext_completeness_rate": 0.0,
            "coverage_status": "sufficient" if len(period_docs) >= 5 else "insufficient",
        }
        for field, keywords in topic_patterns.items():
            row[field] = sum(any(keyword in text_by_doc[doc_id] for keyword in keywords) for doc_id in doc_ids)
        monthly_predicate_values: dict[str, list[float]] = defaultdict(list)
        for predicate in period_predicates:
            raw = predicate.get("value", "").lower()
            if raw in {"true", "false"}:
                monthly_predicate_values[predicate.get("predicate_name", "")].append(1.0 if raw == "true" else 0.0)
        for name in PREDICATE_COLUMNS:
            row[f"predicate_{name}"] = _mean(monthly_predicate_values.get(name, []))
        output.append(row)
    _write_csv("macro_monthly_features.csv", FEATURE_FIELDS, output)
    return output


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    aug = [matrix[i][:] + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        if abs(aug[col][col]) < 1e-10:
            continue
        scale = aug[col][col]
        aug[col] = [value / scale for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            aug[row] = [aug[row][i] - factor * aug[col][i] for i in range(n + 1)]
    return [aug[i][-1] if abs(aug[i][i]) > 1e-10 else 0.0 for i in range(n)]


def _ridge_fit(x: list[list[float]], y: list[float], alpha: float) -> tuple[list[float], list[float], list[float]]:
    if not x:
        return [0.0], [], []
    p = len(x[0])
    means = [_mean([row[j] for row in x]) for j in range(p)]
    scales = [statistics.pstdev([row[j] for row in x]) or 1.0 for j in range(p)]
    z = [[1.0] + [(row[j] - means[j]) / scales[j] for j in range(p)] for row in x]
    size = p + 1
    gram = [[sum(row[i] * row[j] for row in z) for j in range(size)] for i in range(size)]
    for i in range(1, size):
        gram[i][i] += alpha
    rhs = [sum(row[i] * target for row, target in zip(z, y)) for i in range(size)]
    return _solve(gram, rhs), means, scales


def _ridge_predict(model: tuple[list[float], list[float], list[float]], row: list[float]) -> float:
    coefficients, means, scales = model
    return coefficients[0] + sum(coefficients[j + 1] * (row[j] - means[j]) / scales[j] for j in range(len(row)))


def _ridge_predict_clipped(
    model: tuple[list[float], list[float], list[float]], row: list[float], z_limit: float = 3.0,
) -> float:
    """Predict after clipping standardized features to control OOS extrapolation."""
    coefficients, means, scales = model
    standardized = [max(-z_limit, min(z_limit, (row[j] - means[j]) / scales[j])) for j in range(len(row))]
    return coefficients[0] + sum(coefficients[j + 1] * standardized[j] for j in range(len(row)))


def _elastic_net_fit(
    x: list[list[float]], y: list[float], alpha: float = 0.08, l1_ratio: float = 0.5,
) -> tuple[list[float], list[float], list[float]]:
    """Small deterministic coordinate-descent Elastic Net for low-dimensional research."""
    if not x:
        return [0.0], [], []
    p = len(x[0])
    means = [_mean([row[j] for row in x]) for j in range(p)]
    scales = [statistics.pstdev([row[j] for row in x]) or 1.0 for j in range(p)]
    z = [[(row[j] - means[j]) / scales[j] for j in range(p)] for row in x]
    intercept = _mean(y)
    centered = [value - intercept for value in y]
    coefficients = [0.0] * p
    for _ in range(300):
        largest_change = 0.0
        for j in range(p):
            residual = [
                centered[i] - sum(z[i][k] * coefficients[k] for k in range(p) if k != j)
                for i in range(len(z))
            ]
            rho = _mean([z[i][j] * residual[i] for i in range(len(z))])
            threshold = alpha * l1_ratio
            numerator = math.copysign(max(abs(rho) - threshold, 0.0), rho)
            denominator = _mean([row[j] ** 2 for row in z]) + alpha * (1 - l1_ratio)
            updated = numerator / denominator if denominator else 0.0
            largest_change = max(largest_change, abs(updated - coefficients[j]))
            coefficients[j] = updated
        if largest_change < 1e-8:
            break
    return [intercept, *coefficients], means, scales


def _target_for_document(document_date: str) -> dict[str, str] | None:
    for row in _target_periods():
        if row["period_start"] <= document_date <= row["period_end"] and row.get("actual_yoy"):
            return row
    return None


def _latest_actual_at(information_date: str, *, exclude_period_end: str = "") -> float:
    available = [
        row for row in _read_csv("macro_target_history.csv")
        if row.get("actual_yoy") and row.get("release_date")
        and row["release_date"] <= information_date
        and (not exclude_period_end or row["period_end"] < exclude_period_end)
    ]
    available.sort(key=lambda row: row["release_date"])
    return _f(available[-1]["actual_yoy"]) if available else 0.0


def _single_text_base(source_type: str, event_type: str, information_date: str, target_end: str) -> dict[str, float]:
    values = {field: 0.0 for field in SINGLE_TEXT_FIELDS}
    source_field = f"source_{source_type}"
    event_field = f"event_{event_type}"
    if source_field in values:
        values[source_field] = 1.0
    if event_field in values:
        values[event_field] = 1.0
    month = int(target_end[5:7])
    values["latest_published_yoy"] = _latest_actual_at(information_date, exclude_period_end=target_end)
    values["season_sin"] = math.sin(month * math.pi / 6)
    values["season_cos"] = math.cos(month * math.pi / 6)
    return values


def _historical_single_text_rows() -> list[dict[str, Any]]:
    documents = _read_csv("macro_historical_documents.csv")
    events = _read_csv("macro_historical_events.csv")
    predicates = _read_csv("macro_historical_predicates.csv")
    links = _read_csv("macro_historical_entity_links.csv")
    events_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        events_by_doc[event["doc_id"]].append(event)
    predicates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for predicate in predicates:
        predicates_by_event[predicate["event_id"]].append(predicate)
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in links:
        links_by_doc[link["doc_id"]].append(link)
    rows: list[dict[str, Any]] = []
    for document in documents:
        target = _target_for_document(document["publish_time"])
        if target is None:
            continue
        doc_events = events_by_doc.get(document["doc_id"], [])
        if not doc_events:
            continue
        event_counts = Counter(event["event_type"] for event in doc_events)
        dominant_event = event_counts.most_common(1)[0][0]
        values = _single_text_base(
            document["source_type"], dominant_event, document["publish_time"], target["period_end"],
        )
        values["stock_count"] = float(len({event["stock_code"] for event in doc_events}))
        values["avg_event_evidence"] = _mean([_f(event["evidence_strength"]) for event in doc_events])
        values["avg_entity_confidence"] = _mean([_f(link["confidence"]) for link in links_by_doc.get(document["doc_id"], [])])
        predicate_values: dict[str, list[float]] = defaultdict(list)
        for event in doc_events:
            for predicate in predicates_by_event.get(event["event_id"], []):
                raw = predicate["value"].lower()
                predicate_values[predicate["predicate_name"]].append(
                    1.0 if raw == "true" else 0.0 if raw == "false" else _f(raw)
                )
        for name in PREDICATE_COLUMNS:
            values[f"predicate_{name}"] = _mean(predicate_values.get(name, []))
        rows.append({
            "doc_id": document["doc_id"], "information_date": document["publish_time"],
            "source_type": document["source_type"],
            "target_period_end": target["period_end"], "split": _split(target["period_end"]),
            "actual_yoy": _f(target["actual_yoy"]), "values": values,
        })
    return rows


def build_single_text_model() -> dict[str, Any]:
    """Freeze a validation-selected, persistence-anchored single-text model.

    The candidate grid is deliberately small.  Feature set, regularisation and
    anchor weight are selected on 2022--2023 only, then frozen before OOS.  The
    persistence anchor reduces the variance of a sparse one-document forecast
    without allowing the latest value to replace the audited text features.
    """
    rows = _historical_single_text_rows()
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    feature_sets = {
        "all_structured": SINGLE_TEXT_FIELDS,
        "audit_predicates_state": SINGLE_TEXT_AUDIT_FIELDS,
    }
    candidates: list[dict[str, Any]] = []
    for feature_set_name, feature_fields in feature_sets.items():
        train_x = [[row["values"][field] for field in feature_fields] for row in train]
        train_y = [row["actual_yoy"] for row in train]
        for alpha in (50.0, 100.0, 200.0, 500.0):
            frozen = _ridge_fit(train_x, train_y, alpha)
            raw_predictions = [
                _ridge_predict(frozen, [row["values"][field] for field in feature_fields])
                for row in validation
            ]
            for ridge_weight in (0.50, 0.75, 1.00):
                predictions = [
                    ridge_weight * prediction + (1 - ridge_weight) * row["values"]["latest_published_yoy"]
                    for prediction, row in zip(raw_predictions, validation)
                ]
                errors = [prediction - row["actual_yoy"] for prediction, row in zip(predictions, validation)]
                candidates.append({
                    "feature_set_name": feature_set_name,
                    "feature_fields": feature_fields,
                    "alpha": alpha,
                    "ridge_weight": ridge_weight,
                    "model": frozen,
                    "validation_mae": _mean([abs(error) for error in errors]),
                    "validation_rmse": math.sqrt(_mean([error * error for error in errors])) if errors else 0.0,
                })
    selected = min(
        candidates,
        key=lambda item: (item["validation_mae"], item["validation_rmse"], len(item["feature_fields"])),
    )
    alpha = selected["alpha"]
    model = selected["model"]
    feature_fields = list(selected["feature_fields"])
    ridge_weight = selected["ridge_weight"]
    validation_mae = selected["validation_mae"]
    validation_errors = [
        (
            ridge_weight * _ridge_predict(model, [row["values"][field] for field in feature_fields])
            + (1 - ridge_weight) * row["values"]["latest_published_yoy"]
            - row["actual_yoy"]
        )
        for row in validation
    ]
    persistence_errors = [row["values"]["latest_published_yoy"] - row["actual_yoy"] for row in validation]
    persistence_mae = _mean([abs(error) for error in persistence_errors])
    absolute_improvement = persistence_mae - validation_mae
    relative_improvement = absolute_improvement / persistence_mae if persistence_mae else 0.0
    source_type_counts = Counter(row["source_type"] for row in rows)
    payload = {
        "model_name": "single_text_ridge_anchor_blend", "alpha": alpha,
        "feature_set_name": selected["feature_set_name"], "feature_fields": feature_fields,
        "ridge_weight": ridge_weight, "persistence_anchor_weight": 1 - ridge_weight,
        "coefficients": model[0], "means": model[1], "scales": model[2],
        "training_document_count": len(train), "validation_document_count": len(validation),
        "training_period": "2015-01-01 至 2021-12-31",
        "validation_period": "2022-01-01 至 2023-12-31",
        "validation_mae": validation_mae,
        "validation_rmse": selected["validation_rmse"],
        "persistence_validation_mae": persistence_mae,
        "validation_mae_improvement": absolute_improvement,
        "validation_relative_improvement": relative_improvement,
        "historical_source_type_counts": dict(sorted(source_type_counts.items())),
        "prediction_interval_half_width_90": _quantile([abs(error) for error in validation_errors], .9) if validation_errors else 4.0,
        "text_increment_status": "validated_positive" if absolute_improvement >= 0.10 and relative_improvement >= 0.05 else "not_established",
        "selection_boundary": "特征集、Ridge 正则和持久性锚权重只使用 2022—2023 验证集选择，2024 年起冻结。",
        "information_boundary": "每篇历史文本只使用其首次发布日期当日可见信息；目标值发布日期必须严格晚于文本日期。",
    }
    (SAMPLE_DIR / "macro_single_text_model.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    _write_csv(
        "macro_single_text_metrics.csv",
        ["model", "training_document_count", "validation_document_count", "validation_mae", "validation_rmse", "persistence_validation_mae", "validation_mae_improvement", "validation_relative_improvement", "text_increment_status"],
        [{"model": payload["model_name"], **payload}],
    )
    return payload


def _ar_prediction(history: list[float]) -> float:
    if len(history) < 4:
        return history[-1] if history else 0.0
    x = [[history[i - 1]] for i in range(1, len(history))]
    return _ridge_predict(_ridge_fit(x, history[1:], 1.0), [history[-1]])


def _model_predictions(history_rows: list[dict[str, Any]], current_feature: dict[str, Any]) -> dict[str, float]:
    values = [_f(row["actual_yoy"]) for row in history_rows]
    latest = values[-1] if values else 0.0
    month = int(current_feature["period_end"][5:7])
    seasonal_values = [_f(row["actual_yoy"]) for row in history_rows if int(row["period_end"][5:7]) == month]
    seasonal = _mean(seasonal_values[-5:]) if seasonal_values else latest
    ar1 = _ar_prediction(values)
    no_text_x = [[_f(row["actual_yoy"]), math.sin(int(row["period_end"][5:7]) * math.pi / 6), math.cos(int(row["period_end"][5:7]) * math.pi / 6)] for row in history_rows[:-1]]
    no_text_y = values[1:]
    no_text_row = [latest, math.sin(month * math.pi / 6), math.cos(month * math.pi / 6)]
    no_text = _ridge_predict(_ridge_fit(no_text_x, no_text_y, 2.0), no_text_row) if len(no_text_x) >= 8 else ar1
    covered = [row for row in history_rows if int(_f(row.get("document_count"))) > 0]
    sufficient = len(covered) >= TEXT_MIN_TRAIN_MONTHS
    if sufficient:
        text_x = [[_f(row[field]) for field in PREDICTOR_FIELDS] for row in history_rows]
        text_y = values
        feature_row = [_f(current_feature[field]) for field in PREDICTOR_FIELDS]
        ridge_text = _ridge_predict(_ridge_fit(text_x, text_y, 8.0), feature_row)
        elastic_text = _ridge_predict(_elastic_net_fit(text_x, text_y), feature_row)
    else:
        ridge_text = no_text
        elastic_text = no_text
    return {
        "persistence": latest, "seasonal": seasonal, "ar1": ar1, "no_text_ridge": no_text,
        "ridge_text": ridge_text, "elastic_net_text": elastic_text,
    }


def _monthly_model_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one release-date-safe observation per official target month."""
    feature_by_end = {row["period_end"]: row for row in features}
    targets = _target_periods()
    known = [{**row, **feature_by_end.get(row["period_end"], {})} for row in targets if row.get("actual_yoy")]
    rows: list[dict[str, Any]] = []
    for target in targets:
        if not target.get("actual_yoy"):
            continue
        as_of = target["period_end"]
        feature = feature_by_end.get(as_of, {field: 0 for field in FEATURE_FIELDS})
        available = [row for row in known if row.get("release_date") and row["release_date"] <= as_of]
        if len(available) < 12:
            continue
        no_text_prediction = _model_predictions(available, feature)["no_text_ridge"]
        rows.append({
            "period_end": as_of, "split": _split(as_of), "feature": feature,
            "actual_yoy": _f(target["actual_yoy"]), "no_text_prediction": no_text_prediction,
            "target_release_date": target.get("release_date", ""),
        })
    return rows


def build_monthly_nowcast_model(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Fit text evidence to the residual of the frozen no-text monthly baseline.

    Each official month contributes at most one row.  A small feature/alpha/weight
    grid is chosen on 2022--2023 validation MAE and frozen before 2024 OOS.
    """
    rows = _monthly_model_rows(features)
    train = [row for row in rows if row["split"] == "train" and _f(row["feature"].get("document_count")) > 0]
    validation = [row for row in rows if row["split"] == "validation" and _f(row["feature"].get("document_count")) > 0]
    candidates: list[dict[str, Any]] = []
    no_text_errors = [row["no_text_prediction"] - row["actual_yoy"] for row in validation]
    increment_cap = _mean([abs(error) for error in no_text_errors]) or 3.0
    for feature_set_name, fields in MONTHLY_NOWCAST_FEATURE_SETS.items():
        train_x = [[_f(row["feature"].get(field)) for field in fields] for row in train]
        train_y = [row["actual_yoy"] - row["no_text_prediction"] for row in train]
        for alpha in (10.0, 20.0, 50.0, 100.0, 200.0):
            model = _ridge_fit(train_x, train_y, alpha)
            residual_predictions = [
                _ridge_predict_clipped(model, [_f(row["feature"].get(field)) for field in fields])
                for row in validation
            ]
            for text_weight in (0.25, 0.50, 0.75, 1.00):
                errors = [
                    row["no_text_prediction"]
                    + max(-increment_cap, min(increment_cap, text_weight * residual))
                    - row["actual_yoy"]
                    for row, residual in zip(validation, residual_predictions)
                ]
                candidates.append({
                    "feature_set_name": feature_set_name, "feature_fields": fields,
                    "alpha": alpha, "text_weight": text_weight, "model": model,
                    "validation_mae": _mean([abs(error) for error in errors]),
                    "validation_rmse": math.sqrt(_mean([error * error for error in errors])) if errors else 0.0,
                })
    selected = min(
        candidates,
        key=lambda item: (item["validation_mae"], item["validation_rmse"], len(item["feature_fields"])),
    )
    validation_errors = [
        row["no_text_prediction"]
        + max(-increment_cap, min(increment_cap, selected["text_weight"] * _ridge_predict_clipped(
            selected["model"], [_f(row["feature"].get(field)) for field in selected["feature_fields"]],
        )))
        - row["actual_yoy"]
        for row in validation
    ]
    payload = {
        "model_name": "monthly_text_residual_ridge", "alpha": selected["alpha"],
        "feature_set_name": selected["feature_set_name"],
        "feature_fields": list(selected["feature_fields"]), "text_weight": selected["text_weight"],
        "coefficients": selected["model"][0], "means": selected["model"][1], "scales": selected["model"][2],
        "training_month_count": len(train), "validation_month_count": len(validation),
        "training_period": "2015-01-01 至 2021-12-31", "validation_period": "2022-01-01 至 2023-12-31",
        "validation_mae": selected["validation_mae"], "validation_rmse": selected["validation_rmse"],
        "no_text_validation_mae": _mean([abs(error) for error in no_text_errors]),
        "validation_mae_improvement": _mean([abs(error) for error in no_text_errors]) - selected["validation_mae"],
        "text_increment_cap": increment_cap, "standardized_feature_clip": 3.0,
        "prediction_interval_half_width_90": _quantile([abs(error) for error in validation_errors], .9),
        "selection_boundary": "月度特征集、Ridge正则与文本残差权重只使用2022—2023验证期选择，2024年起冻结。",
        "unit_of_observation": "one_unique_month_after_document_deduplication",
    }
    (SAMPLE_DIR / "macro_monthly_nowcast_model.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    _write_csv(
        "macro_monthly_nowcast_metrics.csv",
        ["model", "training_month_count", "validation_month_count", "validation_mae", "validation_rmse",
         "no_text_validation_mae", "validation_mae_improvement", "text_weight"],
        [{"model": payload["model_name"], **payload}],
    )
    return payload


def _monthly_text_prediction(model: dict[str, Any], feature: dict[str, Any], no_text_prediction: float) -> tuple[float, float]:
    fields = list(model["feature_fields"])
    row = [_f(feature.get(field)) for field in fields]
    frozen = (list(model["coefficients"]), list(model["means"]), list(model["scales"]))
    increment = _f(model.get("text_weight"), 1.0) * _ridge_predict_clipped(
        frozen, row, _f(model.get("standardized_feature_clip"), 3.0),
    )
    cap = _f(model.get("text_increment_cap"), 3.0)
    if int(_f(feature.get("document_count"))) <= 0:
        increment = 0.0
    else:
        increment = max(-cap, min(cap, increment))
    return no_text_prediction + increment, increment


def build_forecasts(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model_path = SAMPLE_DIR / "macro_monthly_nowcast_model.json"
    monthly_model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else build_monthly_nowcast_model(features)
    targets = _target_periods()
    feature_by_end = {row["period_end"]: row for row in features}
    known = [{**row, **feature_by_end.get(row["period_end"], {})} for row in targets if row.get("actual_yoy")]
    forecast_rows: list[dict[str, Any]] = []
    prediction_records: list[dict[str, Any]] = []
    validation_records: list[dict[str, Any]] = []
    for target in targets:
        as_of = target["period_end"]
        feature = feature_by_end.get(as_of, {field: 0 for field in FEATURE_FIELDS})
        # This is the central anti-leakage boundary: only values released by month-end are trainable.
        available = [row for row in known if row.get("release_date") and row["release_date"] <= as_of]
        if len(available) < 12:
            continue
        predictions = _model_predictions(available, feature)
        actual = target.get("actual_yoy", "")
        split = _split(as_of)
        no_text_prediction = predictions["no_text_ridge"]
        text_prediction, text_increment = _monthly_text_prediction(monthly_model, feature, no_text_prediction)
        if as_of >= "2022-01-01":
            chosen = text_prediction
            prior_actual = _f(available[-1]["actual_yoy"])
            released_residuals = [
                row["residuals"]["monthly_text_residual_ridge"]
                for row in prediction_records
                if row["release_date"] <= as_of
            ]
            width = _quantile([abs(value) for value in released_residuals], .9) if released_residuals else 3.0
            text_train_count = sum(int(_f(row.get("document_count"))) > 0 for row in available if row["period_end"] <= "2021-12-31")
            text_validation_count = sum(int(_f(row.get("document_count"))) > 0 for row in available if "2022-01-01" <= row["period_end"] <= "2023-12-31")
            text_status = "evaluated" if text_train_count >= TEXT_MIN_TRAIN_MONTHS and text_validation_count >= TEXT_MIN_VALIDATION_MONTHS else "insufficient_history"
            forecast_rows.append({
                "as_of_date": as_of, "target_period_start": target["period_start"],
                "target_period_end": as_of, "split": split, "selected_model": "monthly_text_residual_ridge",
                "predicted_yoy": chosen, "actual_yoy": actual, "latest_published_yoy": prior_actual,
                "no_text_predicted_yoy": no_text_prediction, "text_prediction_increment": text_increment,
                "predicted_acceleration": chosen - prior_actual, "lower_90": chosen - width,
                "upper_90": chosen + width, "text_document_count": int(_f(feature.get("document_count"))),
                "text_train_period_count": text_train_count, "text_validation_period_count": text_validation_count,
                "text_increment_status": text_status, "evidence_status": "sufficient" if text_status == "evaluated" else "insufficient",
                "target_release_date": target.get("release_date", ""), "is_oos": split == "oos",
            })
        release_date = target.get("release_date", "")
        if actual and release_date:
            record = {
                "release_date": release_date,
                "errors": {**{name: abs(value - _f(actual)) for name, value in predictions.items()},
                           "monthly_text_residual_ridge": abs(text_prediction - _f(actual))},
                "residuals": {**{name: _f(actual) - value for name, value in predictions.items()},
                              "monthly_text_residual_ridge": _f(actual) - text_prediction},
            }
            prediction_records.append(record)
            if split == "validation":
                validation_records.append(record)
    fields = [
        "as_of_date", "target_period_start", "target_period_end", "split", "selected_model",
        "predicted_yoy", "actual_yoy", "latest_published_yoy", "no_text_predicted_yoy",
        "text_prediction_increment", "predicted_acceleration",
        "lower_90", "upper_90", "text_document_count", "text_train_period_count",
        "text_validation_period_count", "text_increment_status", "evidence_status",
        "target_release_date", "is_oos",
    ]
    _write_csv("macro_forecasts.csv", fields, forecast_rows)
    _write_forecast_metrics(forecast_rows)
    return forecast_rows


def _write_forecast_metrics(rows: list[dict[str, Any]]) -> None:
    metrics: list[dict[str, Any]] = []
    for split in ("validation", "oos"):
        samples = [row for row in rows if row["split"] == split and str(row.get("actual_yoy", "")) != ""]
        errors = [_f(row["predicted_yoy"]) - _f(row["actual_yoy"]) for row in samples]
        no_text_errors = [_f(row["no_text_predicted_yoy"]) - _f(row["actual_yoy"]) for row in samples]
        actuals = [_f(row["actual_yoy"]) for row in samples]
        baseline = [_f(row["latest_published_yoy"]) for row in samples]
        denominator = sum((actual - prior) ** 2 for actual, prior in zip(actuals, baseline))
        direction = [
            ((_f(row["predicted_yoy"]) - _f(row["latest_published_yoy"])) * (_f(row["actual_yoy"]) - _f(row["latest_published_yoy"]))) > 0
            for row in samples
        ]
        coverage = [
            _f(row["lower_90"]) <= _f(row["actual_yoy"]) <= _f(row["upper_90"])
            for row in samples
        ]
        metrics.append({
            "split": split, "model": samples[-1]["selected_model"] if samples else "",
            "sample_count": len(samples), "mae": _mean([abs(error) for error in errors]),
            "rmse": math.sqrt(_mean([error * error for error in errors])) if errors else 0.0,
            "oos_r2": 1 - sum(error * error for error in errors) / denominator if denominator else 0.0,
            "acceleration_direction_accuracy": _mean([float(value) for value in direction]),
            "interval_coverage_90": _mean([float(value) for value in coverage]),
            "no_text_mae": _mean([abs(error) for error in no_text_errors]),
            "mae_improvement_vs_no_text": (
                _mean([abs(error) for error in no_text_errors]) - _mean([abs(error) for error in errors])
            ),
            "text_increment_status": samples[-1]["text_increment_status"] if samples else "insufficient_history",
        })
    _write_csv(
        "macro_forecast_metrics.csv",
        ["split", "model", "sample_count", "mae", "rmse", "oos_r2",
         "acceleration_direction_accuracy", "interval_coverage_90", "no_text_mae",
         "mae_improvement_vs_no_text", "text_increment_status"],
        metrics,
    )


def ensure_market_data() -> list[dict[str, Any]]:
    path = SAMPLE_DIR / "macro_market_data.csv"
    if path.exists():
        return _read_csv("macro_market_data.csv")
    rows: list[dict[str, Any]] = []
    for code, asset_type in ((RISK_CODE, "risk_etf"), (DEFENSIVE_CODE, "defensive_etf")):
        query = urllib.parse.urlencode({
            "secid": f"1.{code}", "klt": "101", "fqt": "1", "beg": "20130101",
            "end": "20500101", "lmt": "10000", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        })
        result = subprocess.run(
            ["curl", "-L", "--fail", "--retry", "2", "--connect-timeout", "10",
             "-A", "Mozilla/5.0", "-e", "https://quote.eastmoney.com/",
             f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{query}"],
            capture_output=True, check=True, text=True, timeout=45,
        )
        payload = json.loads(result.stdout)
        data = payload.get("data") or {}
        for item in data.get("klines") or []:
            values = item.split(",")
            rows.append({
                "trade_date": values[0], "security_code": code, "asset_type": asset_type,
                "open": values[1], "close": values[2], "high": values[3], "low": values[4],
                "volume": values[5], "source_name": "东方财富行情接口",
                "source_url": "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            })
    rows.sort(key=lambda row: (row["trade_date"], row["security_code"]))
    _write_csv("macro_market_data.csv", ["trade_date", "security_code", "asset_type", "open", "close", "high", "low", "volume", "source_name", "source_url"], rows)
    return rows


def _annualized_metrics(returns: list[float], nav: list[float], turnover: list[float]) -> dict[str, float]:
    if not returns:
        return {key: 0.0 for key in ("annual_return", "annual_volatility", "sharpe", "calmar", "max_drawdown", "monthly_win_rate", "annual_turnover")}
    years = len(returns) / 12
    annual_return = nav[-1] ** (1 / years) - 1 if years > 0 and nav[-1] > 0 else 0.0
    annual_vol = statistics.pstdev(returns) * math.sqrt(12) if len(returns) > 1 else 0.0
    peak = nav[0]
    max_drawdown = 0.0
    for value in nav:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    return {
        "annual_return": annual_return, "annual_volatility": annual_vol,
        "sharpe": annual_return / annual_vol if annual_vol else 0.0,
        "calmar": annual_return / abs(max_drawdown) if max_drawdown else 0.0,
        "max_drawdown": max_drawdown,
        "monthly_win_rate": sum(value > 0 for value in returns) / len(returns),
        "annual_turnover": sum(turnover) / years if years else 0.0,
    }


def build_strategy(forecasts: list[dict[str, Any]], market: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market:
        by_code[row["security_code"]].append(row)
    for values in by_code.values():
        values.sort(key=lambda row: row["trade_date"])
    risk = by_code.get(RISK_CODE, [])
    defensive = by_code.get(DEFENSIVE_CODE, [])
    if not risk or not defensive:
        return []
    monthly_risk: dict[str, dict[str, Any]] = {}
    monthly_risk_first: dict[str, dict[str, Any]] = {}
    monthly_def_first: dict[str, dict[str, Any]] = {}
    for row in risk:
        monthly_risk_first.setdefault(row["trade_date"][:7], row)
        monthly_risk[row["trade_date"][:7]] = row
    for row in defensive:
        monthly_def_first.setdefault(row["trade_date"][:7], row)
    months = sorted(set(monthly_risk) & set(monthly_def_first))
    forecast_by_month = {row["target_period_end"][:7]: row for row in forecasts}
    targets = [row for row in _target_periods() if row.get("actual_yoy")]
    signals: list[dict[str, Any]] = []
    for index in range(12, len(months) - 1):
        month, next_month = months[index], months[index + 1]
        current = monthly_risk[month]
        forecast = forecast_by_month.get(month)
        if not forecast:
            continue
        signal_period_end = forecast["target_period_end"]
        known_targets = sorted(
            (row for row in targets if row.get("release_date") and row["release_date"] <= signal_period_end),
            key=lambda row: row["release_date"],
        )
        if len(known_targets) < 2:
            continue
        latest_yoy = _f(known_targets[-1]["actual_yoy"])
        previous_yoy = _f(known_targets[-2]["actual_yoy"])
        current_target = next((row for row in targets if row["period_end"][:7] == month), None)
        if current_target is None:
            continue
        momentum = _f(current["close"]) / _f(monthly_risk[months[index - 12]]["close"]) - 1
        daily_history = [row for row in risk if row["trade_date"] <= current["trade_date"]][-61:]
        daily_returns = [
            _f(daily_history[pos]["close"]) / _f(daily_history[pos - 1]["close"]) - 1
            for pos in range(1, len(daily_history))
        ]
        volatility = statistics.pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) >= 2 else 0.0
        base_weight = min(0.10 / volatility, 1.0) if momentum > 0 and volatility > 0 else 0.0
        predicted_acceleration = _f(forecast.get("predicted_yoy")) - latest_yoy
        known_acceleration = latest_yoy - previous_yoy
        oracle_acceleration = _f(current_target["actual_yoy"]) - latest_yoy
        risk_entry = _f(monthly_risk_first[next_month]["open"])
        defensive_entry = _f(monthly_def_first[next_month]["open"])
        following_index = index + 2
        if following_index >= len(months):
            continue
        following_month = months[following_index]
        if following_month not in monthly_risk_first or following_month not in monthly_def_first:
            continue
        signals.append({
            "signal_month": month, "trade_month": next_month, "signal_date": signal_period_end,
            "momentum_12m": momentum, "volatility_60d": volatility,
            "latest_yoy": latest_yoy, "previous_yoy": previous_yoy,
            "predicted_acceleration": predicted_acceleration,
            "known_acceleration": known_acceleration, "oracle_acceleration": oracle_acceleration,
            "risk_return": _f(monthly_risk_first[following_month]["open"]) / risk_entry - 1,
            "def_return": _f(monthly_def_first[following_month]["open"]) / defensive_entry - 1,
            "weights": {
                "buy_hold": 1.0,
                "trend": base_weight,
                "trend_latest_macro": base_weight if known_acceleration > 0 else 0.5 * base_weight,
                "trend_alphalens": base_weight if predicted_acceleration > 0 else 0.5 * base_weight,
                "trend_oracle": base_weight if oracle_acceleration > 0 else 0.5 * base_weight,
            },
        })

    _write_csv(
        "macro_strategy_selection.csv",
        ["selection_period_start", "selection_period_end", "cost_bps", "momentum_months",
         "volatility_days", "target_annual_volatility", "selection_rule", "oos_frozen"],
        [{
            "selection_period_start": STRATEGY_VALIDATION_START, "selection_period_end": STRATEGY_VALIDATION_END,
            "cost_bps": 10, "momentum_months": 12, "volatility_days": 60,
            "target_annual_volatility": 0.10,
            "selection_rule": "precommitted_trend_volatility_macro_confirmation", "oos_frozen": True,
        }],
    )
    strategies = ("buy_hold", "trend", "trend_latest_macro", "trend_alphalens", "trend_oracle")
    oos_signals = [row for row in signals if row["signal_date"] >= "2024-01-01"]
    output: list[dict[str, Any]] = []
    for cost_bps in (5, 10, 20):
        navs = {name: 1.0 for name in strategies}
        prior_weights = {name: (1.0 if name == "buy_hold" else 0.0) for name in strategies}
        returns_by_strategy: dict[str, list[float]] = defaultdict(list)
        nav_by_strategy: dict[str, list[float]] = defaultdict(list)
        turnover_by_strategy: dict[str, list[float]] = defaultdict(list)
        for signal in oos_signals:
            for strategy in strategies:
                weight = signal["weights"][strategy]
                turnover = abs(weight - prior_weights[strategy])
                net_return = weight * signal["risk_return"] + (1 - weight) * signal["def_return"] - turnover * cost_bps / 10000
                navs[strategy] *= 1 + net_return
                prior_weights[strategy] = weight
                returns_by_strategy[strategy].append(net_return)
                nav_by_strategy[strategy].append(navs[strategy])
                turnover_by_strategy[strategy].append(turnover)
                output.append({
                    "trade_month": signal["trade_month"], "signal_date": signal["signal_date"], "strategy": strategy,
                    "cost_bps": cost_bps, "risk_weight": weight, "defensive_weight": 1 - weight,
                    "net_return": net_return, "nav": navs[strategy], "turnover": turnover,
                    "momentum_12m": signal["momentum_12m"], "volatility_60d": signal["volatility_60d"],
                    "trend_positive": signal["momentum_12m"] > 0,
                    "macro_acceleration": signal["predicted_acceleration"] if strategy == "trend_alphalens" else "",
                    "tradable": strategy != "trend_oracle",
                })
        metric_rows: list[dict[str, Any]] = []
        for strategy in strategies:
            metric = _annualized_metrics(returns_by_strategy[strategy], nav_by_strategy[strategy], turnover_by_strategy[strategy])
            metric_rows.append({
                "strategy": strategy, "cost_bps": cost_bps, **metric,
                "worst_month": min(returns_by_strategy[strategy], default=0.0),
                "sample_months": len(returns_by_strategy[strategy]),
                "tradable": strategy != "trend_oracle",
            })
        existing = _read_csv("macro_strategy_metrics.csv") if cost_bps != 5 else []
        metric_fields = ["strategy", "cost_bps", "annual_return", "annual_volatility", "sharpe", "calmar", "max_drawdown", "monthly_win_rate", "annual_turnover", "worst_month", "sample_months", "tradable"]
        _write_csv("macro_strategy_metrics.csv", metric_fields, [*existing, *metric_rows])
    fields = ["trade_month", "signal_date", "strategy", "cost_bps", "risk_weight", "defensive_weight",
              "net_return", "nav", "turnover", "momentum_12m", "volatility_60d",
              "trend_positive", "macro_acceleration", "tradable"]
    _write_csv("macro_strategy_nav.csv", fields, output)
    _write_strategy_bootstrap(output)
    return output


def _write_strategy_bootstrap(rows: list[dict[str, Any]]) -> None:
    primary = [row for row in rows if int(row["cost_bps"]) == 10]
    alpha = {row["trade_month"]: _f(row["net_return"]) for row in primary if row["strategy"] == "trend_alphalens"}
    baseline = {row["trade_month"]: _f(row["net_return"]) for row in primary if row["strategy"] == "trend"}
    months = sorted(set(alpha) & set(baseline))
    differences = [alpha[month] - baseline[month] for month in months]
    rng = random.Random(20260810)
    boot: list[float] = []
    block = 6
    if differences:
        for _ in range(1000):
            sample: list[float] = []
            while len(sample) < len(differences):
                start = rng.randrange(len(differences))
                sample.extend(differences[(start + offset) % len(differences)] for offset in range(block))
            boot.append(_mean(sample[:len(differences)]) * 12)
    observed = _mean(differences) * 12 if differences else 0.0
    rows_out = [{
        "comparison": "trend_alphalens_minus_trend", "cost_bps": 10,
        "sample_months": len(differences), "block_months": block, "bootstrap_iterations": len(boot),
        "annualized_net_return_difference": observed, "ci_lower_95": _quantile(boot, .025),
        "ci_upper_95": _quantile(boot, .975),
        "conclusion": "trading_increment_not_established" if not boot or _quantile(boot, .025) <= 0 else "positive_increment_observed",
    }]
    _write_csv("macro_strategy_bootstrap.csv", ["comparison", "cost_bps", "sample_months", "block_months", "bootstrap_iterations", "annualized_net_return_difference", "ci_lower_95", "ci_upper_95", "conclusion"], rows_out)


def build_macro_outputs() -> None:
    features = aggregate_monthly_features()
    monthly_model = build_monthly_nowcast_model(features)
    forecasts = build_forecasts(features)
    market = ensure_market_data()
    strategy = build_strategy(forecasts, market)
    print(
        f"宏观层完成：{len(features)} 期特征，{len(forecasts)} 期预测，"
        f"月度模型训练/验证 {monthly_model['training_month_count']}/"
        f"{monthly_model['validation_month_count']} 期，{len(strategy)} 条策略净值记录"
    )


def load_macro_forecast() -> dict[str, Any]:
    forecasts = _read_csv("macro_forecasts.csv")
    metrics = _read_csv("macro_forecast_metrics.csv")
    features = _read_csv("macro_monthly_features.csv")
    latest = forecasts[-1] if forecasts else {}
    oos_metric = next((row for row in metrics if row.get("split") == "oos"), {})
    improvement = _f(oos_metric.get("mae_improvement_vs_no_text"))
    return {
        "target_name": TARGET_NAME, "latest": latest, "history": forecasts,
        "metrics": metrics, "latest_features": features[-1] if features else {},
        "text_increment_conclusion": (
            "冻结OOS文本预测增量已观察" if improvement > 0
            else "冻结OOS文本预测增量不足"
        ),
        "disclaimer": DISCLAIMER,
    }


def load_macro_backtest() -> dict[str, Any]:
    nav = _read_csv("macro_strategy_nav.csv")
    metrics = _read_csv("macro_strategy_metrics.csv")
    bootstrap = _read_csv("macro_strategy_bootstrap.csv")
    selection = _read_csv("macro_strategy_selection.csv")
    primary_nav = [row for row in nav if row.get("cost_bps") == "10"]
    primary_metrics = [row for row in metrics if row.get("cost_bps") == "10"]
    return {
        "risk_asset": {"code": RISK_CODE, "name": "新能源ETF"},
        "defensive_asset": {"code": DEFENSIVE_CODE, "name": "5年期国债ETF"},
        "pre_listing_proxy": {"code": PROXY_CODE, "tradable": False, "used_in_primary_backtest": False},
        "primary_cost_bps": 10, "cost_sensitivity_bps": [5, 10, 20],
        "nav": primary_nav, "metrics": primary_metrics, "bootstrap": bootstrap,
        "strategy_selection": selection[0] if selection else {},
        "rebalance_timing": "月末收盘后形成信号，下一交易日开盘调仓",
        "strategy_description": "新能源ETF 12个月时间序列动量 + 60日波动率缩放 + AlphaLens宏观确认；不做空、不加杠杆。",
        "comparison_boundary": "比较买入持有、纯趋势、趋势+最新已公布宏观、趋势+AlphaLens与不可交易Oracle；主检验为AlphaLens增强减纯趋势。",
        "oracle_label": "Oracle：不可交易，仅作为理论参照上限",
        "disclaimer": DISCLAIMER,
    }


def _live_strategy_weight(as_of_date: str, predicted_yoy: float, latest_yoy: float) -> dict[str, Any]:
    """Apply the frozen trend/volatility/macro-confirmation rule to a live nowcast."""
    risk = sorted(
        (
            row for row in _read_csv("macro_market_data.csv")
            if row.get("security_code") == RISK_CODE and row.get("trade_date", "") <= as_of_date
        ),
        key=lambda row: row["trade_date"],
    )
    monthly: dict[str, dict[str, Any]] = {}
    for row in risk:
        monthly[row["trade_date"][:7]] = row
    month_rows = [monthly[key] for key in sorted(monthly)]
    if len(month_rows) < 13 or len(risk) < 61:
        return {"risk_weight": 0.0, "base_risk_weight": 0.0, "trend_positive": False}
    momentum = _f(month_rows[-1]["close"]) / _f(month_rows[-13]["close"]) - 1
    recent = risk[-61:]
    daily_returns = [
        _f(recent[index]["close"]) / _f(recent[index - 1]["close"]) - 1
        for index in range(1, len(recent))
    ]
    volatility = statistics.pstdev(daily_returns) * math.sqrt(252)
    base_weight = min(0.10 / volatility, 1.0) if momentum > 0 and volatility > 0 else 0.0
    acceleration_positive = predicted_yoy - latest_yoy > 0
    return {
        "risk_weight": base_weight if acceleration_positive else 0.5 * base_weight,
        "base_risk_weight": base_weight,
        "trend_positive": momentum > 0,
        "momentum_12m": momentum,
        "volatility_60d": volatility,
        "macro_acceleration_positive": acceleration_positive,
    }


def load_macro_status() -> dict[str, Any]:
    target = _read_csv("macro_target_history.csv")
    features = _read_csv("macro_monthly_features.csv")
    forecasts = _read_csv("macro_forecasts.csv")
    covered = sum(int(_f(row.get("document_count"))) > 0 for row in features)
    latest = forecasts[-1] if forecasts else {}
    historical_documents = _read_csv("macro_historical_documents.csv")
    model_path = SAMPLE_DIR / "macro_monthly_nowcast_model.json"
    monthly_model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else {}
    return {
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "target_name": TARGET_NAME, "target_observations": len(target),
        "target_period": {"start": target[0]["period_start"] if target else "", "end": target[-1]["period_end"] if target else ""},
        "feature_periods": len(features), "text_covered_periods": covered,
        "verified_historical_texts": len(historical_documents),
        "monthly_nowcast_model": {
            key: monthly_model.get(key)
            for key in (
                "model_name", "training_month_count", "validation_month_count",
                "validation_mae", "validation_rmse", "no_text_validation_mae",
                "validation_mae_improvement", "feature_set_name", "text_weight",
                "selection_boundary", "unit_of_observation",
            )
        },
        "train_period": "2015-01-01 至 2021-12-31", "validation_period": "2022-01-01 至 2023-12-31",
        "oos_period": "2024-01-01 至最新", "latest_forecast": latest,
        "evidence_warning": "月度文本增强模型的独立OOS增量仍需持续积累，不能将验证改善包装成稳定预测能力。",
        "research_scope": "预测行业实体景气，不预测股票价格，不提供投资建议",
        "disclaimer": DISCLAIMER,
    }


def _single_text_target_end(event_date: str) -> str:
    parsed = datetime.strptime(event_date, "%Y-%m-%d").date()
    if parsed.month in {1, 2}:
        end = date(parsed.year, 2, 29 if parsed.year % 4 == 0 and (parsed.year % 100 != 0 or parsed.year % 400 == 0) else 28)
    elif parsed.month == 12:
        end = date(parsed.year, 12, 31)
    else:
        end = date(parsed.year, parsed.month + 1, 1) - timedelta(days=1)
    return end.isoformat()


def _live_duplicate_status(analysis: dict[str, Any]) -> dict[str, Any]:
    """Match a live item against the accepted corpus without exposing raw text."""
    live_url = str(analysis.get("source_url", "")).strip().lower().split("#", 1)[0]
    live_title = re.sub(r"\W+", "", str(analysis.get("document_title", ""))).lower()
    for row in [*_read_csv("raw_documents.csv"), *_read_csv("macro_historical_documents.csv")]:
        corpus_url = str(row.get("url", "")).strip().lower().split("#", 1)[0]
        corpus_title = re.sub(r"\W+", "", str(row.get("title", ""))).lower()
        if live_url and corpus_url == live_url:
            return {"is_duplicate": True, "matched_doc_id": row.get("doc_id", ""), "matched_by": "canonical_url"}
        if live_title and len(live_title) >= 12 and corpus_title == live_title:
            return {"is_duplicate": True, "matched_doc_id": row.get("doc_id", ""), "matched_by": "normalized_title"}
    return {"is_duplicate": False, "matched_doc_id": "", "matched_by": ""}


def live_text_forecast(analysis: dict[str, Any]) -> dict[str, Any]:
    """Show the marginal effect of one audited text on its monthly Nowcast."""
    model_path = SAMPLE_DIR / "macro_monthly_nowcast_model.json"
    features = _read_csv("macro_monthly_features.csv")
    model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else build_monthly_nowcast_model(features)
    event_date = str(analysis.get("event_time", date.today().isoformat()))
    target_end = _single_text_target_end(event_date)
    stocks = analysis.get("stock_results", [])
    feature_by_end = {row["period_end"]: row for row in features}
    before = {field: 0.0 for field in FEATURE_FIELDS}
    before.update(feature_by_end.get(target_end, {}))
    before["period_start"] = before.get("period_start") or target_end[:8] + "01"
    before["period_end"] = target_end
    before["split"] = _split(target_end)
    latest_actual = _latest_actual_at(event_date, exclude_period_end=target_end)
    known = [
        {**row, **feature_by_end.get(row["period_end"], {})}
        for row in _target_periods() if row.get("actual_yoy")
    ]
    available = [row for row in known if row.get("release_date") and row["release_date"] <= event_date]
    if len(available) < 12:
        no_text_prediction = latest_actual
    else:
        no_text_prediction = _model_predictions(available, before)["no_text_ridge"]
    before_prediction, before_increment = _monthly_text_prediction(model, before, no_text_prediction)

    after = dict(before)
    before_docs = int(_f(before.get("document_count")))
    duplicate_status = _live_duplicate_status(analysis)
    is_duplicate = bool(duplicate_status["is_duplicate"])
    added_docs = 0 if is_duplicate else 1
    new_event_count = 0 if is_duplicate else max(1, len(stocks))
    before_event_count = int(_f(before.get("event_count")))
    after["document_count"] = before_docs + added_docs
    after["unique_source_count"] = int(_f(before.get("unique_source_count"))) + added_docs
    after["event_count"] = before_event_count + new_event_count
    existing_stock_count = int(_f(before.get("stock_count")))
    new_stock_count = 0 if is_duplicate else len({stock.get("code") for stock in stocks})
    after["stock_count"] = new_stock_count + existing_stock_count
    new_event_evidence = _mean([
        _f(stock.get("event", {}).get("evidence_strength", analysis.get("evidence_strength", 0)))
        for stock in stocks
    ])
    new_entity_confidence = _mean([_f(stock.get("confidence")) for stock in stocks])
    if not is_duplicate:
        after["avg_event_evidence"] = (
            _f(before.get("avg_event_evidence")) * before_event_count + new_event_evidence * new_event_count
        ) / max(1, before_event_count + new_event_count)
        after["avg_entity_confidence"] = (
            _f(before.get("avg_entity_confidence")) * existing_stock_count + new_entity_confidence * new_stock_count
        ) / max(1, existing_stock_count + new_stock_count)
    predicate_values: dict[str, list[float]] = defaultdict(list)
    for stock in stocks:
        for name, item in stock.get("predicate_fusion", {}).items():
            predicate_values[name].append(_f(item.get("fused")))
    for name in PREDICATE_COLUMNS:
        field = f"predicate_{name}"
        new_value = _mean(predicate_values.get(name, []))
        if not is_duplicate:
            after[field] = (
                _f(before.get(field)) * before_event_count + new_value * new_event_count
            ) / max(1, before_event_count + new_event_count)
    source_field = {"policy": "policy_count", "announcement": "announcement_count", "news": "news_count", "ir_qa": "ir_qa_count"}.get(str(analysis.get("source_type")))
    if source_field and not is_duplicate:
        after[source_field] = int(_f(before.get(source_field))) + 1
    after_prediction, after_increment = _monthly_text_prediction(model, after, no_text_prediction)
    marginal_change = after_prediction - before_prediction
    half_width = _f(model.get("prediction_interval_half_width_90"), 4.0)
    contributions = []
    feature_fields = list(model["feature_fields"])
    frozen = (list(model["coefficients"]), list(model["means"]), list(model["scales"]))
    for index, field in enumerate(feature_fields):
        contribution = _f(model.get("text_weight"), 1.0) * frozen[0][index + 1] * (
            _f(after.get(field)) - _f(before.get(field))
        ) / frozen[2][index]
        contributions.append({"feature": field, "contribution_pct_point": round(contribution, 6)})
    contributions.sort(key=lambda item: abs(item["contribution_pct_point"]), reverse=True)
    strategy_before = _live_strategy_weight(event_date, before_prediction, latest_actual)
    strategy_after = _live_strategy_weight(event_date, after_prediction, latest_actual)
    weight_before = float(strategy_before["risk_weight"])
    weight_after = float(strategy_after["risk_weight"])
    return {
        "forecast_mode": "monthly_nowcast_marginal_text", "target_name": TARGET_NAME,
        "information_date": event_date, "target_period_end": target_end,
        "no_text_predicted_yoy": round(no_text_prediction, 6),
        "nowcast_before_text": round(before_prediction, 6), "nowcast_after_text": round(after_prediction, 6),
        "marginal_change": round(marginal_change, 6), "predicted_yoy": round(after_prediction, 6),
        "latest_published_yoy": round(latest_actual, 6),
        "predicted_acceleration": round(after_prediction - latest_actual, 6),
        "lower_90": round(after_prediction - half_width, 6), "upper_90": round(after_prediction + half_width, 6),
        "model_name": model["model_name"], "model_alpha": model["alpha"],
        "model_text_weight": model["text_weight"],
        "training_month_count": model["training_month_count"],
        "validation_month_count": model["validation_month_count"],
        "validation_mae": model["validation_mae"], "validation_rmse": model["validation_rmse"],
        "no_text_validation_mae": model["no_text_validation_mae"],
        "text_increment_status": "validated_positive" if model["validation_mae"] < model["no_text_validation_mae"] else "not_established",
        "monthly_document_count_before": before_docs, "monthly_document_count_after": before_docs + added_docs,
        "duplicate_status": duplicate_status,
        "top_contributions": contributions[:8],
        "strategy_impact": {
            "risk_weight_before": round(weight_before, 6), "risk_weight_after": round(weight_after, 6),
            "risk_weight_change": round(weight_after - weight_before, 6),
            "base_risk_weight": round(float(strategy_after["base_risk_weight"]), 6),
            "trend_positive": bool(strategy_after["trend_positive"]),
            "macro_acceleration_positive": bool(strategy_after.get("macro_acceleration_positive", False)),
            "realized_return_status": "not_yet_observable",
            "explanation": "仓位遵循冻结的12个月趋势、60日波动率缩放与宏观确认规则；新文本只可能改变下一次调仓的完整或半仓确认，未来持有期尚未发生，不生成虚假单篇策略收益。",
        },
        "new_text_evidence": {
            "source_type": analysis.get("source_type"), "event_type": analysis.get("event_type"),
            "stock_count": len({stock.get("code") for stock in stocks}),
            "avg_event_evidence": round(new_event_evidence, 6),
            "avg_entity_confidence": round(new_entity_confidence, 6),
            "triggered_rule_count": sum(len(stock.get("triggered_rules", [])) for stock in stocks),
        },
        "forecast_basis": "本月已审计文本先去重聚合形成Nowcast；本篇新文本只作为新增证据，页面展示加入前后预测与仓位的边际变化，不把单篇文本当作整个月。",
        "analysis_conclusion": "月度文本增强模型在验证期仅小幅优于无文本基线，但冻结OOS文本增量不足。单篇文本的未来策略收益尚不可观察。",
        "disclaimer": DISCLAIMER,
    }
