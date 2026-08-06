"""Rule-based event extraction for B-side offline sample data."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from src.research.scoring import evidence_score_breakdown


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"


CORE_OBJECT_BY_SECTOR = {
    "光伏": "光伏组件、逆变器与可再生能源消纳",
    "锂电": "动力电池与锂电材料",
    "风电": "风电设备订单与项目交付",
    "储能": "新型储能系统与逆变器",
    "整车": "新能源汽车销售与车型供给",
}


IMPACT_PATH_BY_SECTOR = {
    "光伏": "政策/需求变化→光伏装机与组件/逆变器需求→产业链关注提升",
    "锂电": "新能源汽车/储能需求→电池装车与材料需求→产业链景气度关注",
    "风电": "项目核准/招标改善→风机订单与交付→设备链关注提升",
    "储能": "新型储能建设→系统集成与逆变器需求→订单关注提升",
    "整车": "政策活动/需求变化→终端销量关注→整车与电池链条联动",
}


EVENT_KEYWORDS = {
    "regulatory_penalty": ["行政处罚", "立案调查", "纪律处分", "监管措施"],
    "inquiry_letter_pressure": ["问询函", "关注函", "监管函"],
    "earnings_quality_anomaly": ["业绩预亏", "业绩亏损", "资产减值", "会计差错", "财务造假"],
    "supply_chain_disruption": ["停产", "复产", "生产事故", "供应中断", "不可抗力"],
    "product_price_increase": ["产品涨价", "价格上调", "调高价格", "调价函"],
    "capacity_expansion": [
        "扩产",
        "新增产能",
        "产能建设",
        "项目投产",
        "投资建设",
        "建设项目",
        "重大合同",
        "中标项目",
        "订单落地",
        "募投项目",
    ],
}


POLICY_ACTION_KEYWORDS = [
    "行动方案",
    "实施方案",
    "补贴",
    "税收优惠",
    "以旧换新",
    "消纳责任权重",
    "并网",
    "市场交易",
    "试点示范",
    "指导意见",
]


NEWS_ATTENTION_KEYWORDS = [
    "装机量",
    "装车量",
    "渗透率",
    "出口量",
    "招标规模",
    "行业自律",
    "供需变化",
    "价格变化",
    "市场关注",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def source_evidence_text(doc: dict[str, str]) -> str:
    """Return source facts only, excluding AlphaLens' own project-association note."""
    content = doc["content"].split("项目关联：", 1)[0]
    return f'{doc["title"]} {content}'


def infer_event_type(doc: dict[str, str]) -> str | None:
    text = source_evidence_text(doc)
    source_type = doc["source_type"]

    if source_type == "ir_qa":
        # A single question is evidence, not proof that question pressure increased.
        return None
    if source_type == "policy":
        return "policy_support" if any(word in text for word in POLICY_ACTION_KEYWORDS) else None

    for event_type, keywords in EVENT_KEYWORDS.items():
        if any(word in text for word in keywords):
            return event_type

    if source_type == "news" and any(word in text for word in NEWS_ATTENTION_KEYWORDS):
        return "attention_spread"
    return None


def build_ir_pressure_documents(
    documents: dict[str, dict[str, str]],
    links_by_doc: dict[str, list[dict[str, str]]],
) -> set[tuple[str, str]]:
    """Identify question-pressure events only from company-level time windows."""
    dates_by_stock: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for doc_id, doc in documents.items():
        if doc["source_type"] != "ir_qa":
            continue
        publish_date = datetime.strptime(doc["publish_time"], "%Y-%m-%d")
        for link in links_by_doc.get(doc_id, []):
            dates_by_stock[link["stock_code"]].append((publish_date, doc_id))
    pressure: set[tuple[str, str]] = set()
    for stock_code, items in dates_by_stock.items():
        for current_date, doc_id in items:
            short_count = sum(current_date - timedelta(days=6) <= date <= current_date for date, _ in items)
            baseline_count = sum(
                current_date - timedelta(days=36) <= date < current_date - timedelta(days=6)
                for date, _ in items
            )
            if short_count >= 3 and short_count / 7 > max(baseline_count / 30, 0.05) * 2:
                pressure.add((doc_id, stock_code))
    return pressure


