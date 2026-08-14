"""Stable contracts for macro predicates, periods and route evaluation."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


TARGET_NAME = "electrical_machinery_industrial_value_added_yoy"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
TRAIN_END = "2021-12-31"
VALIDATION_END = "2023-12-31"
OOS_START = "2024-01-01"

TARGET_FIELDS = [
    "period_start",
    "period_end",
    "period_kind",
    "target_name",
    "target_value",
    "release_date",
    "source_url",
]

MACRO_PREDICATE_FIELDS = [
    "doc_id",
    "period_start",
    "period_end",
    "period_kind",
    "predicate_name",
    "value",
    "direction",
    "intensity",
    "confidence",
    "expected_lag_months",
    "evidence_text",
    "source",
]

MACRO_RULE_FIELDS = [
    "rule_id",
    "route",
    "condition",
    "direction",
    "expected_lag_months",
    "independent_document_count",
    "independent_period_count",
    "train_correlation",
    "rolling_mae_improvement",
    "stability",
    "score",
    "status",
]

MONTHLY_FEATURE_FIELDS = [
    "period_start",
    "period_end",
    "period_kind",
    "route",
    "feature_name",
    "feature_value",
    "document_count",
]

NOWCAST_FORECAST_FIELDS = [
    "period_end",
    "forecast_as_of",
    "target_name",
    "predicted_value",
    "latest_released_value",
    "predicted_acceleration",
    "interval_90_lower",
    "interval_90_upper",
    "selected_route",
    "selected_model",
]

ETF_MARKET_FIELDS = [
    "trade_date",
    "asset_code",
    "open",
    "close",
    "is_tradable",
    "asset_role",
]


@dataclass(frozen=True)
class MacroPredicateDefinition:
    description: str
    default_direction: int
    default_lag_months: int


MACRO_PREDICATES: dict[str, MacroPredicateDefinition] = {
    "policy_expands_effective_demand": MacroPredicateDefinition("政策扩大终端或设备有效需求", 1, 2),
    "policy_accelerates_project_implementation": MacroPredicateDefinition("项目审批、建设或落地提速", 1, 2),
    "capacity_under_construction": MacroPredicateDefinition("产能仍处于规划或建设阶段", 1, 6),
    "capacity_enters_production": MacroPredicateDefinition("新产能投产、达产或进入生产", 1, 1),
    "order_demand_improves": MacroPredicateDefinition("订单、中标、排产或交付需求改善", 1, 1),
    "inventory_pressure_increases": MacroPredicateDefinition("库存、去库或积压压力上升", -1, 1),
    "product_price_recovers": MacroPredicateDefinition("产品价格或加工费恢复", 1, 1),
    "raw_material_cost_pressure": MacroPredicateDefinition("原材料价格上涨形成成本压力", -1, 1),
    "export_demand_improves": MacroPredicateDefinition("出口、海外订单或海外需求改善", 1, 1),
    "grid_investment_accelerates": MacroPredicateDefinition("电网投资、招标和建设提速", 1, 3),
    "financing_constraint_eases": MacroPredicateDefinition("融资条件改善或资金约束减弱", 1, 3),
    "industry_competition_intensifies": MacroPredicateDefinition("价格战、过剩或行业竞争加剧", -1, 1),
}

ROUTES = ("predicate_baseline", "historical_rules", "ai_dynamic_rules", "hybrid_rules")


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def period_for_date(value: str) -> dict[str, str]:
    """Map a document date to the official monthly observation period.

    January and February deliberately share the combined Jan-Feb observation.
    """
    current = parse_iso_date(value)
    if current.month <= 2:
        last_day = calendar.monthrange(current.year, 2)[1]
        return {
            "period_start": f"{current.year:04d}-01-01",
            "period_end": f"{current.year:04d}-02-{last_day:02d}",
            "period_kind": "jan_feb_combined",
        }
    last_day = calendar.monthrange(current.year, current.month)[1]
    return {
        "period_start": f"{current.year:04d}-{current.month:02d}-01",
        "period_end": f"{current.year:04d}-{current.month:02d}-{last_day:02d}",
        "period_kind": "month",
    }


def split_for_period(period_end: str) -> str:
    if period_end <= TRAIN_END:
        return "train"
    if period_end <= VALIDATION_END:
        return "validation"
    return "oos"


def advance_period_end(period_end: str, steps: int) -> str:
    """Advance by official observation periods, treating Jan-Feb as one step."""
    if steps < 0:
        raise ValueError("steps must be non-negative")
    current = parse_iso_date(period_end)
    for _ in range(steps):
        if current.month == 12:
            current = date(current.year + 1, 2, calendar.monthrange(current.year + 1, 2)[1])
        elif current.month == 2:
            current = date(current.year, 3, 31)
        else:
            next_month = current.month + 1
            current = date(current.year, next_month, calendar.monthrange(current.year, next_month)[1])
    return current.isoformat()


def validate_target_row(row: dict[str, Any]) -> None:
    missing = [field for field in TARGET_FIELDS if str(row.get(field, "")).strip() == ""]
    if missing:
        raise ValueError("宏观目标缺少字段值：" + "、".join(missing))
    period = period_for_date(str(row["period_end"]))
    if row["period_start"] != period["period_start"] or row["period_kind"] != period["period_kind"]:
        raise ValueError(f"宏观目标统计期不合法：{row['period_end']}")
    if row["target_name"] != TARGET_NAME:
        raise ValueError(f"不支持的宏观目标：{row['target_name']}")
    float(row["target_value"])
    release = parse_iso_date(str(row["release_date"]))
    if release <= parse_iso_date(str(row["period_end"])):
        raise ValueError("release_date 必须晚于 period_end，避免把当期官方值泄漏到 Nowcast")
    if not str(row["source_url"]).startswith(("http://", "https://")):
        raise ValueError("宏观目标必须提供可核验的官方 source_url")
