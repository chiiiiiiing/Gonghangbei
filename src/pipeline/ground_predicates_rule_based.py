"""Rule-based predicate grounding shared by batch and live analysis."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"


PREDICATES = [
    "has_policy_support",
    "policy_directly_related_to_business",
    "event_mentions_core_product",
    "evidence_from_authoritative_source",
    "source_government_or_exchange",
    "source_company_announcement",
    "source_major_media",
    "social_attention_spikes",
    "policy_attention_followup",
    "institutional_attention_increases",
    "investor_questions_increase",
    "management_response_vague",
    "announcement_contains_uncertainty",
    "risk_or_uncertainty_disclosure",
    "demand_side_policy",
    "supply_side_policy",
    "capacity_policy_support",
    "event_evidence_strength",
    "event_has_short_term_price_impact",
]


CORE_PRODUCT_KEYWORDS = {
    "光伏": ["光伏", "硅料", "硅片", "电池", "组件", "N 型", "TOPCon", "HJT"],
    "锂电": ["动力电池", "锂电", "电池", "材料", "隔膜", "钠离子", "装车量"],
    "风电": ["风电", "海上风电", "风机", "叶片", "机组"],
    "储能": ["储能", "逆变器", "系统集成", "大储", "户储"],
    "整车": ["新能源汽车", "整车", "车型", "销量", "出口", "插混"],
}


PRICE_IMPACT_SCORE = {
    "policy_support": 0.78,
    "attention_spread": 0.68,
    "capacity_expansion": 0.63,
    "product_price_increase": 0.66,
    "investor_question_pressure": 0.50,
    "regulatory_penalty": 0.62,
    "inquiry_letter_pressure": 0.58,
    "earnings_quality_anomaly": 0.64,
    "supply_chain_disruption": 0.61,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def bool_value(value: bool) -> str:
    return "true" if value else "false"


def add_predicate(
    rows: list[dict[str, object]],
    event_id: str,
    name: str,
    value: str,
    confidence: float,
    rationale: str,
) -> None:
    rows.append(
        {
            "event_id": event_id,
            "predicate_name": name,
            "value": value,
            "confidence": f"{confidence:.2f}",
            "rationale": rationale,
        }
    )


def source_is_authoritative(source_type: str, source_name: str) -> bool:
    return source_type in {"policy", "announcement", "ir_qa"} or source_name in {
        "证券时报",
        "上海证券报",
        "中国证券报",
        "21 世纪经济报道",
    }


def source_is_government_or_exchange(source_type: str, source_name: str) -> bool:
    return source_type == "policy" or any(
        keyword in source_name
        for keyword in ["中国政府网", "国务院", "发改委", "工信部", "财政部", "商务部", "上交所", "深交所"]
    )


def source_is_major_media(source_type: str, source_name: str) -> bool:
    return source_type == "news" and source_name in {
        "证券时报",
        "上海证券报",
        "中国证券报",
        "21 世纪经济报道",
    }


def ground_event_predicates(
    event: dict[str, str],
    doc: dict[str, str],
    sector: str,
) -> list[dict[str, object]]:
    """Ground the locked predicate schema for one event without writing files."""
    rows: list[dict[str, object]] = []
    source_content = doc["content"].split("项目关联：", 1)[0]
    full_text = f'{doc["title"]} {source_content}'
    mentions_core_product = any(word in full_text for word in CORE_PRODUCT_KEYWORDS[sector])
    event_type = event["event_type"]
    has_policy = event_type == "policy_support"
    source_type = doc["source_type"]
    source_name = doc["source_name"]
    source_authoritative = source_is_authoritative(source_type, source_name)
    government_or_exchange = source_is_government_or_exchange(source_type, source_name)
    company_announcement = source_type == "announcement"
    major_media = source_is_major_media(source_type, source_name)
    attention_spike = event_type == "attention_spread" and any(
        word in full_text
        for word in ["装机量", "装车量", "渗透率", "出口量", "招标规模", "市场关注", "多家"]
    )
    policy_followup = (has_policy or event_type == "attention_spread") and any(
        word in full_text
        for word in [
            "行动方案",
            "以旧换新",
            "购置税",
            "补贴",
            "装机量",
            "装车量",
            "渗透率",
            "出口量",
            "招标规模",
            "市场关注",
            "多家",
        ]
    )
    investor_questions = event_type == "investor_question_pressure"
    vague_response = investor_questions and any(word in full_text for word in ["以公司公告", "需结合", "可能影响"])
    uncertain_announcement = company_announcement and any(
        word in full_text for word in ["不确定", "风险", "可能影响", "提示"]
    )
    risk_disclosure = uncertain_announcement or event_type in {
        "regulatory_penalty",
        "inquiry_letter_pressure",
        "earnings_quality_anomaly",
        "supply_chain_disruption",
    }
    institutional_attention = any(word in full_text for word in ["机构", "调研", "研报"])
    demand_policy = has_policy and any(
        word in full_text for word in ["消费", "购置税", "补贴", "以旧换新", "销量", "需求", "车辆购置"]
    )
    supply_policy = has_policy and any(
        word in full_text for word in ["供给", "产业链", "制造", "设备更新", "技术改造", "绿色制造"]
    )
    capacity_policy = (
        has_policy and any(word in full_text for word in ["产能", "扩产", "项目", "设备更新"])
    ) or (event_type == "capacity_expansion" and source_authoritative)

    values = [
        ("has_policy_support", has_policy, 0.96, 0.88, "事件类型为 policy_support", "事件不是政策利好类型"),
        (
            "policy_directly_related_to_business",
            has_policy and mentions_core_product,
            0.90,
            0.78,
            f"政策原文证据明确涉及{sector}主营业务或核心产品",
            f"政策原文证据未明确涉及{sector}主营业务或核心产品",
        ),
        ("event_mentions_core_product", mentions_core_product, 0.88, 0.74, "文本提及核心产品/业务关键词", "文本未显式提及核心产品关键词"),
        ("evidence_from_authoritative_source", source_authoritative, 0.94, 0.72, f"来源为{source_name}", f"来源为{source_name}"),
        ("source_government_or_exchange", government_or_exchange, 0.95, 0.76, "来源为政府、部委或交易所", "来源不是政府、部委或交易所"),
        ("source_company_announcement", company_announcement, 0.94, 0.78, "来源类型为公司公告", "来源不是公司公告"),
        ("source_major_media", major_media, 0.90, 0.76, "来源为主流财经媒体", "来源不是主流财经媒体"),
        ("social_attention_spikes", attention_spike, 0.82, 0.68, "新闻原文包含可量化或多源关注扩散线索", "单条来源不足以证明关注度显著上升"),
        ("policy_attention_followup", policy_followup, 0.84, 0.70, "政策或主题文本包含后续关注/扩散线索", "未发现政策后续关注扩散线索"),
        ("institutional_attention_increases", institutional_attention, 0.76, 0.70, "文本出现机构关注线索", "文本未出现机构调研或研报线索"),
        ("investor_questions_increase", investor_questions, 0.86, 0.82, "事件已由时间窗聚合证据确认提问增加", "未发现时间窗内提问数量增加的聚合证据"),
        ("management_response_vague", vague_response, 0.80, 0.76, "回复包含以公告为准、需结合情况或可能影响等表述", "未发现明显模糊回复"),
        ("announcement_contains_uncertainty", uncertain_announcement, 0.84, 0.78, "公告摘要包含风险或不确定性提示", "未发现公告不确定性提示"),
        ("risk_or_uncertainty_disclosure", risk_disclosure, 0.86, 0.78, "事件属于风险披露、问询、业绩异常或供应链扰动", "未发现集中风险披露线索"),
        ("demand_side_policy", demand_policy, 0.86, 0.74, "政策作用于消费、补贴或终端需求", "政策未明确作用于需求侧"),
        ("supply_side_policy", supply_policy, 0.84, 0.74, "政策作用于供给、制造或产业链升级", "政策未明确作用于供给侧"),
        ("capacity_policy_support", capacity_policy, 0.84, 0.74, "政策或权威事件与产能/项目建设相关", "未发现产能政策支持线索"),
    ]
    for name, value, true_conf, false_conf, true_reason, false_reason in values:
        add_predicate(rows, event["event_id"], name, bool_value(value), true_conf if value else false_conf, true_reason if value else false_reason)

    add_predicate(
        rows,
        event["event_id"],
        "event_evidence_strength",
        f"{float(event['evidence_strength']):.2f}",
        0.92,
        "沿用事件抽取阶段的证据强度评分",
    )
    add_predicate(
        rows,
        event["event_id"],
        "event_has_short_term_price_impact",
        f"{PRICE_IMPACT_SCORE.get(event_type, 0.55):.2f}",
        0.70,
        "基于事件类型的先验市场反应强度，用于规则归纳初始特征",
    )
    return rows


def ground_predicates() -> list[dict[str, object]]:
    documents = {doc["doc_id"]: doc for doc in read_csv(SAMPLE_DIR / "raw_documents.csv")}
    stock_pool = {row["stock_code"]: row for row in read_csv(SAMPLE_DIR / "stock_pool.csv")}
    rows: list[dict[str, object]] = []
    for event in read_csv(SAMPLE_DIR / "events.csv"):
        rows.extend(ground_event_predicates(event, documents[event["doc_id"]], stock_pool[event["stock_code"]]["industry_sector"]))
    return rows


def main() -> None:
    fieldnames = ["event_id", "predicate_name", "value", "confidence", "rationale"]
    with (SAMPLE_DIR / "predicates.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ground_predicates())


if __name__ == "__main__":
    main()
