"""Low-capacity, time-aware models for macro rule route comparison."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any

import numpy as np

from src.macro.schema import ROUTES, split_for_period, validate_target_row


MIN_TRAIN_OBSERVATIONS = 72
MIN_VALIDATION_OBSERVATIONS = 18
MIN_TEXT_MONTH_COVERAGE = 0.80
MIN_VALIDATION_TEXT_COVERAGE = 0.60


@dataclass
class Standardizer:
    means: np.ndarray
    scales: np.ndarray

    @classmethod
    def fit(cls, values: np.ndarray) -> "Standardizer":
        means = values.mean(axis=0) if values.size else np.zeros(values.shape[1])
        scales = values.std(axis=0) if values.size else np.ones(values.shape[1])
        scales = np.where(scales < 1e-12, 1.0, scales)
        return cls(means, scales)

    def transform(self, values: np.ndarray) -> np.ndarray:
        return (values - self.means) / self.scales


@dataclass
class LinearModel:
    intercept: float
    coefficients: np.ndarray

    def predict(self, values: np.ndarray) -> np.ndarray:
        return self.intercept + values @ self.coefficients


def fit_ridge(values: np.ndarray, targets: np.ndarray, alpha: float) -> LinearModel:
    if len(targets) == 0:
        return LinearModel(0.0, np.zeros(values.shape[1]))
    x_mean = values.mean(axis=0)
    y_mean = float(targets.mean())
    centered_x = values - x_mean
    centered_y = targets - y_mean
    identity = np.eye(values.shape[1])
    coefficients = np.linalg.pinv(centered_x.T @ centered_x + alpha * identity) @ centered_x.T @ centered_y
    return LinearModel(y_mean - float(x_mean @ coefficients), coefficients)


def _soft_threshold(value: float, threshold: float) -> float:
    if value > threshold:
        return value - threshold
    if value < -threshold:
        return value + threshold
    return 0.0


def fit_elastic_net(
    values: np.ndarray,
    targets: np.ndarray,
    alpha: float,
    l1_ratio: float = 0.5,
    max_iterations: int = 2000,
    tolerance: float = 1e-8,
) -> LinearModel:
    """Coordinate-descent Elastic Net with an unpenalized intercept."""
    if len(targets) == 0:
        return LinearModel(0.0, np.zeros(values.shape[1]))
    x_mean = values.mean(axis=0)
    y_mean = float(targets.mean())
    centered_x = values - x_mean
    centered_y = targets - y_mean
    coefficients = np.zeros(values.shape[1])
    squared_norms = np.sum(centered_x * centered_x, axis=0) / len(targets)
    for _ in range(max_iterations):
        previous = coefficients.copy()
        prediction = centered_x @ coefficients
        for index in range(values.shape[1]):
            residual = centered_y - prediction + centered_x[:, index] * coefficients[index]
            rho = float(centered_x[:, index] @ residual) / len(targets)
            denominator = squared_norms[index] + alpha * (1.0 - l1_ratio)
            updated = _soft_threshold(rho, alpha * l1_ratio) / denominator if denominator else 0.0
            prediction += centered_x[:, index] * (updated - coefficients[index])
            coefficients[index] = updated
        if float(np.max(np.abs(coefficients - previous))) < tolerance:
            break
    return LinearModel(y_mean - float(x_mean @ coefficients), coefficients)


def _target_records(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for row in targets:
        validate_target_row(row)
        records.append({**row, "target_value": float(row["target_value"])})
    return sorted(records, key=lambda row: row["period_end"])


def _feature_pivot(feature_rows: list[dict[str, Any]], route: str) -> tuple[list[str], dict[str, list[float]]]:
    selected = [row for row in feature_rows if row["route"] == route]
    feature_names = sorted({str(row["feature_name"]) for row in selected})
    lookup = {
        (str(row["period_end"]), str(row["feature_name"])): float(row["feature_value"])
        for row in selected
    }
    periods = sorted({str(row["period_end"]) for row in selected})
    return feature_names, {
        period: [lookup.get((period, feature_name), 0.0) for feature_name in feature_names]
        for period in periods
    }


def _latest_released(records: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    return [row for row in records if row["release_date"] <= as_of]


def _seasonal_value(records: list[dict[str, Any]], current: dict[str, Any], as_of: str) -> float | None:
    month = current["period_end"][5:7]
    kind = current["period_kind"]
    candidates = [
        row
        for row in records
        if row["release_date"] <= as_of
        and row["period_kind"] == kind
        and row["period_end"][5:7] == month
        and row["period_end"] < current["period_end"]
    ]
    return float(candidates[-1]["target_value"]) if candidates else None


def build_model_rows(
    targets: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    route: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build rows using only macro values released by each forecast period end."""
    records = _target_records(targets)
    text_feature_names, text_by_period = _feature_pivot(feature_rows, route)
    rows: list[dict[str, Any]] = []
    for current in records:
        period_end = str(current["period_end"])
        released = _latest_released(records, period_end)
        if not released:
            continue
        latest = float(released[-1]["target_value"])
        prior = float(released[-2]["target_value"]) if len(released) >= 2 else latest
        seasonal = _seasonal_value(records, current, period_end)
        month = int(period_end[5:7])
        no_text = [
            latest,
            latest - prior,
            seasonal if seasonal is not None else latest,
            math.sin(2 * math.pi * month / 12.0),
            math.cos(2 * math.pi * month / 12.0),
            1.0 if current["period_kind"] == "jan_feb_combined" else 0.0,
        ]
        text = text_by_period.get(period_end, [0.0] * len(text_feature_names))
        rows.append(
            {
                "period_end": period_end,
                "split": split_for_period(period_end),
                "target": float(current["target_value"]),
                "latest_released": latest,
                "seasonal_value": seasonal if seasonal is not None else latest,
                "no_text": no_text,
                "text": text,
                "text_available": period_end in text_by_period and any(abs(value) > 1e-12 for value in text),
            }
        )
    no_text_names = ["latest_released", "latest_change", "seasonal_value", "month_sin", "month_cos", "jan_feb"]
    return rows, [*no_text_names, *text_feature_names]


