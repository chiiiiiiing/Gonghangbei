"""Run discovery/validation-only sensitivity and ablation audits.

This script never reads 2025+ outcomes.  Results are descriptive robustness
evidence and must not be used to relabel the retrospective holdout as OOS.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.engine import _daily_context, _load, _load_structured  # noqa: E402
from src.rates.modeling import (  # noqa: E402
    LABELS,
    ROUTES,
    ROUTE_LABELS,
    _metrics,
    evaluate_route,
    labels_for_market,
)
from src.rates.schema import (  # noqa: E402
    FLAT_THRESHOLD_BP,
    HORIZON_TRADING_DAYS,
    MINIMUM_TRAIN_DAYS,
    ROLLING_TRAIN_DAYS,
    direction_label,
)


OUT_JSON = ROOT / "data" / "sample" / "rates_robustness.json"
OUT_MD = ROOT / "data" / "sample" / "rates_robustness_report.md"
HORIZONS = (1, 5, 10)
THRESHOLDS = (1.0, 2.0, 3.0)
DECAY_CONFIGS = ((3, 1.0), (5, 2.0), (10, 5.0))
CUTOFF = "2025-01-01"


def _validation_metrics(route: dict[str, Any]) -> dict[str, Any]:
    return next(
        row for row in route.get("period_metrics", [])
        if row.get("period") == "validation_2023_2024"
    )


def _compact(route: dict[str, Any]) -> dict[str, Any]:
    validation = _validation_metrics(route)
    return {
        "route": route["route"], "route_label": route["route_label"],
        "observations": route["observations"], "accuracy": route["accuracy"],
        "macro_f1": route["macro_f1"], "macro_auc_ovr": route["macro_auc_ovr"],
        "validation_observations": validation["observations"],
        "validation_accuracy": validation["accuracy"],
        "validation_macro_f1": validation["macro_f1"],
        "validation_auc_ovr": validation["macro_auc_ovr"],
        "validation_actual_distribution": validation.get("actual_distribution", {}),
    }


def _naive_baseline(
    market: list[dict[str, str]], horizon: int, threshold: float, kind: str,
) -> dict[str, Any]:
    labels = labels_for_market(market, horizon, threshold)
    actual: list[str] = []
    predicted: list[str] = []
    probabilities: list[dict[str, float]] = []
    validation_actual: list[str] = []
    validation_predicted: list[str] = []
    validation_probabilities: list[dict[str, float]] = []
    first = MINIMUM_TRAIN_DAYS + horizon
    for index in range(first, len(market) - horizon, horizon):
        earliest = max(0, index - ROLLING_TRAIN_DAYS)
        train_indices = [
            item for item in range(earliest, index)
            if labels[item] is not None and item + horizon <= index
        ]
        if len(train_indices) < MINIMUM_TRAIN_DAYS:
            continue
        train_labels = [str(labels[item]) for item in train_indices]
        if kind == "majority":
            counts = Counter(train_labels)
            guess = max(LABELS, key=lambda label: (counts[label], -LABELS.index(label)))
            total = len(train_labels)
            probs = {label: counts[label] / total for label in LABELS}
        elif kind == "momentum":
            current = float(market[index]["cgb_10y_yield"])
            previous = float(market[max(0, index - horizon)]["cgb_10y_yield"])
            guess = direction_label((current - previous) * 100, threshold)
            probs = {label: 0.1 for label in LABELS}
            probs[guess] = 0.8
        else:
            raise ValueError(f"未知朴素基线：{kind}")
        truth = str(labels[index])
        actual.append(truth); predicted.append(guess); probabilities.append(probs)
        if market[index]["trade_date"] >= "2023-01-01":
            validation_actual.append(truth)
            validation_predicted.append(guess)
            validation_probabilities.append(probs)
    overall = _metrics(actual, predicted, probabilities)
    validation = _metrics(validation_actual, validation_predicted, validation_probabilities)
    return {
        "route": f"naive_{kind}", "route_label": "滚动多数类" if kind == "majority" else "收益率动量规则",
        **overall,
        "validation_observations": validation["observations"],
        "validation_accuracy": validation["accuracy"],
        "validation_macro_f1": validation["macro_f1"],
        "validation_auc_ovr": validation["macro_auc_ovr"],
        "validation_actual_distribution": validation.get("actual_distribution", {}),
    }


def build_robustness() -> dict[str, Any]:
    market, texts, errors, _duplicates = _load()
    structured, structured_errors = _load_structured()
    errors.extend(structured_errors)
    if errors:
        raise ValueError("；".join(errors))
    audit_market = [row for row in market if row["trade_date"] < CUTOFF]
    audit_texts = [row for row in texts if row["publish_time"][:10] < CUTOFF]
    audit_structured = [row for row in structured if row["release_time"][:10] < CUTOFF]
    factors, pressure, _evidence, _daily = _daily_context(audit_market, audit_texts, audit_structured)

    horizon_threshold: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        for threshold in THRESHOLDS:
            routes = [
                _compact(evaluate_route(
                    audit_market, factors, pressure, route,
                    horizon_trading_days=horizon,
                    threshold_bp=threshold,
                    evaluation_stride=horizon,
                ))
                for route in ROUTES
            ]
            horizon_threshold.append({
                "horizon_trading_days": horizon, "threshold_bp": threshold,
                "routes": routes,
                "naive_baselines": [
                    _naive_baseline(audit_market, horizon, threshold, "majority"),
                    _naive_baseline(audit_market, horizon, threshold, "momentum"),
                ],
            })

    decay_sensitivity: list[dict[str, Any]] = []
    for days, half_life in DECAY_CONFIGS:
        decay_factors, decay_pressure, _evidence, _daily = _daily_context(
            audit_market, audit_texts, audit_structured,
            text_decay_days=days, text_half_life_days=half_life,
        )
        decay_sensitivity.append({
            "text_decay_days": days, "text_half_life_days": half_life,
            "routes": [
                _compact(evaluate_route(
                    audit_market, decay_factors, decay_pressure, route,
                    horizon_trading_days=HORIZON_TRADING_DAYS,
                    threshold_bp=FLAT_THRESHOLD_BP,
                    evaluation_stride=HORIZON_TRADING_DAYS,
                ))
                for route in ROUTES
            ],
        })

    production_case = next(
        row for row in horizon_threshold
        if row["horizon_trading_days"] == HORIZON_TRADING_DAYS and row["threshold_bp"] == FLAT_THRESHOLD_BP
    )
    return {
        "version": "rates-robustness-audit-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_cutoff_exclusive": CUTOFF,
        "period": {"start": audit_market[0]["trade_date"], "end": audit_market[-1]["trade_date"]},
        "selection_allowed": False,
        "selection_warning": (
            "仅使用发现期和验证期做敏感性审计；本报告不改写V1参数，"
            "不读取2025年后真实标签，也不得把回顾性留出包装为前瞻OOS。"
        ),
        "production_contract": {
            "horizon_trading_days": HORIZON_TRADING_DAYS,
            "threshold_bp": FLAT_THRESHOLD_BP,
            "text_decay_days": 5, "text_half_life_days": 2.0,
        },
        "interpretation_guard": (
            "窗口缩短或阈值扩大可能显著提高震荡类占比，从而抬高Accuracy；"
            "必须同时比较Macro-F1、每类召回率和类别分布。"
        ),
        "production_ablation": production_case,
        "horizon_threshold_sensitivity": horizon_threshold,
        "text_decay_sensitivity": decay_sensitivity,
    }


def _fmt(value: Any, percent: bool = False) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1%}" if percent else f"{float(value):.4f}"


def write_report(payload: dict[str, Any]) -> None:
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 利率方向稳健性与消融审计", "",
        f"- 数据截止（不含）：{payload['data_cutoff_exclusive']}",
        f"- 审计区间：{payload['period']['start']} 至 {payload['period']['end']}",
        f"- 参数选择：禁止。{payload['selection_warning']}", "",
        f"- 解读约束：{payload['interpretation_guard']}", "",
        "## V1口径消融（5日、±2bp）", "",
        "| 路线 | 验证期样本 | 验证期准确率 | 验证期Macro-F1 | 验证期AUC |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    production = payload["production_ablation"]
    for row in [*production["naive_baselines"], *production["routes"]]:
        lines.append(
            f"| {row['route_label']} | {row['validation_observations']} | "
            f"{_fmt(row['validation_accuracy'], True)} | {_fmt(row['validation_macro_f1'])} | "
            f"{_fmt(row['validation_auc_ovr'])} |"
        )
    lines.extend([
        "", "## 窗口与阈值敏感性（融合加规则）", "",
        "| 窗口 | 阈值 | 验证期样本 | 准确率 | Macro-F1 | AUC |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for case in payload["horizon_threshold_sensitivity"]:
        row = next(item for item in case["routes"] if item["route"] == "fusion_rules")
        lines.append(
            f"| {case['horizon_trading_days']}日 | ±{case['threshold_bp']:.0f}bp | "
            f"{row['validation_observations']} | {_fmt(row['validation_accuracy'], True)} | "
            f"{_fmt(row['validation_macro_f1'])} | {_fmt(row['validation_auc_ovr'])} |"
        )
    lines.extend([
        "", "## 文本衰减敏感性（5日、±2bp、融合加规则）", "",
        "| 影响窗口 | 半衰期 | 验证期准确率 | 验证期Macro-F1 | 验证期AUC |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ])
    for case in payload["text_decay_sensitivity"]:
        row = next(item for item in case["routes"] if item["route"] == "fusion_rules")
        lines.append(
            f"| {case['text_decay_days']}日 | {case['text_half_life_days']:.1f}日 | "
            f"{_fmt(row['validation_accuracy'], True)} | {_fmt(row['validation_macro_f1'])} | "
            f"{_fmt(row['validation_auc_ovr'])} |"
        )
    lines.extend(["", "本报告仅供研究参考，不构成投资建议。", ""])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    payload = build_robustness()
    write_report(payload)
    print(json.dumps({
        "json": str(OUT_JSON.relative_to(ROOT)),
        "report": str(OUT_MD.relative_to(ROOT)),
        "cases": len(payload["horizon_threshold_sensitivity"]),
        "decay_cases": len(payload["text_decay_sensitivity"]),
        "cutoff": payload["data_cutoff_exclusive"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
