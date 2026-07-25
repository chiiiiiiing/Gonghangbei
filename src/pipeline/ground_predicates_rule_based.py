"""Rule-based predicate grounding for AlphaLens sample events."""

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


def ground_predicates() -> list[dict[str, object]]:
    documents = {doc["doc_id"]: doc for doc in read_csv(SAMPLE_DIR / "raw_documents.csv")}
    stock_pool = {row["stock_code"]: row for row in read_csv(SAMPLE_DIR / "stock_pool.csv")}
    events = read_csv(SAMPLE_DIR / "events.csv")
    rows: list[dict[str, object]] = []

    for event in events:
        doc = documents[event["doc_id"]]
        sector = stock_pool[event["stock_code"]]["industry_sector"]
        source_content = doc["content"].split("项目关联：", 1)[0]
        full_text = f'{doc["title"]} {source_content}'
        core_keywords = CORE_PRODUCT_KEYWORDS[sector]
        mentions_core_product = any(keyword in full_text for keyword in core_keywords)
        has_policy = event["event_type"] == "policy_support"
        source_authoritative = source_is_authoritative(doc["source_type"], doc["source_name"])
        government_or_exchange = source_is_government_or_exchange(doc["source_type"], doc["source_name"])
        company_announcement = doc["source_type"] == "announcement"
        major_media = source_is_major_media(doc["source_type"], doc["source_name"])
        attention_spike = event["event_type"] == "attention_spread" and any(
            word in full_text
            for word in ["装机量", "装车量", "渗透率", "出口量", "招标规模", "市场关注", "多家"]
        )
        policy_followup = (has_policy or event["event_type"] == "attention_spread") and any(
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
        investor_questions = event["event_type"] == "investor_question_pressure"
        vague_response = investor_questions and any(word in full_text for word in ["以公司公告", "需结合", "可能影响"])
        uncertain_announcement = doc["source_type"] == "announcement" and any(
            word in full_text for word in ["不确定", "风险", "可能影响", "提示"]
        )
        risk_disclosure = uncertain_announcement or event["event_type"] in {
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
        capacity_policy = (has_policy and any(word in full_text for word in ["产能", "扩产", "项目", "设备更新"])) or (
            event["event_type"] == "capacity_expansion" and source_authoritative
        )

        add_predicate(
            rows,
            event["event_id"],
            "has_policy_support",
            bool_value(has_policy),
            0.96 if has_policy else 0.88,
            "事件类型为 policy_support" if has_policy else "事件不是政策利好类型",
        )
        add_predicate(
            rows,
            event["event_id"],
            "policy_directly_related_to_business",
            bool_value(has_policy and mentions_core_product),
            0.90 if has_policy and mentions_core_product else 0.78,
            f"政策原文证据{'明确' if has_policy and mentions_core_product else '未明确'}涉及{sector}主营业务或核心产品",
        )
        add_predicate(
            rows,
            event["event_id"],
            "event_mentions_core_product",
            bool_value(mentions_core_product),
            0.88 if mentions_core_product else 0.74,
            "文本提及核心产品/业务关键词" if mentions_core_product else "文本未显式提及核心产品关键词",
        )
        add_predicate(
            rows,
            event["event_id"],
            "evidence_from_authoritative_source",
            bool_value(source_authoritative),
            0.94 if source_authoritative else 0.72,
            f"来源为{doc['source_name']}",
        )
        add_predicate(
            rows,
            event["event_id"],
            "source_government_or_exchange",
            bool_value(government_or_exchange),
            0.95 if government_or_exchange else 0.76,
            "来源为政府、部委或交易所" if government_or_exchange else "来源不是政府、部委或交易所",
        )
        add_predicate(
            rows,
            event["event_id"],
            "source_company_announcement",
            bool_value(company_announcement),
            0.94 if company_announcement else 0.78,
            "来源类型为公司公告" if company_announcement else "来源不是公司公告",
        )
        add_predicate(
            rows,
            event["event_id"],
            "source_major_media",
            bool_value(major_media),
            0.90 if major_media else 0.76,
            "来源为主流财经媒体" if major_media else "来源不是主流财经媒体",
        )
        add_predicate(
            rows,
            event["event_id"],
            "social_attention_spikes",
            bool_value(attention_spike),
            0.82 if attention_spike else 0.68,
            "新闻原文包含可量化或多源关注扩散线索" if attention_spike else "单条来源不足以证明关注度显著上升",
        )
        add_predicate(
            rows,
            event["event_id"],
            "policy_attention_followup",
            bool_value(policy_followup),
            0.84 if policy_followup else 0.70,
            "政策或主题文本包含后续关注/扩散线索" if policy_followup else "未发现政策后续关注扩散线索",
        )
        add_predicate(
            rows,
            event["event_id"],
            "institutional_attention_increases",
            bool_value(institutional_attention),
            0.76 if institutional_attention else 0.70,
            "文本出现机构关注线索" if institutional_attention else "文本未出现机构调研或研报线索",
        )
        add_predicate(
            rows,
            event["event_id"],
            "investor_questions_increase",
            bool_value(investor_questions),
            0.86 if investor_questions else 0.82,
            "事件已由时间窗聚合证据确认提问增加" if investor_questions else "未发现时间窗内提问数量增加的聚合证据",
        )
        add_predicate(
            rows,
            event["event_id"],
            "management_response_vague",
            bool_value(vague_response),
            0.80 if vague_response else 0.76,
            "回复包含以公告为准、需结合情况或可能影响等表述" if vague_response else "未发现明显模糊回复",
        )
        add_predicate(
            rows,
            event["event_id"],
            "announcement_contains_uncertainty",
            bool_value(uncertain_announcement),
            0.84 if uncertain_announcement else 0.78,
            "公告摘要包含风险或不确定性提示" if uncertain_announcement else "未发现公告不确定性提示",
        )
        add_predicate(
            rows,
            event["event_id"],
            "risk_or_uncertainty_disclosure",
            bool_value(risk_disclosure),
            0.86 if risk_disclosure else 0.78,
            "事件属于风险披露、问询、业绩异常或供应链扰动" if risk_disclosure else "未发现集中风险披露线索",
        )
        add_predicate(
            rows,
            event["event_id"],
            "demand_side_policy",
            bool_value(demand_policy),
            0.86 if demand_policy else 0.74,
            "政策作用于消费、补贴或终端需求" if demand_policy else "政策未明确作用于需求侧",
        )
        add_predicate(
            rows,
            event["event_id"],
            "supply_side_policy",
            bool_value(supply_policy),
            0.84 if supply_policy else 0.74,
            "政策作用于供给、制造或产业链升级" if supply_policy else "政策未明确作用于供给侧",
        )
        add_predicate(
            rows,
            event["event_id"],
            "capacity_policy_support",
            bool_value(capacity_policy),
            0.84 if capacity_policy else 0.74,
            "政策或权威事件与产能/项目建设相关" if capacity_policy else "未发现产能政策支持线索",
        )
        add_predicate(
            rows,
            event["event_id"],
            "event_evidence_strength",
            f"{float(event['evidence_strength']):.2f}",
            0.92,
            "沿用事件抽取阶段的证据强度评分",
        )
        impact_score = PRICE_IMPACT_SCORE.get(event["event_type"], 0.55)
        add_predicate(
            rows,
            event["event_id"],
            "event_has_short_term_price_impact",
            f"{impact_score:.2f}",
            0.70,
            "基于事件类型的先验市场反应强度，用于规则归纳初始特征",
        )

    return rows


def main() -> None:
    fieldnames = ["event_id", "predicate_name", "value", "confidence", "rationale"]
    with (SAMPLE_DIR / "predicates.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ground_predicates())


if __name__ == "__main__":
    main()