def _truncate_at_boundary(text: str, max_len: int) -> str:
    """Truncate at a sentence boundary instead of cutting mid-sentence."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for boundary in ("。", "；", ";", "！", "？"):
        index = cut.rfind(boundary)
        if index >= max_len * 0.5:
            return cut[: index + 1]
    return cut


def evidence_sentence(doc: dict[str, str], stock_name: str) -> str:
    text = doc["content"].split("项目关联：", 1)[0].replace("。", "。\n")
    priority_words = [stock_name, "政策", "订单", "产能", "储能", "电池", "光伏", "风电", "销量", "投资者"]
    for sentence in text.splitlines():
        if any(word in sentence for word in priority_words):
            return _truncate_at_boundary(sentence, 80)
    return _truncate_at_boundary(doc["title"], 80)


def infer_subject(doc: dict[str, str]) -> str:
    if doc["source_type"] == "policy":
        return doc["source_name"]
    if doc["source_type"] == "announcement":
        return doc["title"].split("披露")[0].split("发布")[0]
    if doc["source_type"] == "ir_qa":
        return "投资者与公司管理层"
    return doc["source_name"]


def build_events(
    documents: dict[str, dict[str, str]],
    links_by_doc: dict[str, list[dict[str, str]]],
) -> list[dict[str, object]]:
    """Pure read-only event construction for an arbitrary corpus (no file IO).

    Predicts what extract_events() would emit for a given document set and its
    entity links, so the 预演导入 precheck can preview prospective documents
    without touching any file under data/sample/.
    """
    ir_pressure = build_ir_pressure_documents(documents, links_by_doc)

    rows: list[dict[str, object]] = []
    event_idx = 1
    for doc_id in sorted(documents):
        doc = documents[doc_id]
        event_type = infer_event_type(doc)
        for link in links_by_doc.get(doc_id, []):
            current_event_id = f"E{event_idx:03d}"
            event_idx += 1
            current_event_type = event_type
            if doc["source_type"] == "ir_qa" and (doc_id, link["stock_code"]) in ir_pressure:
                current_event_type = "investor_question_pressure"
            if current_event_type is None:
                continue
            sector = link["industry"]
            event = {
                "event_id": current_event_id,
                "doc_id": doc_id,
                "stock_code": link["stock_code"],
                "event_type": current_event_type,
                "event_time": doc["publish_time"],
                "subject": infer_subject(doc),
                "object": CORE_OBJECT_BY_SECTOR[sector],
                "impact_path": IMPACT_PATH_BY_SECTOR[sector],
                "evidence_text": evidence_sentence(doc, link["stock_name"]),
            }
            breakdown = evidence_score_breakdown(doc, event, link)
            event["evidence_strength"] = f"{breakdown['score']:.2f}"
            rows.append(event)
    return rows


def extract_events() -> list[dict[str, object]]:
    """Construct events for the live raw_documents.csv corpus."""
    documents = {doc["doc_id"]: doc for doc in read_csv(SAMPLE_DIR / "raw_documents.csv")}
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in read_csv(SAMPLE_DIR / "entity_links.csv"):
        links_by_doc[link["doc_id"]].append(link)
    return build_events(documents, links_by_doc)


def main() -> None:
    fieldnames = [
        "event_id",
        "doc_id",
        "stock_code",
        "event_type",
        "event_time",
        "subject",
        "object",
        "impact_path",
        "evidence_text",
        "evidence_strength",
    ]
    with (SAMPLE_DIR / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(extract_events())


if __name__ == "__main__":
    main()
