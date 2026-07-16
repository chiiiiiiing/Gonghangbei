"""Validate AlphaLens B-side CSV files and write a data quality report."""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
REPORT_PATH = ROOT / "查看材料" / "数据质量报告.md"


def today() -> str:
    return date.today().isoformat()


SCHEMAS = {
    "stock_pool.csv": ["stock_code", "stock_name", "industry_sector", "market_cap"],
    "raw_documents.csv": ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"],
    "entity_links.csv": ["doc_id", "stock_code", "stock_name", "industry", "confidence", "evidence"],
    "events.csv": [
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
    ],
    "predicates.csv": ["event_id", "predicate_name", "value", "confidence", "rationale"],
    "market_data.csv": ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"],
}

SOURCE_TYPES = {"policy", "announcement", "news", "ir_qa"}
SECTORS = {"光伏", "锂电", "风电", "储能", "整车"}
EVENT_TYPES = {
    "policy_support",
    "regulatory_penalty",
    "inquiry_letter_pressure",
    "earnings_quality_anomaly",
    "product_price_increase",
    "supply_chain_disruption",
    "capacity_expansion",
    "investor_question_pressure",
    "attention_spread",
}
PREDICATES = {
    "has_policy_support",
    "policy_directly_related_to_business",
    "event_mentions_core_product",
    "evidence_from_authoritative_source",
    "social_attention_spikes",
    "institutional_attention_increases",
    "investor_questions_increase",
    "management_response_vague",
    "announcement_contains_uncertainty",
    "event_evidence_strength",
    "event_has_short_term_price_impact",
}
MVP_PREDICATES = {
    "has_policy_support",
    "policy_directly_related_to_business",
    "evidence_from_authoritative_source",
    "social_attention_spikes",
    "event_evidence_strength",
    "event_has_short_term_price_impact",
}
BOOLEAN_PREDICATES = PREDICATES - {"event_evidence_strength", "event_has_short_term_price_impact"}
SCORE_PREDICATES = {"event_evidence_strength", "event_has_short_term_price_impact"}

COMPLETENESS_FILES = [
    "stock_pool.csv",
    "raw_documents.csv",
    "entity_links.csv",
    "events.csv",
    "predicates.csv",
    "market_data.csv",
    "predicate_matrix.csv",
    "event_forward_returns.csv",
    "rules.csv",
    "factors.csv",
    "factor_snapshot.csv",
    "backtest_metrics.csv",
]


def read_csv(filename: str) -> tuple[list[str], list[dict[str, str]]]:
    path = SAMPLE_DIR / filename
    if not path.exists():
        return [], []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def in_range(value: str, low: float, high: float) -> bool:
    return is_float(value) and low <= float(value) <= high


def add_unique_errors(
    errors: list[str],
    rows: list[dict[str, str]],
    keys: list[str],
    filename: str,
) -> None:
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(row[k] for k in keys)
        if key in seen:
            errors.append(f"{filename}: duplicate key {keys}={key}")
        seen.add(key)


