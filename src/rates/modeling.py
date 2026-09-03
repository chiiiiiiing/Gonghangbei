"""Low-capacity, time-aware probability models for five-day yield direction."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np

from src.rates.schema import FACTOR_NAMES, HORIZON_TRADING_DAYS, direction_label


LABELS = ("down", "flat", "up")
ROUTES = ("market_baseline", "text_only", "fusion", "fusion_rules")


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values)
    exp = np.exp(shifted)
    total = float(exp.sum())
    return exp / total if total else np.full(len(values), 1 / len(values))


def _fit_predict(train_x: list[list[float]], train_y: list[str], sample: list[float]) -> dict[str, float]:
    matrix = np.asarray(train_x, dtype=float)
    target = np.zeros((len(train_y), len(LABELS)), dtype=float)
    for row_index, label in enumerate(train_y):
        target[row_index, LABELS.index(label)] = 1.0
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-8] = 1.0
    standardized = (matrix - means) / scales
    design = np.column_stack([np.ones(len(standardized)), standardized])
    penalty = np.eye(design.shape[1]) * 1.0
    penalty[0, 0] = 0.0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ target)
    point = np.concatenate([[1.0], (np.asarray(sample, dtype=float) - means) / scales])
    probabilities = _softmax(point @ weights)
    return {label: round(float(probabilities[index]), 6) for index, label in enumerate(LABELS)}


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
        prev = max(index - 1, 0)
        prev5 = max(index - 5, 0)
        market = [
            (yields[index] - yields[prev]) * 100,
            (yields[index] - yields[prev5]) * 100,
            liquidity[index],
            (liquidity[index] - liquidity[prev]) * 100,
        ]
        factor_map = factors_by_date.get(str(row["trade_date"]), {})
        text = [float(factor_map.get(name, 0.0)) for name in FACTOR_NAMES]
        if route == "market_baseline":
            features = market
        elif route == "text_only":
            features = text
        elif route == "fusion":
            features = market + text
        elif route == "fusion_rules":
            features = market + text + [float(rule_pressure_by_date.get(str(row["trade_date"]), 0.0))]
        else:
            raise ValueError(f"未知模型路线：{route}")
        result.append(features)
    return result


def labels_for_market(market_rows: list[dict[str, Any]]) -> list[str | None]:
    labels: list[str | None] = []
    for index, row in enumerate(market_rows):
        future_index = index + HORIZON_TRADING_DAYS
        if future_index >= len(market_rows):
            labels.append(None)
            continue
        delta_bp = (
            float(market_rows[future_index]["cgb_10y_yield"])
            - float(row["cgb_10y_yield"])
        ) * 100
        labels.append(direction_label(delta_bp))
    return labels


def _metrics(actual: list[str], predicted: list[str], probabilities: list[dict[str, float]]) -> dict[str, Any]:
    if not actual:
        return {"observations": 0, "accuracy": None, "macro_f1": None, "brier": None, "confusion_matrix": {}}
    accuracy = sum(a == p for a, p in zip(actual, predicted)) / len(actual)
    f1_scores: list[float] = []
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    for a, p in zip(actual, predicted):
        matrix[a][p] += 1
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        fn = sum(matrix[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    brier = sum(
        sum((prob[label] - (1.0 if label == a else 0.0)) ** 2 for label in LABELS) / len(LABELS)
        for a, prob in zip(actual, probabilities)
    ) / len(actual)
    return {
        "observations": len(actual),
        "accuracy": round(accuracy, 4),
        "macro_f1": round(sum(f1_scores) / len(f1_scores), 4),
        "brier": round(brier, 4),
        "confusion_matrix": matrix,
        "actual_distribution": dict(Counter(actual)),
    }


def evaluate_route(
    market_rows: list[dict[str, Any]],
    factors_by_date: dict[str, dict[str, float]],
    rule_pressure_by_date: dict[str, float],
    route: str,
    minimum_train: int = 20,
) -> dict[str, Any]:
    features = _feature_rows(market_rows, factors_by_date, rule_pressure_by_date, route)
    labels = labels_for_market(market_rows)
    actual: list[str] = []
    predicted: list[str] = []
    probabilities: list[dict[str, float]] = []
    timeline: list[dict[str, Any]] = []
    last_labeled = len(market_rows) - HORIZON_TRADING_DAYS
    for index in range(minimum_train, last_labeled):
        # At an as-of date, a training row is eligible only if its five-day
        # outcome has already been observed strictly before that date.
        train_indices = [
            item
            for item in range(index)
            if labels[item] is not None and item + HORIZON_TRADING_DAYS < index
        ]
        if len(train_indices) < minimum_train:
            continue
        train_x = [features[item] for item in train_indices]
        train_y = [str(labels[item]) for item in train_indices]
        probs = _fit_predict(train_x, train_y, features[index])
        prediction = max(LABELS, key=lambda label: probs[label])
        actual.append(str(labels[index]))
        predicted.append(prediction)
        probabilities.append(probs)
        timeline.append({
            "as_of": market_rows[index]["trade_date"],
            "actual": labels[index],
            "predicted": prediction,
            "probabilities": probs,
            "train_end": market_rows[index - 1]["trade_date"],
            "train_feature_end": market_rows[train_indices[-1]]["trade_date"],
            "train_label_observed_end": market_rows[train_indices[-1] + HORIZON_TRADING_DAYS]["trade_date"],
        })
    return {"route": route, **_metrics(actual, predicted, probabilities), "timeline": timeline}


def live_probabilities(
    market_rows: list[dict[str, Any]],
    factors_by_date: dict[str, dict[str, float]],
    rule_pressure_by_date: dict[str, float],
    route: str = "fusion_rules",
    minimum_train: int = 20,
) -> dict[str, Any]:
    labels = labels_for_market(market_rows)
    features = _feature_rows(market_rows, factors_by_date, rule_pressure_by_date, route)
    train_indices = [index for index, label in enumerate(labels) if label is not None]
    if len(train_indices) < minimum_train:
        return {
            "data_sufficient": False,
            "probabilities": {label: round(1 / len(LABELS), 6) for label in LABELS},
            "predicted_label": "insufficient",
            "reason": f"至少需要 {minimum_train + HORIZON_TRADING_DAYS} 个交易日，当前仅 {len(market_rows)} 个",
        }
    probs = _fit_predict(
        [features[index] for index in train_indices],
        [str(labels[index]) for index in train_indices],
        features[-1],
    )
    return {
        "data_sufficient": True,
        "probabilities": probs,
        "predicted_label": max(LABELS, key=lambda label: probs[label]),
        "train_observations": len(train_indices),
        "reason": "",
    }
