"""Document deduplication and monthly feature aggregation for macro nowcasts."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from statistics import mean, median, pstdev
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.macro.schema import MACRO_PREDICATES, period_for_date


SOURCE_TYPES = ("policy", "announcement", "news", "ir_qa")
TOPICS: dict[str, tuple[str, ...]] = {
    "battery": ("电池", "锂电", "锂离子", "钠离子", "隔膜", "正极", "负极", "电解液"),
    "photovoltaic": ("光伏", "硅料", "硅片", "组件", "TOPCon", "HJT"),
    "storage": ("储能", "逆变器", "大储", "户储", "系统集成"),
    "grid": ("电网", "特高压", "配电网", "输变电", "电力设备"),
    "wind": ("风电", "风机", "叶片", "海上风电", "风电机组"),
}


def normalize_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "spm", "from", "source"))
    ]
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalize_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def document_fingerprint(document: dict[str, str]) -> str:
    normalized = normalize_text(f"{document.get('title', '')}{document.get('content', '')}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ngrams(text: str, size: int = 5) -> set[str]:
    normalized = normalize_text(text)
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def near_duplicate_similarity(left: dict[str, str], right: dict[str, str]) -> float:
    left_grams = _ngrams(f"{left.get('title', '')}{left.get('content', '')}")
    right_grams = _ngrams(f"{right.get('title', '')}{right.get('content', '')}")
    if not left_grams or not right_grams:
        return 0.0
    overlap = len(left_grams & right_grams)
    jaccard = overlap / len(left_grams | right_grams)
    containment = overlap / min(len(left_grams), len(right_grams))
    return max(jaccard, containment)


def deduplicate_documents(
    documents: list[dict[str, str]],
    similarity_threshold: float = 0.88,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Keep one canonical document per id, normalized URL, exact or near duplicate.

    Near-duplicate comparisons are bounded to documents in the same macro period,
    which is both economically appropriate and avoids quadratic work across years.
    """
    kept: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_urls: dict[str, str] = {}
    seen_fingerprints: dict[str, str] = {}
    kept_by_period: dict[str, list[dict[str, str]]] = defaultdict(list)
    ordered = sorted(documents, key=lambda row: (row["publish_time"], row["doc_id"]))
    for document in ordered:
        doc_id = document["doc_id"]
        normalized_url = normalize_url(document.get("url", ""))
        fingerprint = document_fingerprint(document)
        reason = ""
        duplicate_of = ""
        if doc_id in seen_ids:
            reason, duplicate_of = "duplicate_doc_id", doc_id
        elif normalized_url and normalized_url in seen_urls:
            reason, duplicate_of = "duplicate_url", seen_urls[normalized_url]
        elif fingerprint in seen_fingerprints:
            reason, duplicate_of = "duplicate_text", seen_fingerprints[fingerprint]
        else:
            period_end = period_for_date(document["publish_time"])["period_end"]
            for canonical in kept_by_period[period_end]:
                if near_duplicate_similarity(document, canonical) >= similarity_threshold:
                    reason, duplicate_of = "near_duplicate_text", canonical["doc_id"]
                    break
        if reason:
            dropped.append({"doc_id": doc_id, "duplicate_of": duplicate_of, "reason": reason})
            continue
        seen_ids.add(doc_id)
        if normalized_url:
            seen_urls[normalized_url] = doc_id
        seen_fingerprints[fingerprint] = doc_id
        period_end = period_for_date(document["publish_time"])["period_end"]
        kept_by_period[period_end].append(document)
        kept.append(document)
    return kept, dropped


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _active_macro_score(row: dict[str, Any]) -> float:
    if str(row.get("value", "")).lower() != "true" or not str(row.get("evidence_text", "")).strip():
        return 0.0
    return float(row["direction"]) * float(row["intensity"]) * float(row["confidence"])