def validate() -> tuple[list[str], list[str], dict[str, list[dict[str, str]]]]:
    errors: list[str] = []
    warnings: list[str] = []
    tables: dict[str, list[dict[str, str]]] = {}

    for filename, schema in SCHEMAS.items():
        header, rows = read_csv(filename)
        tables[filename] = rows
        if not header:
            errors.append(f"{filename}: missing file or empty header")
            continue
        if header != schema:
            errors.append(f"{filename}: header mismatch. expected {schema}, got {header}")

    stock_rows = tables["stock_pool.csv"]
    stock_codes = {row["stock_code"] for row in stock_rows}
    stock_sector = {row["stock_code"]: row["industry_sector"] for row in stock_rows}
    doc_rows = tables["raw_documents.csv"]
    doc_ids = {row["doc_id"] for row in doc_rows}
    doc_dates = {row["doc_id"]: parse_date(row["publish_time"]) for row in doc_rows}
    event_rows = tables["events.csv"]
    event_ids = {row["event_id"] for row in event_rows}
    market_rows = tables["market_data.csv"]

    add_unique_errors(errors, stock_rows, ["stock_code"], "stock_pool.csv")
    add_unique_errors(errors, doc_rows, ["doc_id"], "raw_documents.csv")
    add_unique_errors(errors, tables["entity_links.csv"], ["doc_id", "stock_code"], "entity_links.csv")
    add_unique_errors(errors, event_rows, ["event_id"], "events.csv")
    add_unique_errors(errors, tables["predicates.csv"], ["event_id", "predicate_name"], "predicates.csv")
    add_unique_errors(errors, market_rows, ["trade_date", "stock_code"], "market_data.csv")

    if len(stock_rows) != 30:
        errors.append(f"stock_pool.csv: expected 30 rows, got {len(stock_rows)}")
    sector_counts = Counter(row["industry_sector"] for row in stock_rows)
    for sector in SECTORS:
        if sector_counts[sector] < 5:
            errors.append(f"stock_pool.csv: sector {sector} has {sector_counts[sector]} rows, expected at least 5")
    for row in stock_rows:
        if not re.fullmatch(r"\d{6}", row["stock_code"]):
            errors.append(f"stock_pool.csv: invalid stock_code {row['stock_code']}")
        if row["industry_sector"] not in SECTORS:
            errors.append(f"stock_pool.csv: invalid industry_sector {row['industry_sector']}")
        if not is_float(row["market_cap"]) or float(row["market_cap"]) <= 0:
            errors.append(f"stock_pool.csv: invalid market_cap for {row['stock_code']}")

    if len(doc_rows) < 100:
        warnings.append(f"raw_documents.csv: sample has {len(doc_rows)} rows; final MVP target is at least 100")
    candidate_doc_count = sum(1 for row in doc_rows if "待人工核验" in row["content"])
    if candidate_doc_count:
        warnings.append(
            f"raw_documents.csv: {candidate_doc_count} rows are candidate summaries pending manual source verification"
        )
    for row in doc_rows:
        if not re.fullmatch(r"S\d{3}", row["doc_id"]):
            errors.append(f"raw_documents.csv: invalid doc_id {row['doc_id']}")
        if row["source_type"] not in SOURCE_TYPES:
            errors.append(f"raw_documents.csv: invalid source_type {row['source_type']}")
        if parse_date(row["publish_time"]) is None:
            errors.append(f"raw_documents.csv: invalid publish_time {row['publish_time']}")
        elif not ("2024-01-01" <= row["publish_time"] <= "2026-06-30"):
            errors.append(f"raw_documents.csv: publish_time out of MVP range for {row['doc_id']}")
        if len(row["content"]) < 50:
            errors.append(f"raw_documents.csv: content too short for {row['doc_id']}")

    for row in tables["entity_links.csv"]:
        if row["doc_id"] not in doc_ids:
            errors.append(f"entity_links.csv: unknown doc_id {row['doc_id']}")
        if row["stock_code"] not in stock_codes:
            errors.append(f"entity_links.csv: unknown stock_code {row['stock_code']}")
        if row["industry"] != stock_sector.get(row["stock_code"]):
            errors.append(f"entity_links.csv: industry mismatch for {row['doc_id']} {row['stock_code']}")
        if not in_range(row["confidence"], 0, 1):
            errors.append(f"entity_links.csv: confidence out of range for {row['doc_id']} {row['stock_code']}")

    for row in event_rows:
        if not re.fullmatch(r"E\d{3}", row["event_id"]):
            errors.append(f"events.csv: invalid event_id {row['event_id']}")
        if row["doc_id"] not in doc_ids:
            errors.append(f"events.csv: unknown doc_id {row['doc_id']}")
        if row["stock_code"] not in stock_codes:
            errors.append(f"events.csv: unknown stock_code {row['stock_code']}")
        if row["event_type"] not in EVENT_TYPES:
            errors.append(f"events.csv: invalid event_type {row['event_type']}")
        event_date = parse_date(row["event_time"])
        if event_date is None:
            errors.append(f"events.csv: invalid event_time {row['event_time']}")
        elif doc_dates.get(row["doc_id"]) and event_date < doc_dates[row["doc_id"]]:
            errors.append(f"events.csv: event_time before publish_time for {row['event_id']}")
        if not in_range(row["evidence_strength"], 0, 1):
            errors.append(f"events.csv: evidence_strength out of range for {row['event_id']}")

    predicate_names_by_event: dict[str, set[str]] = {event_id: set() for event_id in event_ids}
    for row in tables["predicates.csv"]:
        event_id = row["event_id"]
        name = row["predicate_name"]
        if event_id not in event_ids:
            errors.append(f"predicates.csv: unknown event_id {event_id}")
        if name not in PREDICATES:
            errors.append(f"predicates.csv: invalid predicate_name {name}")
        predicate_names_by_event.setdefault(event_id, set()).add(name)
        if not in_range(row["confidence"], 0, 1):
            errors.append(f"predicates.csv: confidence out of range for {event_id}/{name}")
        if name in BOOLEAN_PREDICATES and row["value"] not in {"true", "false"}:
            errors.append(f"predicates.csv: boolean predicate {event_id}/{name} has invalid value {row['value']}")
        if name in SCORE_PREDICATES and not in_range(row["value"], 0, 1):
            errors.append(f"predicates.csv: score predicate {event_id}/{name} out of range")
        if not row["rationale"]:
            errors.append(f"predicates.csv: empty rationale for {event_id}/{name}")
    for event_id, names in predicate_names_by_event.items():
        missing = sorted(MVP_PREDICATES - names)
        if missing:
            errors.append(f"predicates.csv: event {event_id} missing MVP predicates {missing}")

    max_market_date_by_stock: dict[str, str] = {}
    market_dates_by_stock: dict[str, set[str]] = {stock_code: set() for stock_code in stock_codes}
    for row in market_rows:
        if parse_date(row["trade_date"]) is None:
            errors.append(f"market_data.csv: invalid trade_date {row['trade_date']}")
        elif not ("2024-01-01" <= row["trade_date"] <= "2026-06-30"):
            errors.append(f"market_data.csv: trade_date out of MVP range {row['trade_date']}")
        if row["stock_code"] not in stock_codes:
            errors.append(f"market_data.csv: unknown stock_code {row['stock_code']}")
        for field in ["open", "high", "low", "close", "adj_factor"]:
            if not is_float(row[field]) or float(row[field]) <= 0:
                errors.append(f"market_data.csv: invalid {field} for {row['trade_date']} {row['stock_code']}")
        if not is_float(row["volume"]) or float(row["volume"]) < 0:
            errors.append(f"market_data.csv: invalid volume for {row['trade_date']} {row['stock_code']}")
        if all(is_float(row[field]) for field in ["open", "high", "low", "close"]):
            open_price = float(row["open"])
            high_price = float(row["high"])
            low_price = float(row["low"])
            close_price = float(row["close"])
            if high_price < max(open_price, low_price, close_price):
                errors.append(f"market_data.csv: high below OHLC value for {row['trade_date']} {row['stock_code']}")
            if low_price > min(open_price, high_price, close_price):
                errors.append(f"market_data.csv: low above OHLC value for {row['trade_date']} {row['stock_code']}")
        if row["stock_code"] not in max_market_date_by_stock or row["trade_date"] > max_market_date_by_stock[row["stock_code"]]:
            max_market_date_by_stock[row["stock_code"]] = row["trade_date"]
        if row["stock_code"] in market_dates_by_stock:
            market_dates_by_stock[row["stock_code"]].add(row["trade_date"])
    expected_market_days = max((len(dates) for dates in market_dates_by_stock.values()), default=0)
    for stock_code, dates in sorted(market_dates_by_stock.items()):
        if not dates:
            errors.append(f"market_data.csv: missing all rows for stock {stock_code}")
        elif len(dates) < expected_market_days:
            warnings.append(
                f"market_data.csv: stock {stock_code} has {len(dates)} dates; max coverage is {expected_market_days}"
            )

    for row in event_rows:
        max_date = max_market_date_by_stock.get(row["stock_code"])
        if not max_date:
            errors.append(f"future-info check: no market data for event {row['event_id']}")
        elif row["event_time"] >= max_date:
            warnings.append(f"future-info check: event {row['event_id']} has limited later market window")

    return errors, warnings, tables


