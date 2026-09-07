"""Stable contracts and temporal rules for the rates research pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any, Iterable


TARGET_NAME = "cgb_10y_yield_5d_direction"
HORIZON_TRADING_DAYS = 5
FLAT_THRESHOLD_BP = 2.0
MARKET_CLOSE = time(17, 30)
TEXT_DECAY_DAYS = 5
TEXT_HALF_LIFE_DAYS = 2.0
# The enhanced model is deliberately anchored to the market baseline.  Text is
# sparse and regime-sensitive, so a small frozen overlay is safer than letting
# it replace the market signal in one high-dimensional fit.
TEXT_OVERLAY_WEIGHT = 0.10
# Activated economic rules are an explicit log-probability prior rather than a
# second copy of the same text factors inside the statistical model.
RULE_LOGIT_WEIGHT = 0.15
ENHANCEMENT_VERSION = "rates-enhancement-v1.0-20260907"
MIN_LLM_ONLY_CONFIDENCE = 0.80
ROLLING_TRAIN_DAYS = 756
MINIMUM_TRAIN_DAYS = 252
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
RESEARCH_BOUNDARY = (
    "系统预测的是10年期国债收益率未来5个交易日方向，用于银行金融市场投研辅助，"
    "不自动下单、不输出目标价或收益保证。公开历史流动性序列使用FDR007定盘利率作为DR007代理，"
    "不能将其表述为原始DR007成交加权利率。"
)

FACTOR_NAMES = (
    "monetary_policy",
    "liquidity",
    "growth",
    "inflation",
    "bond_supply",
    "risk_appetite",
)


@dataclass(frozen=True)
class FactorDefinition:
    label: str
    meaning: str
    negative_signal: str
    positive_signal: str
    text_sources: str
    structured_confirmation: str


FACTOR_DEFINITIONS: dict[str, FactorDefinition] = {
    "monetary_policy": FactorDefinition(
        "货币政策", "央行政策立场相对前期的边际变化", "宽松，收益率下行压力", "收紧，收益率上行压力",
        "货币政策执行报告、政策工具公告、央行新闻稿", "政策利率、准备金率、MLF利率",
    ),
    "liquidity": FactorDefinition(
        "市场流动性", "银行间资金供给与资金价格状态", "投放或宽松", "回笼或偏紧",
        "公开市场操作公告、资金面表述", "DR007/FDR007、公开市场净投放",
    ),
    "growth": FactorDefinition(
        "经济增长", "实体经济增长与需求预期", "增长走弱", "增长增强",
        "货币政策报告、重要经济会议、统计发布", "PMI、社融、工业增加值",
    ),
    "inflation": FactorDefinition(
        "通胀", "价格水平及通胀预期", "通胀回落", "通胀上升",
        "货币政策报告、统计发布", "CPI、PPI",
    ),
    "bond_supply": FactorDefinition(
        "债券供给", "政府债发行对利率债供需的压力", "供给减少", "供给增加",
        "财政政策文件、国债及地方债发行公告", "政府债净融资与发行计划",
    ),
    "risk_appetite": FactorDefinition(
        "风险偏好", "避险需求和市场风险承受意愿", "避险上升，利率债需求增强", "风险偏好回升",
        "政策报告、风险事件与权威会议文件", "权益波动、信用利差（仅作确认）",
    ),
}

FACTOR_LABELS = {name: definition.label for name, definition in FACTOR_DEFINITIONS.items()}

SOURCE_WEIGHTS = {
    "中国人民银行": 1.0,
    "财政部": 0.98,
    "国家统计局": 0.96,
    "中国外汇交易中心": 0.96,
    "中央国债登记结算有限责任公司": 0.96,
    "国务院": 0.95,
    "国家发展改革委": 0.93,
}

MARKET_FIELDS = [
    "trade_date", "cgb_10y_yield", "dr007_proxy", "dr007_proxy_name",
    "cgb_source_url", "liquidity_source_url", "cgb_source_sha256",
    "liquidity_source_sha256", "ingested_at",
]

TEXT_FIELDS = [
    "doc_id", "publish_time", "title", "content", "source_name",
    "source_url", "source_sha256",
]

# Structured observations keep the statistical period separate from the first
# public release timestamp.  This is the B/C data contract for vintage-safe
# macro inputs (CPI/PPI/PMI, AFRE, MLF and government-bond issuance).
STRUCTURED_FIELDS = [
    "observation_date", "release_time", "period_start", "period_end",
    "indicator", "value", "unit", "source_name", "source_url",
    "source_sha256", "vintage",
]

STRUCTURED_INDICATORS = (
    "cpi_yoy", "ppi_yoy", "pmi_manufacturing", "afre_flow",
    "afre_rmb_loans", "afre_government_bonds", "mlf_amount",
    "mlf_rate", "government_bond_issuance",
)

MINIMUM_INDEPENDENT_EVENTS = 5

EVENT_FIELDS = (
    "event_id", "subject", "action", "object", "policy_direction",
    "intensity", "horizon", "transmission_channel", "evidence_text", "confidence",
)


@dataclass(frozen=True)
class PredicateDefinition:
    factor: str
    description: str
    yield_direction: int
    subject: str
    action: str
    object: str
    horizon: str


PREDICATES: dict[str, PredicateDefinition] = {
    "policy_stance_eases": PredicateDefinition("monetary_policy", "货币政策取向边际宽松", -1, "中国人民银行", "宽松", "政策立场", "中期"),
    "policy_stance_tightens": PredicateDefinition("monetary_policy", "货币政策取向边际收紧", 1, "中国人民银行", "收紧", "政策立场", "中期"),
    "liquidity_supply_increases": PredicateDefinition("liquidity", "流动性投放或资金供给增加", -1, "中国人民银行", "投放", "银行间流动性", "短期"),
    "liquidity_supply_decreases": PredicateDefinition("liquidity", "流动性回笼或资金供给减少", 1, "中国人民银行", "回笼", "银行间流动性", "短期"),
    "funding_conditions_tighten": PredicateDefinition("liquidity", "资金价格或融资条件趋紧", 1, "银行间市场", "趋紧", "资金面", "短期"),
    "funding_conditions_ease": PredicateDefinition("liquidity", "资金价格或融资条件趋松", -1, "银行间市场", "趋松", "资金面", "短期"),
    "growth_outlook_strengthens": PredicateDefinition("growth", "增长预期增强", 1, "宏观经济", "增强", "增长预期", "中期"),
    "growth_outlook_weakens": PredicateDefinition("growth", "增长预期走弱", -1, "宏观经济", "走弱", "增长预期", "中期"),
    "inflation_pressure_rises": PredicateDefinition("inflation", "通胀压力上升", 1, "价格水平", "上升", "通胀压力", "中期"),
    "inflation_pressure_falls": PredicateDefinition("inflation", "通胀压力下降", -1, "价格水平", "下降", "通胀压力", "中期"),
    "government_bond_supply_rises": PredicateDefinition("bond_supply", "政府债券供给增加", 1, "财政部门", "增加", "政府债供给", "短中期"),
    "government_bond_supply_falls": PredicateDefinition("bond_supply", "政府债券供给减少", -1, "财政部门", "减少", "政府债供给", "短中期"),
    "risk_aversion_rises": PredicateDefinition("risk_appetite", "避险需求上升", -1, "金融市场", "上升", "避险需求", "短期"),
    "risk_aversion_falls": PredicateDefinition("risk_appetite", "风险偏好回升", 1, "金融市场", "回升", "风险偏好", "短期"),
}


def factor_dictionary() -> list[dict[str, str]]:
    return [{"name": name, **asdict(definition)} for name, definition in FACTOR_DEFINITIONS.items()]


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
        raise ValueError("收益率字段应使用百分数数值，例如1.85")
    if row["dr007_proxy_name"] != "FDR007_FIXING":
        raise ValueError("公开历史流动性代理必须明确标识为FDR007_FIXING")
    for field in ("cgb_source_url", "liquidity_source_url"):
        if not str(row[field]).startswith(("https://", "http://")):
            raise ValueError(f"{field}必须是可核验网址")
    for field in ("cgb_source_sha256", "liquidity_source_sha256"):
        if len(str(row[field])) != 64:
            raise ValueError(f"{field}必须是SHA-256")


def validate_text_row(row: dict[str, Any]) -> None:
    missing = [field for field in TEXT_FIELDS if str(row.get(field, "")).strip() == ""]
    if missing:
        raise ValueError("政策文本缺少字段：" + "、".join(missing))
    parse_datetime(str(row["publish_time"]))
    if not str(row["source_url"]).startswith(("https://", "http://")):
        raise ValueError("政策文本必须提供可核验source_url")
    if len(str(row["source_sha256"])) != 64:
        raise ValueError("政策文本必须提供SHA-256")


def validate_structured_row(row: dict[str, Any]) -> None:
    missing = [field for field in STRUCTURED_FIELDS if str(row.get(field, "")).strip() == ""]
    if missing:
        raise ValueError("结构化数据缺少字段：" + "、".join(missing))
    observation_date = date.fromisoformat(str(row["observation_date"]))
    period_start = date.fromisoformat(str(row["period_start"]))
    period_end = date.fromisoformat(str(row["period_end"]))
    if min(observation_date, period_start, period_end) < date(1990, 1, 1):
        raise ValueError("结构化数据日期疑似上游缺失值占位")
    if period_start > period_end:
        raise ValueError("结构化数据period_start不能晚于period_end")
    parse_datetime(str(row["release_time"]))
    if str(row["indicator"]) not in STRUCTURED_INDICATORS:
        raise ValueError("未知结构化指标：" + str(row["indicator"]))
    float(row["value"])
    if not str(row["source_url"]).startswith(("https://", "http://")):
        raise ValueError("结构化数据必须提供可核验source_url")
    if len(str(row["source_sha256"])) != 64:
        raise ValueError("结构化数据必须提供SHA-256")


def effective_trade_date(publish_time: str, trade_dates: Iterable[str]) -> str | None:
    """Map a public timestamp to the first close at which it may be used."""
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