def _event_map(events: Iterable[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        result[event["doc_id"]].append(event)
    return result


def _candidate_values_by_doc(live_analyses: Iterable[dict[str, Any]] | None) -> dict[str, list[float]]:
    result: dict[str, list[float]] = defaultdict(list)
    for analysis in live_analyses or []:
        doc_id = str(analysis.get("doc_id", ""))
        for stock in analysis.get("stock_results", []) or []:
            try:
                result[doc_id].append(float(stock["candidate_factor"]))
            except (KeyError, TypeError, ValueError):
                continue
    return result


def aggregate_base_monthly_features(
    documents: list[dict[str, str]],
    macro_predicates: list[dict[str, Any]],
    events: list[dict[str, str]] | None = None,
    live_analyses: list[dict[str, Any]] | None = None,
    legacy_predicates: list[dict[str, str]] | None = None,
    entity_links: list[dict[str, str]] | None = None,
    factor_rows: list[dict[str, str]] | None = None,
    ai_annotated_doc_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Aggregate route-independent monthly evidence after document deduplication."""
    canonical, dropped = deduplicate_documents(documents)
    canonical_ids = {row["doc_id"] for row in canonical}
    predicates_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in macro_predicates:
        if row["doc_id"] in canonical_ids:
            predicates_by_doc[row["doc_id"]].append(row)
    events_by_doc = _event_map(events or [])
    event_to_doc = {
        event["event_id"]: event["doc_id"]
        for event in events or []
        if event.get("event_id") and event.get("doc_id")
    }
    legacy_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in legacy_predicates or []:
        doc_id = event_to_doc.get(row.get("event_id", ""), "")
        if doc_id in canonical_ids:
            legacy_by_doc[doc_id].append(row)
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in entity_links or []:
        if row.get("doc_id") in canonical_ids:
            links_by_doc[row["doc_id"]].append(row)
    factor_values_by_doc: dict[str, list[float]] = defaultdict(list)
    rule_ids_by_doc: dict[str, set[str]] = defaultdict(set)
    for factor in factor_rows or []:
        event_ids = str(factor.get("trigger_event_ids", "")).split("|")
        doc_ids = {event_to_doc[event_id] for event_id in event_ids if event_id in event_to_doc}
        for doc_id in doc_ids & canonical_ids:
            try:
                factor_values_by_doc[doc_id].append(float(factor.get("factor_value", 0.0)))
            except (TypeError, ValueError):
                pass
            rule_ids_by_doc[doc_id].update(
                value for value in str(factor.get("trigger_rule_ids", "")).split("|") if value
            )
    candidate_by_doc = _candidate_values_by_doc(live_analyses)
    documents_by_period: dict[str, list[dict[str, str]]] = defaultdict(list)
    for document in canonical:
        documents_by_period[period_for_date(document["publish_time"])["period_end"]].append(document)

    rows: list[dict[str, Any]] = []
    for period_end, period_documents in sorted(documents_by_period.items()):
        period = period_for_date(period_end)
        values: dict[str, float] = {}
        count = len(period_documents)
        values["coverage.document_count_log1p"] = math.log1p(count)
        values["coverage.independent_source_count_log1p"] = math.log1p(
            len({(row["source_name"], normalize_url(row.get("url", ""))) for row in period_documents})
        )
        event_rows = [event for document in period_documents for event in events_by_doc.get(document["doc_id"], [])]
        values["coverage.event_count_log1p"] = math.log1p(len(event_rows))
        values["coverage.ai_annotation_success_rate"] = (
            sum(document["doc_id"] in (ai_annotated_doc_ids or set()) for document in period_documents) / count
            if count
            else 0.0
        )
        source_counts = Counter(row["source_type"] for row in period_documents)
        for source_type in SOURCE_TYPES:
            values[f"source.share.{source_type}"] = source_counts[source_type] / count if count else 0.0
        for topic, keywords in TOPICS.items():
            topic_docs = sum(
                any(word in f"{document['title']} {document['content']}" for word in keywords)
                for document in period_documents
            )
            values[f"topic.share.{topic}"] = topic_docs / count if count else 0.0
        link_confidences = [
            float(link["confidence"])
            for document in period_documents
            for link in links_by_doc.get(document["doc_id"], [])
            if str(link.get("confidence", "")).strip()
        ]
        values["evidence.entity_confidence_mean"] = mean(link_confidences) if link_confidences else 0.0
        evidence_strengths = [
            float(event["evidence_strength"])
            for event in event_rows
            if str(event.get("evidence_strength", "")).strip()
        ]
        values["evidence.event_strength_mean"] = mean(evidence_strengths) if evidence_strengths else 0.0
        legacy_names = sorted(
            {
                row["predicate_name"]
                for document in period_documents
                for row in legacy_by_doc.get(document["doc_id"], [])
            }
        )
        for predicate_name in legacy_names:
            doc_values: list[float] = []
            doc_confidences: list[float] = []
            for document in period_documents:
                matched = [
                    row
                    for row in legacy_by_doc.get(document["doc_id"], [])
                    if row["predicate_name"] == predicate_name
                ]
                if not matched:
                    continue
                active = [
                    1.0
                    if str(row["value"]).lower() == "true"
                    else 0.0
                    if str(row["value"]).lower() == "false"
                    else float(row["value"])
                    for row in matched
                ]
                doc_values.append(max(active))
                doc_confidences.append(mean(float(row["confidence"]) for row in matched))
            values[f"legacy_predicate.mean.{predicate_name}"] = mean(doc_values) if doc_values else 0.0
            values[f"legacy_predicate.confidence.{predicate_name}"] = (
                mean(doc_confidences) if doc_confidences else 0.0
            )
        for predicate_name in MACRO_PREDICATES:
            predicate_rows = [
                row
                for document in period_documents
                for row in predicates_by_doc.get(document["doc_id"], [])
                if row["predicate_name"] == predicate_name
            ]
            active = [row for row in predicate_rows if _active_macro_score(row) != 0.0]
            values[f"macro_predicate.hit_rate.{predicate_name}"] = len(active) / count if count else 0.0
            values[f"macro_predicate.net.{predicate_name}"] = (
                sum(_active_macro_score(row) for row in active) / count if count else 0.0
            )
        candidate_values = [
            value
            for document in period_documents
            for value in [
                *candidate_by_doc.get(document["doc_id"], []),
                *factor_values_by_doc.get(document["doc_id"], []),
            ]
        ]
        values["candidate.mean"] = mean(candidate_values) if candidate_values else 0.0
        values["candidate.std"] = pstdev(candidate_values) if len(candidate_values) > 1 else 0.0
        values["candidate.p25"] = _quantile(candidate_values, 0.25)
        values["candidate.p50"] = median(candidate_values) if candidate_values else 0.0
        values["candidate.p75"] = _quantile(candidate_values, 0.75)
        values["candidate.positive_share"] = (
            sum(value > 0 for value in candidate_values) / len(candidate_values) if candidate_values else 0.0
        )
        triggered_rule_ids = {
            rule_id
            for document in period_documents
            for rule_id in rule_ids_by_doc.get(document["doc_id"], set())
        }
        values["frozen_rule.unique_rule_count_log1p"] = math.log1p(len(triggered_rule_ids))
        values["frozen_rule.supporting_document_share"] = (
            sum(bool(rule_ids_by_doc.get(document["doc_id"])) for document in period_documents) / count
            if count
            else 0.0
        )
        for feature_name, feature_value in sorted(values.items()):
            rows.append(
                {
                    **period,
                    "route": "predicate_baseline",
                    "feature_name": feature_name,
                    "feature_value": f"{feature_value:.8f}",
                    "document_count": str(count),
                }
            )
    audit = {
        "input_document_count": len(documents),
        "canonical_document_count": len(canonical),
        "dropped_document_count": len(dropped),
        "dropped_documents": dropped,
        "period_count": len(documents_by_period),
    }
    return rows, audit


def feature_matrix(rows: list[dict[str, Any]], route: str) -> tuple[list[str], list[str], list[list[float]]]:
    """Pivot long monthly feature rows into period x feature form."""
    filtered = [row for row in rows if row["route"] == route]
    features = sorted({str(row["feature_name"]) for row in filtered})
    periods = sorted({str(row["period_end"]) for row in filtered})
    values = {(str(row["period_end"]), str(row["feature_name"])): float(row["feature_value"]) for row in filtered}
    matrix = [[values.get((period, feature), 0.0) for feature in features] for period in periods]
    return periods, features, matrix
