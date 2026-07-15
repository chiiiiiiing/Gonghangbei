"""Export preliminary explanation cases for B/A handoff."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
CASE_PATH = VIEW_DIR / "解释案例草稿.md"


def today() -> str:
    return date.today().isoformat()


CASE_KEYS = [
    ("S001", "002594"),
    ("S002", "300750"),
    ("S003", "601012"),
    ("S006", "300750"),
    ("S009", "300274"),
    ("S018", "688063"),
]


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def forward_return(
    market_by_stock: dict[str, list[dict[str, str]]],
    stock_code: str,
    event_time: str,
    horizon: int = 5,
) -> tuple[str, str, float] | None:
    rows = [row for row in market_by_stock.get(stock_code, []) if row["trade_date"] > event_time]
    if len(rows) < horizon:
        return None
    entry = rows[0]
    exit_row = rows[horizon - 1]
    ret = float(exit_row["close"]) / float(entry["open"]) - 1
    return entry["trade_date"], exit_row["trade_date"], ret


def main() -> None:
    docs = {row["doc_id"]: row for row in read_csv("raw_documents.csv")}
    stocks = {row["stock_code"]: row for row in read_csv("stock_pool.csv")}
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    market_rows = read_csv("market_data.csv")
    market_by_stock: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in market_rows:
        market_by_stock[row["stock_code"]].append(row)
    for rows in market_by_stock.values():
        rows.sort(key=lambda row: row["trade_date"])

    event_by_key = {(row["doc_id"], row["stock_code"]): row for row in events}
    selected_keys = [key for key in CASE_KEYS if key in event_by_key]
    seen_keys = set(selected_keys)
    for event in events:
        key = (event["doc_id"], event["stock_code"])
        if key in seen_keys:
            continue
        selected_keys.append(key)
        seen_keys.add(key)
        if len(selected_keys) >= len(CASE_KEYS):
            break

    pred_by_event: dict[str, dict[str, str]] = defaultdict(dict)
    for row in predicates:
        pred_by_event[row["event_id"]][row["predicate_name"]] = row["value"]

    lines = [
        "# AlphaLens B 线解释案例草稿",
        "",
        f"生成日期：{today()}",
        "",
        "说明：以下收益路径使用当前 `data/sample/market_data.csv`，仅用于验证事件日之后的收益对齐逻辑；正式答辩仍需人工确认行情复权口径和案例叙事。",
        "",
        "本报告仅供研究参考，不构成投资建议",
        "",
    ]

    if not selected_keys:
        lines.extend(["暂无可导出的事件案例，请先生成 `events.csv`。", ""])

    for idx, key in enumerate(selected_keys, start=1):
        event = event_by_key[key]
        doc = docs[event["doc_id"]]
        stock = stocks[event["stock_code"]]
        path = forward_return(market_by_stock, event["stock_code"], event["event_time"])
        if path:
            entry_date, exit_date, ret = path
            return_text = f"5 日窗口：{entry_date} 开盘至 {exit_date} 收盘，收益 {ret * 100:.2f}%"
            outcome_text = "窗口收益为正的研究样本" if ret > 0 else "窗口收益为负的研究样本"
        else:
            return_text = "行情窗口不足或事件过近，待后续行情补齐"
            outcome_text = "待补案例"
        pred = pred_by_event[event["event_id"]]
        lines.extend(
            [
                f"## 案例 {idx}：{doc['title']} / {stock['stock_name']}",
                "",
                "### 原文摘要",
                "",
                doc["content"],
                "",
                "### 抽取结果",
                "",
                f"- 事件 ID：{event['event_id']}",
                f"- 事件类型：{event['event_type']}",
                f"- 关联股票：{event['stock_code']} {stock['stock_name']}（{stock['industry_sector']}）",
                f"- 事件时间：{event['event_time']}",
                f"- 证据强度：{event['evidence_strength']}",
                f"- 影响路径：{event['impact_path']}",
                f"- 证据片段：{event['evidence_text']}",
                "",
                "### 谓词结果",
                "",
                f"- has_policy_support: {pred.get('has_policy_support', '')}",
                f"- policy_directly_related_to_business: {pred.get('policy_directly_related_to_business', '')}",
                f"- evidence_from_authoritative_source: {pred.get('evidence_from_authoritative_source', '')}",
                f"- social_attention_spikes: {pred.get('social_attention_spikes', '')}",
                f"- event_evidence_strength: {pred.get('event_evidence_strength', '')}",
                f"- event_has_short_term_price_impact: {pred.get('event_has_short_term_price_impact', '')}",
                "",
                "### 收益路径",
                "",
                f"- 案例分类：{outcome_text}",
                f"- {return_text}",
                "",
                "### 金融逻辑",
                "",
                "该案例展示文本如何先被约束为事件与谓词，再交给 C 端做规则归纳、因子生成和回测审计；它不是股价预测，也不是投资建议。",
                "",
            ]
        )

    CASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASE_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Explanation cases written to {CASE_PATH}")


if __name__ == "__main__":
    main()
