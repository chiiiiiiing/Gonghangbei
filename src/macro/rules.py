"""Historical, AI-dynamic and hybrid macro rule feature routes."""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from statistics import mean, median
from typing import Any

from src.macro.schema import MACRO_PREDICATES, advance_period_end, period_for_date, split_for_period


MIN_RULE_DOCUMENTS = 12
MIN_RULE_PERIODS = 6
MAX_RULE_TERMS = 3
MAX_HISTORICAL_RULES = 12


def _correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 3 or len(left) != len(right):
        return 0.0
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_var = sum((x - left_mean) ** 2 for x in left)
    right_var = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator else 0.0


def _active(row: dict[str, Any]) -> bool:
    return str(row.get("value", "")).lower() == "true" and bool(str(row.get("evidence_text", "")).strip())


def _predicate_score(row: dict[str, Any]) -> float:
    if not _active(row):
        return 0.0
    return float(row["direction"]) * float(row["intensity"]) * float(row["confidence"])


def _target_by_period(targets: list[dict[str, Any]], split: str = "train") -> dict[str, float]:
    return {
        str(row["period_end"]): float(row["target_value"])
        for row in targets
        if split_for_period(str(row["period_end"])) == split
    }


def _rolling_rule_improvement(
    signal: dict[str, float],
    targets: list[dict[str, Any]],
    minimum_history: int = 12,
) -> float:
    """Expanding-window MAE improvement over persistence inside Train only."""
    train_targets = sorted(
        (
            {
                "period_end": str(row["period_end"]),
                "release_date": str(row["release_date"]),
                "target": float(row["target_value"]),
            }
            for row in targets
            if split_for_period(str(row["period_end"])) == "train"
        ),
        key=lambda row: row["period_end"],
    )
    errors: list[tuple[float, float]] = []
    for current in train_targets:
        period = current["period_end"]
        if period not in signal:
            continue
        available = [row for row in train_targets if row["release_date"] <= period]
        aligned = [row for row in available if row["period_end"] in signal]
        if len(aligned) < minimum_history or not available:
            continue
        x = [signal[row["period_end"]] for row in aligned]
        y = [row["target"] for row in aligned]
        x_mean, y_mean = mean(x), mean(y)
        variance = sum((value - x_mean) ** 2 for value in x)
        slope = (
            sum((left - x_mean) * (right - y_mean) for left, right in zip(x, y)) / variance
            if variance
            else 0.0
        )
        intercept = y_mean - slope * x_mean
        predicted = intercept + slope * signal[period]
        persistence = available[-1]["target"]
        errors.append((abs(predicted - current["target"]), abs(persistence - current["target"])))
    if not errors:
        return 0.0
    text_mae = mean(row[0] for row in errors)
    persistence_mae = mean(row[1] for row in errors)
    return persistence_mae - text_mae


