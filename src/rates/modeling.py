"""Low-capacity, purged rolling models for five-day yield direction."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from src.rates.schema import (
    FACTOR_LABELS,
    FACTOR_NAMES,
    FLAT_THRESHOLD_BP,
    HORIZON_TRADING_DAYS,
    MINIMUM_TRAIN_DAYS,
    RULE_LOGIT_WEIGHT,
    ROLLING_TRAIN_DAYS,
    TEXT_OVERLAY_WEIGHT,
    direction_label,
)


LABELS = ("down", "flat", "up")
ROUTES = ("market_baseline", "text_only", "fusion", "fusion_rules")
ROUTE_LABELS = {
    "market_baseline": "仅市场数据",
    "text_only": "仅文本因子",
    "fusion": "市场数据+文本因子+结构化宏观",
    "fusion_rules": "市场数据+文本因子+结构化宏观+规则增强",
}
MARKET_FEATURES = (
    "yield_change_1d_bp", "yield_change_5d_bp", "yield_change_20d_bp",
    "yield_volatility_20d_bp", "fdr007_level", "fdr007_change_1d_bp",
    "fdr007_gap_20d_bp",
)
STRUCTURED_FEATURES = (
    "cpi_yoy", "ppi_yoy", "pmi_manufacturing", "afre_flow",
    "afre_rmb_loans", "afre_government_bonds", "mlf_amount", "mlf_rate",
    "government_bond_issuance",
)


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values)
    exp = np.exp(shifted)
    total = float(exp.sum())
    return exp / total if total else np.full(len(values), 1 / len(values))


def blend_with_market_baseline(
    baseline: dict[str, float], enhanced: dict[str, float], weight: float = TEXT_OVERLAY_WEIGHT,
) -> dict[str, float]:
    """Apply a frozen, conservative text overlay to market probabilities."""
    if not 0.0 <= weight <= 1.0:
        raise ValueError("文本叠加权重必须位于0到1之间")
    values = {
        label: (1.0 - weight) * float(baseline[label]) + weight * float(enhanced[label])
        for label in LABELS
    }
    total = sum(values.values())
    return {label: round(values[label] / total, 8) for label in LABELS}


def apply_rule_prior(
    probabilities: dict[str, float], pressure: float, weight: float = RULE_LOGIT_WEIGHT,
) -> dict[str, float]:
    """Tilt up/down log probabilities with the frozen economic rule pressure."""
    bounded_pressure = max(min(float(pressure), 1.0), -1.0)
    logits = np.log(np.asarray([max(float(probabilities[label]), 1e-12) for label in LABELS]))
    logits[LABELS.index("down")] -= weight * bounded_pressure
    logits[LABELS.index("up")] += weight * bounded_pressure
    adjusted = _softmax(logits)
    return {label: round(float(adjusted[index]), 8) for index, label in enumerate(LABELS)}


def _fit_predict(
    train_x: list[list[float]], train_y: list[str], sample: list[float]
) -> tuple[dict[str, float], np.ndarray, np.ndarray, np.ndarray]:
    matrix = np.asarray(train_x, dtype=float)
    target = np.zeros((len(train_y), len(LABELS)), dtype=float)
    for row_index, label in enumerate(train_y):
        target[row_index, LABELS.index(label)] = 1.0
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-8] = 1.0
    standardized = (matrix - means) / scales
    design = np.column_stack([np.ones(len(standardized)), standardized])
    penalty = np.eye(design.shape[1])
    penalty[0, 0] = 0.0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ target)
    standardized_sample = (np.asarray(sample, dtype=float) - means) / scales
    point = np.concatenate([[1.0], standardized_sample])
    probabilities = _softmax(point @ weights)
    return (
        {label: round(float(probabilities[index]), 8) for index, label in enumerate(LABELS)},
        weights,
        standardized_sample,
        scales,
    )


def feature_names(route: str) -> list[str]:
    text_names = [f"text_{name}" for name in FACTOR_NAMES]
    if route == "market_baseline":
        return list(MARKET_FEATURES)
    if route == "text_only":
        return text_names
    if route == "fusion":
        return list(MARKET_FEATURES) + text_names + [f"structured_{name}" for name in STRUCTURED_FEATURES]
    if route == "fusion_rules":
        return list(MARKET_FEATURES) + text_names + [f"structured_{name}" for name in STRUCTURED_FEATURES] + ["rule_pressure"]
    raise ValueError(f"未知模型路线：{route}")


def _lag(values: list[float], index: int, days: int) -> float:
    return values[max(index - days, 0)]


def _feature_rows(
    market_rows: list[dict[str, Any]],
    factors_by_date: dict[str, dict[str, float]],
    rule_pressure_by_date: dict[str, float],
    route: str,
) -> list[list[float]]:
    yields = [float(row["cgb_10y_yield"]) for row in market_rows]
    liquidity = [float(row["dr007_proxy"]) for row in market_rows]
    result: list[list[float]] = []
    for index, row in enumerate(market_rows):
        window_yields = yields[max(0, index - 19):index + 1]
        window_liquidity = liquidity[max(0, index - 19):index + 1]
        yield_changes = np.diff(np.asarray(window_yields, dtype=float)) * 100
        market = [
            (yields[index] - _lag(yields, index, 1)) * 100,
            (yields[index] - _lag(yields, index, 5)) * 100,
            (yields[index] - _lag(yields, index, 20)) * 100,
            float(yield_changes.std()) if len(yield_changes) else 0.0,
            liquidity[index],
            (liquidity[index] - _lag(liquidity, index, 1)) * 100,
            (liquidity[index] - float(np.mean(window_liquidity))) * 100,
        ]
        factor_map = factors_by_date.get(str(row["trade_date"]), {})
        text = [float(factor_map.get(name, 0.0)) for name in FACTOR_NAMES]
        structured_map = factor_map.get("__structured__", {})
        structured = [float(structured_map.get(name, 0.0)) for name in STRUCTURED_FEATURES]
        if route == "market_baseline":
            features = market
        elif route == "text_only":
            features = text
        elif route == "fusion":
            features = market + text + structured
        elif route == "fusion_rules":
            features = market + text + structured + [float(rule_pressure_by_date.get(str(row["trade_date"]), 0.0))]
        else:
            raise ValueError(f"未知模型路线：{route}")
        result.append(features)
    return result


def labels_for_market(
    market_rows: list[dict[str, Any]],
    horizon_trading_days: int = HORIZON_TRADING_DAYS,
    threshold_bp: float = FLAT_THRESHOLD_BP,
) -> list[str | None]:
    if horizon_trading_days < 1:
        raise ValueError("预测窗口至少为1个交易日")
    if threshold_bp <= 0:
        raise ValueError("震荡阈值必须大于0bp")
    labels: list[str | None] = []
    for index, row in enumerate(market_rows):
        future_index = index + horizon_trading_days
        if future_index >= len(market_rows):
            labels.append(None)
            continue
        delta_bp = (float(market_rows[future_index]["cgb_10y_yield"]) - float(row["cgb_10y_yield"])) * 100
        labels.append(direction_label(delta_bp, threshold_bp))
    return labels


def _auc(actual: list[str], probabilities: list[dict[str, float]], label: str) -> float | None:
    binary = [1 if item == label else 0 for item in actual]
    positive = sum(binary)
    negative = len(binary) - positive
    if positive == 0 or negative == 0:
        return None
    ordered = sorted(enumerate(probabilities), key=lambda pair: pair[1][label])
    ranks = [0.0] * len(ordered)
    position = 0
    while position < len(ordered):
        end = position + 1
        score = ordered[position][1][label]
        while end < len(ordered) and ordered[end][1][label] == score:
            end += 1
        average_rank = (position + 1 + end) / 2
        for cursor in range(position, end):
            ranks[ordered[cursor][0]] = average_rank
        position = end
    rank_sum = sum(rank for rank, value in zip(ranks, binary) if value)
    return (rank_sum - positive * (positive + 1) / 2) / (positive * negative)


def _metrics(actual: list[str], predicted: list[str], probabilities: list[dict[str, float]]) -> dict[str, Any]:
    if not actual:
        return {
            "observations": 0, "accuracy": None, "macro_precision": None,
            "macro_recall": None, "macro_f1": None, "macro_auc_ovr": None,
            "brier": None, "confusion_matrix": {}, "per_class": {},
        }
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    for truth, guess in zip(actual, predicted):
        matrix[truth][guess] += 1
    per_class: dict[str, dict[str, float | int | None]] = {}
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        fn = sum(matrix[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        auc = _auc(actual, probabilities, label)
        per_class[label] = {
            "support": sum(1 for item in actual if item == label),
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "auc_ovr": round(auc, 4) if auc is not None else None,
        }
    brier = sum(
        sum((prob[label] - (1.0 if label == truth else 0.0)) ** 2 for label in LABELS) / len(LABELS)
        for truth, prob in zip(actual, probabilities)
    ) / len(actual)
    auc_values = [float(row["auc_ovr"]) for row in per_class.values() if row["auc_ovr"] is not None]
    return {
        "observations": len(actual),
        "accuracy": round(sum(a == p for a, p in zip(actual, predicted)) / len(actual), 4),
        "macro_precision": round(sum(float(row["precision"]) for row in per_class.values()) / len(LABELS), 4),
        "macro_recall": round(sum(float(row["recall"]) for row in per_class.values()) / len(LABELS), 4),
        "macro_f1": round(sum(float(row["f1"]) for row in per_class.values()) / len(LABELS), 4),
        "macro_auc_ovr": round(sum(auc_values) / len(auc_values), 4) if auc_values else None,
        "brier": round(brier, 4),
        "confusion_matrix": matrix,
        "actual_distribution": dict(Counter(actual)),
        "per_class": per_class,
    }


def _period(trade_date: str) -> str:
    if trade_date < "2023-01-01":
        return "discovery_2018_2022"
    if trade_date < "2025-01-01":
        return "validation_2023_2024"
    return "retrospective_holdout_2025_latest"


def _calibration(actual: list[str], predicted: list[str], probabilities: list[dict[str, float]]) -> list[dict[str, Any]]:
    bins = ((0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.75), (0.75, 1.01))
    rows: list[dict[str, Any]] = []
    for lower, upper in bins:
        members = [index for index, prob in enumerate(probabilities) if lower <= max(prob.values()) < upper]
        if not members:
            continue
        rows.append({
            "confidence_range": f"{lower:.2f}-{min(upper, 1.0):.2f}",
            "observations": len(members),
            "mean_confidence": round(sum(max(probabilities[i].values()) for i in members) / len(members), 4),
            "accuracy": round(sum(actual[i] == predicted[i] for i in members) / len(members), 4),
        })
    return rows


def evaluate_route(
    market_rows: list[dict[str, Any]],
    factors_by_date: dict[str, dict[str, float]],
    rule_pressure_by_date: dict[str, float],
    route: str,
    minimum_train: int = MINIMUM_TRAIN_DAYS,
    rolling_window: int = ROLLING_TRAIN_DAYS,
    evaluation_stride: int | None = None,
    horizon_trading_days: int = HORIZON_TRADING_DAYS,
    threshold_bp: float = FLAT_THRESHOLD_BP,
) -> dict[str, Any]:
    # Rules are applied as an explicit prior after the statistical fusion fit;
    # they are not duplicated as a highly collinear fitted feature.
    statistical_route = "fusion" if route == "fusion_rules" else route
    features = _feature_rows(market_rows, factors_by_date, rule_pressure_by_date, statistical_route)
    baseline_features = (
        _feature_rows(market_rows, factors_by_date, rule_pressure_by_date, "market_baseline")
        if route in {"fusion", "fusion_rules"} else None
    )
    labels = labels_for_market(market_rows, horizon_trading_days, threshold_bp)
    actual: list[str] = []
    predicted: list[str] = []
    probabilities: list[dict[str, float]] = []
    timeline: list[dict[str, Any]] = []
    evaluation_stride = horizon_trading_days if evaluation_stride is None else max(1, evaluation_stride)
    first_prediction = minimum_train + horizon_trading_days
    last_labeled = len(market_rows) - horizon_trading_days
    for index in range(first_prediction, last_labeled, evaluation_stride):
        earliest = max(0, index - rolling_window)
        train_indices = [
            item for item in range(earliest, index)
            if labels[item] is not None and item + horizon_trading_days <= index
        ]
        if len(train_indices) < minimum_train:
            continue
        raw_probs, _weights, _point, _scales = _fit_predict(
            [features[item] for item in train_indices], [str(labels[item]) for item in train_indices], features[index]
        )
        if baseline_features is not None:
            baseline_probs, _base_weights, _base_point, _base_scales = _fit_predict(
                [baseline_features[item] for item in train_indices],
                [str(labels[item]) for item in train_indices],
                baseline_features[index],
            )
            probs = blend_with_market_baseline(baseline_probs, raw_probs)
        else:
            probs = raw_probs
        current_rule_pressure = float(rule_pressure_by_date.get(str(market_rows[index]["trade_date"]), 0.0))
        if route == "fusion_rules":
            probs = apply_rule_prior(probs, current_rule_pressure)
        prediction = max(LABELS, key=lambda label: probs[label])
        truth = str(labels[index])
        actual.append(truth)
        predicted.append(prediction)
        probabilities.append(probs)
        timeline.append({
            "as_of": market_rows[index]["trade_date"], "actual": truth, "predicted": prediction,
            "probabilities": probs, "correct": truth == prediction,
            "train_origin_start": market_rows[train_indices[0]]["trade_date"],
            "train_origin_end": market_rows[train_indices[-1]]["trade_date"],
            "label_known_through": market_rows[train_indices[-1] + horizon_trading_days]["trade_date"],
            "train_observations": len(train_indices), "period": _period(str(market_rows[index]["trade_date"])),
            "text_overlay_weight": TEXT_OVERLAY_WEIGHT if baseline_features is not None else None,
            "rule_pressure_applied": current_rule_pressure if route == "fusion_rules" else 0.0,
        })
    overall = _metrics(actual, predicted, probabilities)
    period_metrics: list[dict[str, Any]] = []
    for period in (
        "discovery_2018_2022",
        "validation_2023_2024",
        "retrospective_holdout_2025_latest",
    ):
        indices = [i for i, row in enumerate(timeline) if row["period"] == period]
        metrics = _metrics(
            [actual[i] for i in indices], [predicted[i] for i in indices], [probabilities[i] for i in indices]
        )
        period_metrics.append({"period": period, **metrics})
    ranked = sorted(timeline, key=lambda row: max(row["probabilities"].values()), reverse=True)
    examples = {
        "correct": [row for row in ranked if row["correct"]][:3],
        "incorrect": [row for row in ranked if not row["correct"]][:3],
    }
    return {
        "route": route, "route_label": ROUTE_LABELS[route], **overall,
        "period_metrics": period_metrics,
        "calibration": _calibration(actual, predicted, probabilities),
        "examples": examples, "timeline": timeline,
        "training_policy": {
            "kind": "purged_non_overlapping_rolling_window" if evaluation_stride >= horizon_trading_days else "purged_rolling_window",
            "rolling_window_days": rolling_window,
            "minimum_train_days": minimum_train, "label_embargo_days": horizon_trading_days,
            "evaluation_stride_days": evaluation_stride, "horizon_trading_days": horizon_trading_days,
            "threshold_bp": threshold_bp, "shuffle": False,
            "text_overlay_weight": TEXT_OVERLAY_WEIGHT if baseline_features is not None else None,
            "rule_logit_weight": RULE_LOGIT_WEIGHT if route == "fusion_rules" else None,
            "enhancement_policy": (
                "market_anchored_text_overlay_then_rule_prior"
                if route == "fusion_rules" else
                "market_anchored_text_overlay" if route == "fusion" else "standalone_fit"
            ),
        },
    }


def live_probabilities(
    market_rows: list[dict[str, Any]],
    factors_by_date: dict[str, dict[str, float]],
    rule_pressure_by_date: dict[str, float],
    route: str = "fusion_rules",
    minimum_train: int = MINIMUM_TRAIN_DAYS,
    rolling_window: int = ROLLING_TRAIN_DAYS,
) -> dict[str, Any]:
    labels = labels_for_market(market_rows)
    statistical_route = "fusion" if route == "fusion_rules" else route
    features = _feature_rows(market_rows, factors_by_date, rule_pressure_by_date, statistical_route)
    baseline_features = (
        _feature_rows(market_rows, factors_by_date, rule_pressure_by_date, "market_baseline")
        if route in {"fusion", "fusion_rules"} else None
    )
    latest = len(market_rows) - 1
    earliest = max(0, latest - rolling_window)
    train_indices = [
        index for index in range(earliest, latest)
        if labels[index] is not None and index + HORIZON_TRADING_DAYS <= latest
    ]
    if len(train_indices) < minimum_train:
        return {
            "data_sufficient": False,
            "probabilities": {label: round(1 / len(LABELS), 8) for label in LABELS},
            "predicted_label": "insufficient", "feature_contributions": [],
            "reason": f"至少需要{minimum_train + HORIZON_TRADING_DAYS}个交易日，当前仅{len(market_rows)}个",
        }
    raw_probs, weights, point, _scales = _fit_predict(
        [features[index] for index in train_indices], [str(labels[index]) for index in train_indices], features[latest]
    )
    baseline_fit = None
    text_overlay_delta = {label: 0.0 for label in LABELS}
    if baseline_features is not None:
        baseline_fit = _fit_predict(
            [baseline_features[index] for index in train_indices],
            [str(labels[index]) for index in train_indices], baseline_features[latest],
        )
        probs = blend_with_market_baseline(baseline_fit[0], raw_probs)
        text_overlay_delta = {
            label: round(probs[label] - baseline_fit[0][label], 8) for label in LABELS
        }
    else:
        probs = raw_probs
    latest_day = str(market_rows[latest]["trade_date"])
    current_rule_pressure = float(rule_pressure_by_date.get(latest_day, 0.0))
    before_rule_probs = dict(probs)
    if route == "fusion_rules":
        probs = apply_rule_prior(probs, current_rule_pressure)
    rule_prior_delta = {
        label: round(probs[label] - before_rule_probs[label], 8) for label in LABELS
    }
    predicted = max(LABELS, key=lambda label: probs[label])
    label_index = LABELS.index(predicted)
    contribution_map: dict[str, float] = {}
    raw_scale = TEXT_OVERLAY_WEIGHT if baseline_fit is not None else 1.0
    for index, name in enumerate(feature_names(statistical_route)):
        contribution_map[name] = contribution_map.get(name, 0.0) + raw_scale * float(
            point[index] * weights[index + 1, label_index]
        )
    if baseline_fit is not None:
        _base_probs, base_weights, base_point, _base_scales = baseline_fit
        for index, name in enumerate(feature_names("market_baseline")):
            contribution_map[name] = contribution_map.get(name, 0.0) + (1.0 - TEXT_OVERLAY_WEIGHT) * float(
                base_point[index] * base_weights[index + 1, label_index]
            )
    if route == "fusion_rules":
        # Signed value describes the prior's directional tilt even when the
        # final class remains flat; the exact probability movement is returned
        # separately below.
        contribution_map["rule_pressure"] = RULE_LOGIT_WEIGHT * current_rule_pressure
    contributions = []
    for name, value in contribution_map.items():
        display = FACTOR_LABELS.get(name.removeprefix("text_"), name)
        contributions.append({"feature": name, "label": display, "contribution": round(value, 6)})
    contributions.sort(key=lambda row: abs(float(row["contribution"])), reverse=True)
    return {
        "data_sufficient": True, "probabilities": probs, "predicted_label": predicted,
        "train_observations": len(train_indices), "train_start": market_rows[train_indices[0]]["trade_date"],
        "train_origin_end": market_rows[train_indices[-1]]["trade_date"],
        "label_known_through": market_rows[train_indices[-1] + HORIZON_TRADING_DAYS]["trade_date"],
        "feature_contributions": contributions,
        "text_overlay_weight": TEXT_OVERLAY_WEIGHT if baseline_fit is not None else None,
        "rule_logit_weight": RULE_LOGIT_WEIGHT if route == "fusion_rules" else None,
        "rule_pressure_applied": current_rule_pressure if route == "fusion_rules" else 0.0,
        "probability_decomposition": {
            "market_baseline": baseline_fit[0] if baseline_fit is not None else None,
            "text_model": raw_probs,
            "after_text_overlay": before_rule_probs,
            "text_overlay_delta": text_overlay_delta,
            "after_rule_prior": probs,
            "rule_prior_delta": rule_prior_delta,
        },
        "reason": "",
    }


def paired_block_bootstrap(
    baseline: dict[str, Any], enhanced: dict[str, Any], iterations: int = 2000, block_days: int = 20
) -> dict[str, Any]:
    baseline_by_date = {row["as_of"]: row for row in baseline.get("timeline", [])}
    enhanced_by_date = {row["as_of"]: row for row in enhanced.get("timeline", [])}
    dates = sorted(set(baseline_by_date) & set(enhanced_by_date))
    differences = np.asarray([
        float(enhanced_by_date[day]["correct"]) - float(baseline_by_date[day]["correct"])
        for day in dates
    ])
    # Formal predictions are five trading days apart. Convert calendar intent
    # into evaluation observations so a 20-day block remains 20 trading days.
    block_observations = max(1, int(np.ceil(block_days / HORIZON_TRADING_DAYS)))
    if len(differences) < block_observations:
        return {
            "method": "paired_moving_block_bootstrap", "block_days": block_days,
            "block_observations": block_observations, "observations": len(differences),
            "accuracy_difference": 0.0, "ci_lower_95": None, "ci_upper_95": None,
            "stable": False,
        }
    starts = np.arange(max(len(differences) - block_observations + 1, 1))
    rng = np.random.default_rng(20260902)
    estimates = []
    blocks_needed = int(np.ceil(len(differences) / block_observations))
    for _ in range(iterations):
        sample = np.concatenate([
            differences[start:start + block_observations]
            for start in rng.choice(starts, blocks_needed)
        ])[:len(differences)]
        estimates.append(float(sample.mean()))
    lower, upper = np.quantile(estimates, [0.025, 0.975])
    point = float(differences.mean())
    return {
        "method": "paired_moving_block_bootstrap", "iterations": iterations,
        "block_days": block_days, "block_observations": block_observations,
        "observations": len(differences),
        "accuracy_difference": round(point, 6), "ci_lower_95": round(float(lower), 6),
        "ci_upper_95": round(float(upper), 6), "stable": bool(lower > 0),
    }
