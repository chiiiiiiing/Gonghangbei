"""Rule-based event extraction for B-side offline sample data."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


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


SOURCE_STRENGTH = {
    "policy": 0.92,
    "announcement": 0.88,
    "news": 0.76,
    "ir_qa": 0.72,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def infer_event_type(doc: dict[str, str]) -> str:
    text = f'{doc["title"]} {doc["content"]}'
    if doc["source_type"] == "policy":
        return "policy_support"
    if doc["source_type"] == "ir_qa":
        return "investor_question_pressure"
    if doc["source_type"] == "announcement" and any(
        word in text for word in ["产能", "投产", "项目", "订单", "交付", "技改", "建设"]
    ):
        return "capacity_expansion"
    if any(word in text for word in ["价格", "涨价", "企稳"]):
        return "product_price_increase"
    if any(word in text for word in ["产能", "投产", "项目", "订单", "交付", "技改", "建设"]):
        return "capacity_expansion"
    return "attention_spread"


def evidence_sentence(doc: dict[str, str], stock_name: str) -> str:
    text = doc["content"].replace("。", "。\n")
    priority_words = [stock_name, "政策", "订单", "产能", "储能", "电池", "光伏", "风电", "销量", "投资者"]
    for sentence in text.splitlines():
        if any(word in sentence for word in priority_words):
            return sentence[:80]
    return doc["title"][:80]


def infer_subject(doc: dict[str, str]) -> str:
    if doc["source_type"] == "policy":
        return doc["source_name"]
    if doc["source_type"] == "announcement":
        return doc["title"].split("披露")[0].split("发布")[0]
    if doc["source_type"] == "ir_qa":
        return "投资者与公司管理层"
    return doc["source_name"]


def extract_events() -> list[dict[str, object]]:
    documents = {doc["doc_id"]: doc for doc in read_csv(SAMPLE_DIR / "raw_documents.csv")}
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in read_csv(SAMPLE_DIR / "entity_links.csv"):
        links_by_doc[link["doc_id"]].append(link)

    rows: list[dict[str, object]] = []
    event_idx = 1
    for doc_id in sorted(documents):
        doc = documents[doc_id]
        event_type = infer_event_type(doc)
        for link in links_by_doc.get(doc_id, []):
            sector = link["industry"]
            strength = SOURCE_STRENGTH[doc["source_type"]]
            if event_type in {"policy_support", "capacity_expansion"}:
                strength += 0.02
            rows.append(
                {
                    "event_id": f"E{event_idx:03d}",
                    "doc_id": doc_id,
                    "stock_code": link["stock_code"],
                    "event_type": event_type,
                    "event_time": doc["publish_time"],
                    "subject": infer_subject(doc),
                    "object": CORE_OBJECT_BY_SECTOR[sector],
                    "impact_path": IMPACT_PATH_BY_SECTOR[sector],
                    "evidence_text": evidence_sentence(doc, link["stock_name"]),
                    "evidence_strength": f"{min(strength, 0.98):.2f}",
                }
            )
            event_idx += 1
    return rows


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