def _document_predicates(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        result[str(row["doc_id"])][str(row["predicate_name"])] = row
    return result


def _year_stability(period_values: dict[str, float], target_values: dict[str, float], expected_sign: int) -> float:
    years = sorted({period[:4] for period in period_values if period in target_values})
    checks: list[bool] = []
    for year in years:
        periods = sorted(period for period in period_values if period.startswith(year) and period in target_values)
        if len(periods) < 3:
            continue
        corr = _correlation([period_values[p] for p in periods], [target_values[p] for p in periods])
        checks.append(corr * expected_sign > 0)
    return sum(checks) / len(checks) if checks else 0.0


def _candidate_signal(
    conditions: tuple[str, ...],
    by_document: dict[str, dict[str, dict[str, Any]]],
) -> tuple[dict[str, float], set[str], set[str], int, int, float]:
    matched_documents: set[str] = set()
    source_periods: set[str] = set()
    scores_by_source_period: dict[str, list[float]] = defaultdict(list)
    lags: list[int] = []
    directions: list[int] = []
    confidences: list[float] = []
    for doc_id, predicate_map in by_document.items():
        if not all(name in predicate_map and _active(predicate_map[name]) for name in conditions):
            continue
        matched_documents.add(doc_id)
        rows = [predicate_map[name] for name in conditions]
        source_period = str(rows[0]["period_end"])
        source_periods.add(source_period)
        lags.extend(int(row["expected_lag_months"]) for row in rows)
        directions.extend(int(row["direction"]) for row in rows)
        confidences.extend(float(row["confidence"]) for row in rows)
        magnitude = mean(abs(_predicate_score(row)) for row in rows)
        direction_sum = sum(int(row["direction"]) for row in rows)
        direction = 1 if direction_sum > 0 else -1 if direction_sum < 0 else 0
        scores_by_source_period[source_period].append(direction * magnitude)
    lag = round(median(lags)) if lags else 0
    direction_sum = sum(directions)
    expected_direction = 1 if direction_sum > 0 else -1 if direction_sum < 0 else 0
    evidence = mean(confidences) if confidences else 0.0
    shifted: dict[str, float] = defaultdict(float)
    for source_period, values in scores_by_source_period.items():
        shifted[advance_period_end(source_period, lag)] += mean(values)
    return dict(shifted), matched_documents, source_periods, expected_direction, lag, evidence


def learn_historical_rules(
    macro_predicates: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    *,
    min_documents: int = MIN_RULE_DOCUMENTS,
    min_periods: int = MIN_RULE_PERIODS,
) -> list[dict[str, Any]]:
    """Learn and freeze macro predicate combinations using Train only."""
    train_rows = [
        row for row in macro_predicates if split_for_period(str(row["period_end"])) == "train"
    ]
    by_document = _document_predicates(train_rows)
    target_values = _target_by_period(targets, "train")
    active_names = [
        name
        for name in MACRO_PREDICATES
        if any(_active(predicate_map.get(name, {})) for predicate_map in by_document.values())
    ]
    candidates: list[dict[str, Any]] = []
    for term_count in range(1, MAX_RULE_TERMS + 1):
        for conditions in itertools.combinations(active_names, term_count):
            signal, documents, periods, expected_direction, lag, evidence = _candidate_signal(conditions, by_document)
            aligned = sorted(set(signal) & set(target_values))
            if len(documents) < min_documents or len(periods) < min_periods or len(aligned) < 3:
                continue
            corr = _correlation([signal[period] for period in aligned], [target_values[period] for period in aligned])
            rolling_improvement = _rolling_rule_improvement(signal, targets)
            if rolling_improvement <= 0:
                continue
            empirical_direction = 1 if corr > 0 else -1 if corr < 0 else 0
            direction = expected_direction or empirical_direction
            stability = _year_stability(signal, target_values, empirical_direction or direction)
            coverage = 0.5 * min(len(documents) / 40.0, 1.0) + 0.5 * min(len(periods) / 24.0, 1.0)
            complexity_penalty = 0.04 * max(term_count - 1, 0)
            score = (
                0.25 * abs(corr)
                + 0.20 * (1.0 / (1.0 + math.exp(-rolling_improvement / 0.5)))
                + 0.25 * stability
                + 0.20 * coverage
                + 0.10 * evidence
                - complexity_penalty
            )
            candidates.append(
                {
                    "conditions": conditions,
                    "condition": " AND ".join(conditions),
                    "direction": direction,
                    "expected_lag_months": lag,
                    "independent_document_count": len(documents),
                    "independent_period_count": len(periods),
                    "train_correlation": corr,
                    "rolling_mae_improvement": rolling_improvement,
                    "stability": stability,
                    "score": min(max(score, 0.0), 1.0),
                    "event_set": frozenset(documents),
                    "status": "qualified",
                }
            )
    candidates.sort(key=lambda row: (float(row["score"]), len(row["event_set"])), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_sets: set[frozenset[str]] = set()
    for candidate in candidates:
        if candidate["event_set"] in seen_sets:
            continue
        seen_sets.add(candidate["event_set"])
        selected.append(candidate)
        if len(selected) >= MAX_HISTORICAL_RULES:
            break
    return [
        {
            "rule_id": f"MR{index:03d}",
            "route": "historical_rules",
            "condition": row["condition"],
            "direction": str(row["direction"]),
            "expected_lag_months": str(row["expected_lag_months"]),
            "independent_document_count": str(row["independent_document_count"]),
            "independent_period_count": str(row["independent_period_count"]),
            "train_correlation": f"{row['train_correlation']:.6f}",
            "rolling_mae_improvement": f"{row['rolling_mae_improvement']:.6f}",
            "stability": f"{row['stability']:.6f}",
            "score": f"{row['score']:.6f}",
            "status": row["status"],
        }
        for index, row in enumerate(selected, start=1)
    ]


def historical_rule_features(
    rules: list[dict[str, Any]],
    macro_predicates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_document = _document_predicates(macro_predicates)
    rows: list[dict[str, Any]] = []
    for rule in rules:
        conditions = tuple(part.strip() for part in str(rule["condition"]).split("AND"))
        signal, documents, _periods, _direction, _lag, _evidence = _candidate_signal(conditions, by_document)
        support_by_period: dict[str, int] = defaultdict(int)
        for doc_id in documents:
            source_period = str(by_document[doc_id][conditions[0]]["period_end"])
            target_period = advance_period_end(source_period, int(rule["expected_lag_months"]))
            support_by_period[target_period] += 1
        for period_end, value in sorted(signal.items()):
            rows.append(
                {
                    **period_for_date(period_end),
                    "period_end": period_end,
                    "route": "historical_rules",
                    "feature_name": f"historical_rule.{rule['rule_id']}",
                    "feature_value": f"{value:.8f}",
                    "document_count": str(support_by_period[period_end]),
                }
            )
    return rows


def ai_dynamic_rule_features(ai_analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate AI rules by stable condition/direction/lag signatures."""
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    document_support: dict[tuple[str, str], set[str]] = defaultdict(set)
    for analysis in ai_analyses:
        if not analysis.get("used") or not isinstance(analysis.get("result"), dict):
            continue
        doc_id = str(analysis.get("doc_id", ""))
        period_end = str(analysis.get("period_end", ""))
        for rule in analysis["result"].get("candidate_rules", []):
            lag = int(rule["expected_lag_months"])
            target_period = advance_period_end(period_end, lag)
            signature = (
                f"{rule['condition_signature']}|direction={rule['direction']}|lag={rule['lag_bucket']}"
            )
            key = (target_period, signature)
            grouped[key].append(float(rule["direction"]) * float(rule["intensity"]) * float(rule["confidence"]))
            document_support[key].add(doc_id)
    return [
        {
            **period_for_date(period_end),
            "period_end": period_end,
            "route": "ai_dynamic_rules",
            "feature_name": f"ai_rule.{signature}",
            "feature_value": f"{mean(values):.8f}",
            "document_count": str(len(document_support[(period_end, signature)])),
        }
        for (period_end, signature), values in sorted(grouped.items())
    ]


def combine_route_features(
    base_rows: list[dict[str, Any]],
    historical_rows: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
    ai_weight: float = 0.25,
) -> list[dict[str, Any]]:
    """Build comparable route datasets from one shared base feature table."""
    output = list(base_rows)
    base_by_period: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in base_rows:
        base_by_period[str(row["period_end"])].append(row)

    def clone_base(route: str) -> None:
        for row in base_rows:
            output.append({**row, "route": route})

    clone_base("historical_rules")
    clone_base("ai_dynamic_rules")
    clone_base("hybrid_rules")
    output.extend(historical_rows)
    output.extend(ai_rows)
    output.extend({**row, "route": "hybrid_rules"} for row in historical_rows)
    output.extend(
        {
            **row,
            "route": "hybrid_rules",
            "feature_name": f"hybrid.{row['feature_name']}",
            "feature_value": f"{float(row['feature_value']) * ai_weight:.8f}",
        }
        for row in ai_rows
    )
    return output
