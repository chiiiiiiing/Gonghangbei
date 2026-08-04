"""从 AkShare 获取股票池前复权行情，默认只写入 Git 忽略的暂存区。"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from src.ingestion.common import atomic_write_csv, sha256


ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "data" / "sample"
STAGED = ROOT / "data" / "external" / "行情导入暂存" / "market_data.csv"
DESTINATION = SAMPLE_DIR / "market_data.csv"
FIELDS = ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"]


def main() -> None:
    parser = argparse.ArgumentParser(description="更新 AlphaLens 前复权行情")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--apply", action="store_true", help="校验通过后替换样例行情")
    args = parser.parse_args()
    for value in (args.start, args.end):
        datetime.strptime(value, "%Y-%m-%d")
    try:
        import akshare as ak
    except ImportError as exc:
        raise SystemExit("请先运行 pip install -r requirements-data.txt") from exc
    with (SAMPLE_DIR / "stock_pool.csv").open(encoding="utf-8", newline="") as handle:
        stocks = list(csv.DictReader(handle))
    rows: list[dict[str, str]] = []
    for stock in stocks:
        code = stock["stock_code"]
        frame = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=args.start.replace("-", ""),
            end_date=args.end.replace("-", ""),
            adjust="qfq",
        )
        for _, item in frame.iterrows():
            rows.append(
                {
                    "trade_date": str(item["日期"]),
                    "stock_code": code,
                    "open": f"{float(item['开盘']):.4f}",
                    "high": f"{float(item['最高']):.4f}",
                    "low": f"{float(item['最低']):.4f}",
                    "close": f"{float(item['收盘']):.4f}",
                    "volume": str(int(float(item["成交量"]))),
                    "adj_factor": "1",
                }
            )
        print(f"{code}: {len(frame)} 行")
    rows.sort(key=lambda row: (row["trade_date"], row["stock_code"]))
    if {row["stock_code"] for row in rows} != {row["stock_code"] for row in stocks}:
        raise RuntimeError("部分股票未取得行情，未替换样例文件")
    atomic_write_csv(STAGED, FIELDS, rows)
    print(f"已暂存 {len(rows)} 行：{STAGED}")
    print("adj_factor=1 仍为占位字段；价格列已请求前复权口径。")
    if args.apply:
        before = sha256(DESTINATION)
        atomic_write_csv(DESTINATION, FIELDS, rows)
        print(f"已替换行情：{before} -> {sha256(DESTINATION)}")


if __name__ == "__main__":
    main()
