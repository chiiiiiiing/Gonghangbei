"""从深交所互动易暂存可人工核验的 Discovery 互动问答候选。"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src.ingestion.discovery_ir_qa import OfficialIRMClient, collect_candidates, write_candidate_bundle


ROOT = Path(__file__).resolve().parent
STOCK_POOL = ROOT / "data" / "sample" / "stock_pool.csv"
OUTPUT_DIR = ROOT / "data" / "external" / "文本导入暂存"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只暂存 2024-2025 官方互动问答候选；不会修改 raw_documents.csv"
    )
    parser.add_argument("--pages-per-stock", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=25)
    parser.add_argument("--max-stocks", type=int, default=0, help="用于小范围联网验收；0 表示全股票池")
    args = parser.parse_args()
    with STOCK_POOL.open(encoding="utf-8", newline="") as handle:
        stocks = list(csv.DictReader(handle))
    if args.max_stocks:
        stocks = stocks[: args.max_stocks]
    candidates, report = collect_candidates(
        stocks,
        OfficialIRMClient(),
        pages_per_stock=args.pages_per_stock,
        max_candidates=args.max_candidates,
    )
    candidates_path, report_path = write_candidate_bundle(candidates, report, OUTPUT_DIR)
    print(f"候选暂存：{candidates_path}")
    print(f"核验报告：{report_path}")
    print(f"候选数：{report['candidate_count']}；状态：{report['status']}")
    print("raw_documents.csv 未修改；仅人工确认后才可复制到模板并调用导入命令。")


if __name__ == "__main__":
    main()
