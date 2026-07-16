"""Validate AlphaLens market_data.csv or a real adjusted market-data import."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
DEFAULT_INPUT = SAMPLE_DIR / "market_data.csv"
DEFAULT_REPORT = VIEW_DIR / "真实行情校验报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

MARKET_SCHEMA = ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"]
DATE_LOW = "2024-01-01"
DATE_HIGH = "2026-06-30"


def today() -> str:
    return date.today().isoformat()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
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


def load_stock_codes() -> set[str]:
    _header, rows = read_csv(SAMPLE_DIR / "stock_pool.csv")
    return {row["stock_code"] for row in rows}


def validate_market_data(input_path: Path) -> tuple[list[str], list[str], dict[str, object]]:
    errors: list[str] = []
    warnings: list[str] = []
    header, rows = read_csv(input_path)
    if not header:
        return [f"{input_path}: missing or empty"], warnings, {"row_count": 0}
    if header != MARKET_SCHEMA:
        errors.append(f"{input_path}: header mismatch. expected {MARKET_SCHEMA}, got {header}")

    stock_codes = load_stock_codes()
    seen: set[tuple[str, str]] = set()
    dates_by_stock: dict[str, set[str]] = defaultdict(set)
    all_dates: list[str] = []
    adj_values: set[str] = set()

    for line_number, row in enumerate(rows, start=2):
        stock_code = row.get("stock_code", "")
        trade_date = row.get("trade_date", "")
        key = (trade_date, stock_code)
        if key in seen:
            errors.append(f"{input_path}: duplicate trade_date+stock_code at line {line_number}: {key}")
        seen.add(key)

        parsed_date = parse_date(trade_date)
        if parsed_date is None:
            errors.append(f"{input_path}: invalid trade_date at line {line_number}: {trade_date}")
        else:
            if not (DATE_LOW <= trade_date <= DATE_HIGH):
                errors.append(f"{input_path}: trade_date out of MVP range at line {line_number}: {trade_date}")
            if parsed_date.weekday() >= 5:
                errors.append(f"{input_path}: weekend date at line {line_number}: {trade_date}")
            all_dates.append(trade_date)

        if not re.fullmatch(r"\d{6}", stock_code):
            errors.append(f"{input_path}: invalid stock_code at line {line_number}: {stock_code}")
        elif stock_code not in stock_codes:
            errors.append(f"{input_path}: stock_code not in stock_pool at line {line_number}: {stock_code}")
        else:
            dates_by_stock[stock_code].add(trade_date)

        numeric_ok = True
        for field in ["open", "high", "low", "close", "adj_factor"]:
            if not is_float(row.get(field, "")) or float(row[field]) <= 0:
                errors.append(f"{input_path}: invalid {field} at line {line_number}")
                numeric_ok = False
        if not is_float(row.get("volume", "")) or float(row["volume"]) < 0:
            errors.append(f"{input_path}: invalid volume at line {line_number}")
            numeric_ok = False

        if numeric_ok:
            open_price = float(row["open"])
            high_price = float(row["high"])
            low_price = float(row["low"])
            close_price = float(row["close"])
            if high_price < max(open_price, low_price, close_price):
                errors.append(f"{input_path}: high below OHLC value at line {line_number}")
            if low_price > min(open_price, high_price, close_price):
                errors.append(f"{input_path}: low above OHLC value at line {line_number}")
            adj_values.add(row["adj_factor"])

    missing_stocks = sorted(stock_codes - set(dates_by_stock))
    if missing_stocks:
        errors.append(f"{input_path}: missing market data for stocks {missing_stocks}")

    if dates_by_stock:
        max_coverage = max(len(dates) for dates in dates_by_stock.values())
        min_coverage = min(len(dates) for dates in dates_by_stock.values())
        if min_coverage < max_coverage:
            warnings.append(f"{input_path}: uneven stock date coverage, min={min_coverage}, max={max_coverage}")
    else:
        max_coverage = 0
        min_coverage = 0

    if all_dates:
        min_date = min(all_dates)
        max_date = max(all_dates)
        if min_date > "2024-01-02":
            warnings.append(f"{input_path}: first market date is {min_date}; confirm coverage starts near 2024-01-01")
        if max_date < DATE_HIGH:
            warnings.append(f"{input_path}: last market date is {max_date}; confirm coverage reaches 2026-06-30")
    else:
        min_date = ""
        max_date = ""

    if len(adj_values) <= 1:
        warnings.append(
            f"{input_path}: adj_factor has {len(adj_values)} unique value; accepted as a placeholder, not a real adjustment-factor series"
        )

    summary = {
        "row_count": len(rows),
        "stock_count": len(dates_by_stock),
        "min_date": min_date,
        "max_date": max_date,
        "min_coverage": min_coverage,
        "max_coverage": max_coverage,
        "adj_factor_unique_count": len(adj_values),
        "source_counts": Counter(row.get("stock_code", "") for row in rows),
    }
    return errors, warnings, summary


def write_report(input_path: Path, report_path: Path, errors: list[str], warnings: list[str], summary: dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AlphaLens 真实行情校验报告",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 结论",
        "",
        f"- 校验文件：`{input_path.relative_to(ROOT) if input_path.is_relative_to(ROOT) else input_path}`",
        f"- Fatal errors: {len(errors)}",
        f"- Warnings: {len(warnings)}",
        f"- 行数：{summary.get('row_count', 0)}",
        f"- 覆盖股票数：{summary.get('stock_count', 0)}",
        f"- 日期范围：{summary.get('min_date', '')} 至 {summary.get('max_date', '')}",
        f"- 单股日期覆盖：{summary.get('min_coverage', 0)} 至 {summary.get('max_coverage', 0)}",
        f"- adj_factor 唯一值数量：{summary.get('adj_factor_unique_count', 0)}",
        "",
        "## 校验规则",
        "",
        "- 字段必须严格等于 `trade_date,stock_code,open,high,low,close,volume,adj_factor`。",
        "- 股票代码必须是 6 位字符串，且存在于 `stock_pool.csv`。",
        "- 日期必须为 `YYYY-MM-DD`，范围在 2024-01-01 至 2026-06-30。",
        "- OHLC 和 `adj_factor` 必须为正数，`volume` 必须大于等于 0。",
        "- `high` 不低于开收低，`low` 不高于开收高。",
        "- 当前价格使用东方财富 `fqt=1` 前复权候选口径；`adj_factor=1` 已接受为字段占位，但不是真实复权因子序列。",
        "",
        "## Warnings",
        "",
    ]
    lines.extend([f"- {item}" for item in warnings] or ["- 无"])
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {item}" for item in errors] or ["- 无"])
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate AlphaLens real market data CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Market data CSV path to validate.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Markdown report path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or [])
    input_path = Path(args.input)
    report_path = Path(args.report)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    errors, warnings, summary = validate_market_data(input_path)
    write_report(input_path, report_path, errors, warnings, summary)
    print(f"Real market data validation report written to {report_path}")
    print(f"market_data_errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
