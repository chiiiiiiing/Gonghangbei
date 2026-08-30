"""Stable contracts and time-alignment rules for the rates research path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Iterable


TARGET_NAME = "cgb_10y_yield_5d_direction"
HORIZON_TRADING_DAYS = 5
FLAT_THRESHOLD_BP = 2.0
MARKET_CLOSE = time(17, 30)
DISCLAIMER = "本报告仅供研究参考，不构成投资建议或自动交易指令"
RESEARCH_BOUNDARY = (
    "首版为官方公开数据MVP；FDR007定盘利率仅作为DR007历史代理，"
    "正式研究应补充经授权的完整DR007序列并进行跨周期样本外检验。"
)

FACTOR_NAMES = (
    "monetary_policy",
    "liquidity",
    "growth",
    "inflation",
    "bond_supply",
    "risk_appetite",
)

FACTOR_LABELS = {
    "monetary_policy": "货币政策",
    "liquidity": "市场流动性",
    "growth": "经济增长",
    "inflation": "通胀",
    "bond_supply": "债券供给",
    "risk_appetite": "风险偏好",
}

MARKET_FIELDS = [
    "trade_date",
    "cgb_10y_yield",
    "dr007_proxy",
    "dr007_proxy_name",
    "cgb_source_url",
    "liquidity_source_url",
    "cgb_source_sha256",
    "liquidity_source_sha256",
    "ingested_at",
]

TEXT_FIELDS = [
    "doc_id",
    "publish_time",
    "title",
    "content",
    "source_name",
    "source_url",
    "source_sha256",
]


@dataclass(frozen=True)
class PredicateDefinition:
    factor: str
    description: str
    yield_direction: int


PREDICATES: dict[str, PredicateDefinition] = {
    "policy_stance_eases": PredicateDefinition("monetary_policy", "货币政策取向边际宽松", -1),
    "policy_stance_tightens": PredicateDefinition("monetary_policy", "货币政策取向边际收紧", 1),
    "liquidity_supply_increases": PredicateDefinition("liquidity", "流动性投放或资金供给增加", -1),
    "liquidity_supply_decreases": PredicateDefinition("liquidity", "流动性回笼或资金供给减少", 1),
    "growth_outlook_strengthens": PredicateDefinition("growth", "增长预期增强", 1),
    "growth_outlook_weakens": PredicateDefinition("growth", "增长预期走弱", -1),
    "inflation_pressure_rises": PredicateDefinition("inflation", "通胀压力上升", 1),
    "inflation_pressure_falls": PredicateDefinition("inflation", "通胀压力下降", -1),
    "government_bond_supply_rises": PredicateDefinition("bond_supply", "政府债券供给增加", 1),
    "government_bond_supply_falls": PredicateDefinition("bond_supply", "政府债券供给减少", -1),
    "risk_aversion_rises": PredicateDefinition("risk_appetite", "避险需求上升", -1),
    "risk_aversion_falls": PredicateDefinition("risk_appetite", "风险偏好回升", 1),
}


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def validate_market_row(row: dict[str, Any]) -> None:
    missing = [field for field in MARKET_FIELDS if str(row.get(field, "")).strip() == ""]
    if missing:
        raise ValueError("利率市场数据缺少字段：" + "、".join(missing))
    date.fromisoformat(str(row["trade_date"]))
    cgb = float(row["cgb_10y_yield"])
    liquidity = float(row["dr007_proxy"])
    if not 0 < cgb < 20 or not 0 < liquidity < 20:
        raise ValueError("收益率字段应使用百分数数值，例如 1.85")
    if row["dr007_proxy_name"] != "FDR007_FIXING":
        raise ValueError("首版历史流动性代理必须明确标识为 FDR007_FIXING")
    for field in ("cgb_source_url", "liquidity_source_url"):
        if not str(row[field]).startswith(("https://", "http://")):
            raise ValueError(f"{field} 必须是可核验网址")
    for field in ("cgb_source_sha256", "liquidity_source_sha256"):
        if len(str(row[field])) != 64:
            raise ValueError(f"{field} 必须是 SHA-256")


def validate_text_row(row: dict[str, Any]) -> None:
    missing = [field for field in TEXT_FIELDS if str(row.get(field, "")).strip() == ""]
    if missing:
        raise ValueError("政策文本缺少字段：" + "、".join(missing))
    parse_datetime(str(row["publish_time"]))
    if not str(row["source_url"]).startswith(("https://", "http://")):
        raise ValueError("政策文本必须提供可核验 source_url")
    if len(str(row["source_sha256"])) != 64:
        raise ValueError("政策文本必须提供 SHA-256")


def effective_trade_date(publish_time: str, trade_dates: Iterable[str]) -> str | None:
    """Map a public timestamp to the first close at which it may be used.

    A document published after 17:30 or on a non-trading day becomes available
    on the next listed trading day. No calendar guess is made beyond supplied
    official market dates.
    """
    stamp = parse_datetime(publish_time)
    ordered = sorted(set(trade_dates))
    same_day = stamp.date().isoformat()
    if same_day in ordered and stamp.time() <= MARKET_CLOSE:
        return same_day
    return next((item for item in ordered if item > same_day), None)


def direction_label(delta_bp: float, threshold_bp: float = FLAT_THRESHOLD_BP) -> str:
    if delta_bp > threshold_bp:
        return "up"
    if delta_bp < -threshold_bp:
        return "down"
    return "flat"