def _arrays(rows: list[dict[str, Any]], include_text: bool) -> tuple[np.ndarray, np.ndarray]:
    values = [row["no_text"] + (row["text"] if include_text else []) for row in rows]
    width = len(values[0]) if values else 0
    return np.asarray(values, dtype=float).reshape(len(values), width), np.asarray([row["target"] for row in rows], dtype=float)


def _time_series_alpha(
    rows: list[dict[str, Any]],
    include_text: bool,
    model_kind: str,
) -> float:
    candidates = (0.1, 1.0, 10.0, 100.0)
    if len(rows) < 24:
        return 10.0
    split_at = max(12, int(len(rows) * 0.75))
    fit_rows, check_rows = rows[:split_at], rows[split_at:]
    train_x, train_y = _arrays(fit_rows, include_text)
    check_x, check_y = _arrays(check_rows, include_text)
    scaler = Standardizer.fit(train_x)
    train_scaled, check_scaled = scaler.transform(train_x), scaler.transform(check_x)
    errors: list[tuple[float, float]] = []
    for alpha in candidates:
        model = (
            fit_ridge(train_scaled, train_y, alpha)
            if model_kind == "ridge"
            else fit_elastic_net(train_scaled, train_y, alpha, l1_ratio=0.5)
        )
        prediction = model.predict(check_scaled)
        errors.append((float(np.mean(np.abs(check_y - prediction))), alpha))
    return min(errors)[1]


def _metrics(actual: list[float], predicted: list[float], persistence: list[float]) -> dict[str, float | int]:
    if not actual:
        return {
            "sample_count": 0,
            "mae": 0.0,
            "rmse": 0.0,
            "oos_r2_vs_persistence": 0.0,
            "acceleration_direction_accuracy": 0.0,
            "prediction_interval_90_coverage": 0.0,
        }
    errors = [forecast - value for forecast, value in zip(predicted, actual)]
    benchmark_errors = [forecast - value for forecast, value in zip(persistence, actual)]
    benchmark_sse = sum(value * value for value in benchmark_errors)
    direction_hits = [
        (forecast - latest >= 0) == (value - latest >= 0)
        for forecast, value, latest in zip(predicted, actual, persistence)
    ]
    return {
        "sample_count": len(actual),
        "mae": mean(abs(value) for value in errors),
        "rmse": math.sqrt(mean(value * value for value in errors)),
        "oos_r2_vs_persistence": 1.0 - sum(value * value for value in errors) / benchmark_sse if benchmark_sse else 0.0,
        "acceleration_direction_accuracy": sum(direction_hits) / len(direction_hits),
        "prediction_interval_90_coverage": 0.0,
    }


def _fit_and_predict(
    train_rows: list[dict[str, Any]],
    evaluation_rows: list[dict[str, Any]],
    *,
    include_text: bool,
    model_kind: str,
    fixed_alpha: float | None = None,
) -> tuple[list[float], float, float]:
    train_x, train_y = _arrays(train_rows, include_text)
    evaluation_x, _evaluation_y = _arrays(evaluation_rows, include_text)
    if train_x.shape[1] == 0 or len(train_y) < 3:
        return [row["latest_released"] for row in evaluation_rows], 0.0, 0.0
    scaler = Standardizer.fit(train_x)
    alpha = fixed_alpha if fixed_alpha is not None else _time_series_alpha(train_rows, include_text, model_kind)
    model = (
        fit_ridge(scaler.transform(train_x), train_y, alpha)
        if model_kind == "ridge"
        else fit_elastic_net(scaler.transform(train_x), train_y, alpha, l1_ratio=0.5)
    )
    fitted = model.predict(scaler.transform(train_x))
    residual_std = float(np.std(train_y - fitted))
    prediction = model.predict(scaler.transform(evaluation_x))
    return [float(value) for value in prediction], alpha, residual_std


