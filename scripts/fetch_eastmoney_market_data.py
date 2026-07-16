"""Fetch front-adjusted daily A-share market data from Eastmoney."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path
from typing import Sequence

import requests


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
OUTPUT_PATH = SAMPLE_DIR / "market_data.csv"
REPORT_PATH = VIEW_DIR / "真实行情获取记录.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

EASTMONEY_KLINE_URLS = [
    "http://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://72.push2his.eastmoney.com/api/qt/stock/kline/get",
]
MARKET_FIELDS = ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"]


def today() -> str:
    return date.today().isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def secid_for_stock(stock_code: str) -> str:
    market_prefix = "1" if stock_code.startswith(("6", "9")) else "0"
    return f"{market_prefix}.{stock_code}"


def fetch_stock_klines(
    stock_code: str,
    *,
    begin: str,
    end: str,
    session: requests.Session,
    retries: int,
) -> list[dict[str, str]]:
    params = {
        "secid": secid_for_stock(stock_code),
        "klt": "101",
        "fqt": "1",
        "beg": begin,
        "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    last_error: Exception | None = None
    response = None
    for attempt in range(retries + 1):
        for url in EASTMONEY_KLINE_URLS:
            try:
                response = session.get(url, params=params, timeout=(5, 8))
                response.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if response is not None:
            break
        time.sleep(0.4 * (attempt + 1))
    if response is None:
        raise RuntimeError(last_error)
    payload = response.json()
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    rows: list[dict[str, str]] = []
    for item in klines:
        parts = item.split(",")
        if len(parts) < 6:
            continue
        trade_date, open_price, close_price, high_price, low_price, volume = parts[:6]
        rows.append(
            {
                "trade_date": trade_date,
                "stock_code": stock_code,
                "open": f"{float(open_price):.2f}",
                "high": f"{float(high_price):.2f}",
                "low": f"{float(low_price):.2f}",
                "close": f"{float(close_price):.2f}",
                "volume": str(int(float(volume)) * 100),
                "adj_factor": "1.000000",
            }
        )
    return rows


def write_report(rows_by_stock: dict[str, int], errors: list[str], *, begin: str, end: str) -> None:
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AlphaLens 真实行情获取记录",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 数据源",
        "",
        "- 来源：东方财富历史 K 线接口。",
        "- 参数：`klt=101` 日频，`fqt=1` 前复权，时间范围 `{}` 至 `{}`。".format(begin, end),
        "- 字段落表：`trade_date, stock_code, open, high, low, close, volume, adj_factor`。",
        "- 口径决定：项目接受 `adj_factor=1.000000` 作为字段占位；它不代表真实复权因子序列。当前 open/high/low/close 使用接口返回的前复权价格候选版，回测仅用于研究链路验证。",
        "- 答辩限制：必须主动说明接口未直接返回复权因子；如后续接入 Wind、Choice 等正式数据源，应整体替换价格与复权因子并重跑。",
        "",
        "## 覆盖情况",
        "",
        "| stock_code | 行数 |",
        "|------------|------|",
    ]
    for stock_code, count in sorted(rows_by_stock.items()):
        lines.append(f"| {stock_code} | {count} |")
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {item}" for item in errors] or ["- 无"])
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Eastmoney front-adjusted daily market data.")
    parser.add_argument("--begin", default="20240101", help="Begin date, YYYYMMDD.")
    parser.add_argument("--end", default="20260630", help="End date, YYYYMMDD.")
    parser.add_argument("--sleep", type=float, default=0.08, help="Sleep seconds between requests.")
    parser.add_argument("--retries", type=int, default=3, help="Retries for each stock request.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or [])
    stock_rows = read_csv(SAMPLE_DIR / "stock_pool.csv")
    all_rows: list[dict[str, str]] = []
    rows_by_stock: dict[str, int] = {}
    errors: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    for stock in stock_rows:
        stock_code = stock["stock_code"]
        try:
            print(f"[AlphaLens] Fetch market data {stock_code} ...", flush=True)
            rows = fetch_stock_klines(
                stock_code,
                begin=args.begin,
                end=args.end,
                session=session,
                retries=args.retries,
            )
        except Exception as exc:  # noqa: BLE001
            rows = []
            errors.append(f"{stock_code}: {exc}")
        rows_by_stock[stock_code] = len(rows)
        if not rows:
            errors.append(f"{stock_code}: no rows returned")
        all_rows.extend(rows)
        time.sleep(args.sleep)

    if errors:
        write_report(rows_by_stock, errors, begin=args.begin, end=args.end)
        print(f"market_fetch_errors={len(errors)}")
        for error in errors:
            print(error)
        return 1

    all_rows.sort(key=lambda row: (row["trade_date"], row["stock_code"]))
    write_csv(OUTPUT_PATH, MARKET_FIELDS, all_rows)
    write_report(rows_by_stock, errors, begin=args.begin, end=args.end)
    print(f"Eastmoney market data written to {OUTPUT_PATH}")
    print(f"market_fetch_errors=0 rows={len(all_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