def write_report(errors: list[str], warnings: list[str], tables: dict[str, list[dict[str, str]]]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    stock_counts = Counter(row["industry_sector"] for row in tables.get("stock_pool.csv", []))
    source_counts = Counter(row["source_type"] for row in tables.get("raw_documents.csv", []))
    event_counts = Counter(row["event_type"] for row in tables.get("events.csv", []))
    lines = [
        "# AlphaLens B 线数据质量报告",
        "",
        f"生成日期：{today()}",
        "",
        "## 结论",
        "",
        f"- Fatal errors: {len(errors)}",
        f"- Warnings: {len(warnings)}",
        "- 本报告仅供研究参考，不构成投资建议",
        "",
        "## 数据规模",
        "",
        f"- stock_pool.csv: {len(tables.get('stock_pool.csv', []))} 行",
        f"- raw_documents.csv: {len(tables.get('raw_documents.csv', []))} 行",
        f"- entity_links.csv: {len(tables.get('entity_links.csv', []))} 行",
        f"- events.csv: {len(tables.get('events.csv', []))} 行",
        f"- predicates.csv: {len(tables.get('predicates.csv', []))} 行",
        f"- market_data.csv: {len(tables.get('market_data.csv', []))} 行",
        "",
        "## 股票池分布",
        "",
    ]
    for sector, count in sorted(stock_counts.items()):
        lines.append(f"- {sector}: {count}")
    lines.extend(["", "## 文本来源分布", ""])
    for source_type, count in sorted(source_counts.items()):
        lines.append(f"- {source_type}: {count}")
    lines.extend(["", "## 事件类型分布", ""])
    for event_type, count in sorted(event_counts.items()):
        lines.append(f"- {event_type}: {count}")

    lines.extend(
        [
            "",
            "## 文件完整性",
            "",
            "| 文件 | 行数 | 空字符串单元格 | 说明 |",
            "|------|------|----------------|------|",
        ]
    )
    for filename in COMPLETENESS_FILES:
        header, rows = read_csv(filename)
        empty_cells = sum(1 for row in rows for value in row.values() if value == "")
        if not header:
            note = "缺失或无表头"
        elif empty_cells:
            note = "存在允许的空值或待补字段"
        else:
            note = "通过"
        lines.append(f"| `{filename}` | {len(rows)} | {empty_cells} | {note} |")
    lines.extend(
        [
            "",
            "说明：人工抽检表中的留空字段用于人工填写，不属于 B↔C 数据契约。",
        ]
    )
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {item}" for item in warnings] or ["- 无"])
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {item}" for item in errors] or ["- 无"])
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "1. 完成人工谓词抽检，并由 A 确认事件与谓词金融口径。",
            "2. 与 C 联调当前真实文本和候选行情，确认收益对齐与未来函数审计。",
            "3. 当前 `adj_factor=1` 是已接受的字段占位，不是真实复权因子序列；答辩和报告必须披露限制。",
            "4. 对近尾部事件，补充后续行情后再扩大 forward return 样本。",
            "5. 保持 CSV 字段名不变，只使用 `python run_pipeline.py --preserve-inputs` 安全复跑。",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    errors, warnings, tables = validate()
    write_report(errors, warnings, tables)
    print(f"Data quality report written to {REPORT_PATH}")
    print(f"errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