def _prediction_rows(
    rows: list[dict[str, Any]],
    predicted: list[float],
    interval_half_width: float,
) -> list[dict[str, Any]]:
    return [
        {
            "period_end": row["period_end"],
            "actual": row["target"],
            "predicted": forecast,
            "latest_released": row["latest_released"],
            "predicted_acceleration": forecast - row["latest_released"],
            "actual_acceleration": row["target"] - row["latest_released"],
            "interval_90_lower": forecast - interval_half_width,
            "interval_90_upper": forecast + interval_half_width,
        }
        for row, forecast in zip(rows, predicted)
    ]


def _interval_coverage(predictions: list[dict[str, Any]]) -> float:
    if not predictions:
        return 0.0
    return sum(row["interval_90_lower"] <= row["actual"] <= row["interval_90_upper"] for row in predictions) / len(predictions)


def evaluate_model(
    rows: list[dict[str, Any]],
    evaluation_split: str,
    *,
    include_text: bool,
    model_kind: str,
    fixed_alpha: float | None = None,
) -> dict[str, Any]:
    fit_splits = {"train"} if evaluation_split == "validation" else {"train", "validation"}
    train_rows = [row for row in rows if row["split"] in fit_splits]
    evaluation_rows = [row for row in rows if row["split"] == evaluation_split]
    predicted, alpha, residual_std = _fit_and_predict(
        train_rows,
        evaluation_rows,
        include_text=include_text,
        model_kind=model_kind,
        fixed_alpha=fixed_alpha,
    )
    predictions = _prediction_rows(evaluation_rows, predicted, 1.645 * residual_std)
    metrics = _metrics(
        [row["target"] for row in evaluation_rows],
        predicted,
        [row["latest_released"] for row in evaluation_rows],
    )
    metrics["prediction_interval_90_coverage"] = _interval_coverage(predictions)
    return {"metrics": metrics, "predictions": predictions, "alpha": alpha}


def evaluate_simple_baselines(rows: list[dict[str, Any]], evaluation_split: str) -> dict[str, Any]:
    """Evaluate persistence, same-period seasonal and expanding AR(1) baselines."""
    evaluation_rows = [row for row in rows if row["split"] == evaluation_split]
    fit_splits = {"train"} if evaluation_split == "validation" else {"train", "validation"}
    fit_rows = [row for row in rows if row["split"] in fit_splits]

    persistence = [float(row["latest_released"]) for row in evaluation_rows]
    seasonal = [float(row["seasonal_value"]) for row in evaluation_rows]
    if len(fit_rows) >= 3:
        ar_x = np.asarray([[float(row["latest_released"])] for row in fit_rows], dtype=float)
        ar_y = np.asarray([float(row["target"]) for row in fit_rows], dtype=float)
        ar_model = fit_ridge(ar_x, ar_y, alpha=0.1)
        ar_prediction = [float(value) for value in ar_model.predict(
            np.asarray([[float(row["latest_released"])] for row in evaluation_rows], dtype=float)
        )]
    else:
        ar_prediction = persistence

    actual = [float(row["target"]) for row in evaluation_rows]
    return {
        "persistence": {
            "metrics": _metrics(actual, persistence, persistence),
            "predictions": _prediction_rows(evaluation_rows, persistence, 0.0),
        },
        "seasonal": {
            "metrics": _metrics(actual, seasonal, persistence),
            "predictions": _prediction_rows(evaluation_rows, seasonal, 0.0),
        },
        "ar1": {
            "metrics": _metrics(actual, ar_prediction, persistence),
            "predictions": _prediction_rows(evaluation_rows, ar_prediction, 0.0),
        },
    }


def _coverage(rows: list[dict[str, Any]], split: str) -> float:
    selected = [row for row in rows if row["split"] == split]
    return sum(row["text_available"] for row in selected) / len(selected) if selected else 0.0


def _block_concentration(
    baseline_predictions: list[dict[str, Any]],
    text_predictions: list[dict[str, Any]],
) -> float:
    baseline = {row["period_end"]: row for row in baseline_predictions}
    improvements: dict[str, float] = {}
    for row in text_predictions:
        period = row["period_end"]
        if period not in baseline:
            continue
        block = f"{period[:4]}H{'1' if int(period[5:7]) <= 6 else '2'}"
        improvement = abs(baseline[period]["predicted"] - row["actual"]) - abs(row["predicted"] - row["actual"])
        improvements[block] = improvements.get(block, 0.0) + max(improvement, 0.0)
    total = sum(improvements.values())
    return max(improvements.values(), default=0.0) / total if total else 1.0


def evaluate_routes(
    targets: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare all routes on Validation and report a frozen OOS view."""
    target_records = _target_records(targets)
    target_counts = {
        split: sum(split_for_period(row["period_end"]) == split for row in target_records)
        for split in ("train", "validation", "oos")
    }
    results: dict[str, Any] = {}
    shared_baselines: dict[str, Any] = {}
    for route in ROUTES:
        rows, feature_names = build_model_rows(target_records, feature_rows, route)
        if not shared_baselines:
            shared_baselines = {
                "validation": evaluate_simple_baselines(rows, "validation"),
                "frozen_oos": evaluate_simple_baselines(rows, "oos"),
            }
        baseline = evaluate_model(rows, "validation", include_text=False, model_kind="ridge")
        ridge = evaluate_model(rows, "validation", include_text=True, model_kind="ridge")
        elastic = evaluate_model(rows, "validation", include_text=True, model_kind="elastic_net")
        route_result = min(
            (("ridge", ridge), ("elastic_net", elastic)),
            key=lambda item: (float(item[1]["metrics"]["mae"]), float(item[1]["metrics"]["rmse"])),
        )
        model_name, selected = route_result
        base_metrics = baseline["metrics"]
        text_metrics = selected["metrics"]
        concentration = _block_concentration(baseline["predictions"], selected["predictions"])
        coverage = _coverage(rows, "validation")
        qualifies = (
            int(text_metrics["sample_count"]) >= MIN_VALIDATION_OBSERVATIONS
            and float(text_metrics["mae"]) < float(base_metrics["mae"])
            and float(text_metrics["rmse"]) < float(base_metrics["rmse"])
            and float(text_metrics["acceleration_direction_accuracy"])
            >= float(base_metrics["acceleration_direction_accuracy"])
            and coverage >= MIN_VALIDATION_TEXT_COVERAGE
            and concentration <= 0.60
        )
        oos = evaluate_model(
            rows,
            "oos",
            include_text=True,
            model_kind=model_name,
            fixed_alpha=float(selected["alpha"]),
        )
        results[route] = {
            "selected_model": model_name,
            "feature_count": len(feature_names),
            "validation_text_coverage": coverage,
            "block_improvement_concentration": concentration,
            "baseline_validation": baseline,
            "text_validation": selected,
            "delta_validation": {
                "mae": float(text_metrics["mae"]) - float(base_metrics["mae"]),
                "rmse": float(text_metrics["rmse"]) - float(base_metrics["rmse"]),
                "oos_r2_vs_persistence": float(text_metrics["oos_r2_vs_persistence"])
                - float(base_metrics["oos_r2_vs_persistence"]),
            },
            "qualified": qualifies,
            "frozen_oos": oos,
        }

    baseline_rows, _baseline_features = build_model_rows(target_records, feature_rows, "predicate_baseline")
    train_text_coverage = _coverage(baseline_rows, "train")
    validation_text_coverage = _coverage(baseline_rows, "validation")
    data_sufficient = (
        target_counts["train"] >= MIN_TRAIN_OBSERVATIONS
        and target_counts["validation"] >= MIN_VALIDATION_OBSERVATIONS
        and train_text_coverage >= MIN_TEXT_MONTH_COVERAGE
        and validation_text_coverage >= MIN_TEXT_MONTH_COVERAGE
    )
    qualified = [
        (route, result)
        for route, result in results.items()
        if route != "predicate_baseline" and result["qualified"] and data_sufficient
    ]
    if qualified:
        selected_route, selected_result = min(
            qualified,
            key=lambda item: (
                float(item[1]["text_validation"]["metrics"]["mae"]),
                float(item[1]["text_validation"]["metrics"]["rmse"]),
                int(item[1]["feature_count"]),
            ),
        )
        status = "text_increment_validated"
        conclusion = f"验证期选择 {selected_route}，进入冻结 OOS。"
    else:
        selected_route = "no_text_ridge"
        selected_result = None
        status = "insufficient_text_increment"
        conclusion = "文本预测增量不足"
    return {
        "target_name": target_records[0]["target_name"] if target_records else "electrical_machinery_industrial_value_added_yoy",
        "selection_split": "validation_2022_2023",
        "oos_policy": "2024 年后只报告，不重新选择路线或调参",
        "target_counts": target_counts,
        "train_text_coverage": train_text_coverage,
        "validation_text_coverage": validation_text_coverage,
        "data_sufficient": data_sufficient,
        "status": status,
        "conclusion": conclusion,
        "selected_route": selected_route,
        "selected_model": selected_result["selected_model"] if selected_result else "ridge",
        "routes": results,
        "baselines": shared_baselines,
        "disclaimer": "本报告仅供研究参考，不构成投资建议",
    }
