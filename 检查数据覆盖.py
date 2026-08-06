"""检查 AlphaLens 样本数据在研究分区与事件类型上的覆盖缺口。

把"补数据"这个 P0 任务变成可量化、可验收的清单：
- 按 分区 × 来源类型 统计当前 / 目标(25) / 待补数量。
- 按事件类型统计样本覆盖，指出零样本类型。
- 输出待补清单；全部达标时退出码为 0，否则为 1（可用于流水线检查）。

用法：
    .venv/bin/python 检查数据覆盖.py
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "data" / "sample"
TARGET_PER_TYPE = 25
DISCOVERY_END = "2025-12-31"
SOURCE_TYPES = ["policy", "announcement", "news", "ir_qa"]
EVENT_TYPES = [
    "policy_support",
    "regulatory_penalty",
    "inquiry_letter_pressure",
    "earnings_quality_anomaly",
    "product_price_increase",
    "supply_chain_disruption",
    "capacity_expansion",
    "investor_question_pressure",
    "attention_spread",
]
SOURCE_LABELS = {"policy": "政策", "announcement": "公告", "news": "新闻", "ir_qa": "互动问答"}
SPLIT_LABELS = {"discovery": "Discovery(2024—2025)", "oos": "OOS(2026H1)"}


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    documents = read_csv("raw_documents.csv")
    split_source: dict[str, Counter[str]] = {"discovery": Counter(), "oos": Counter()}
    for row in documents:
        split = "discovery" if row["publish_time"] <= DISCOVERY_END else "oos"
        split_source[split][row["source_type"]] += 1

    print("# AlphaLens 数据覆盖检查")
    print(f"\n文档总数：{len(documents)}")
    print(f"覆盖目标：每个分区 × 每种来源至少 {TARGET_PER_TYPE} 篇独立文档\n")

    gaps = 0
    for split in ("discovery", "oos"):
        print(f"## {SPLIT_LABELS[split]}")
        for source_type in SOURCE_TYPES:
            count = split_source[split].get(source_type, 0)
            remaining = max(TARGET_PER_TYPE - count, 0)
            status = "达标" if count >= TARGET_PER_TYPE else "待补"
            if remaining:
                gaps += 1
            print(f"- {SOURCE_LABELS[source_type]}: {count}/{TARGET_PER_TYPE} (待补 {remaining}) [{status}]")
        print()

    events = read_csv("events.csv")
    event_counts = Counter(row["event_type"] for row in events)
    print("## 事件类型覆盖")
    for event_type in EVENT_TYPES:
        count = event_counts.get(event_type, 0)
        status = "有样本" if count else "零样本(未覆盖)"
        if not count:
            gaps += 1
        print(f"- {event_type}: {count} [{status}]")

    print("\n## 待补清单（供采集 / 导入真实文本.py 使用）")
    for split in ("discovery", "oos"):
        for source_type in SOURCE_TYPES:
            count = split_source[split].get(source_type, 0)
            remaining = max(TARGET_PER_TYPE - count, 0)
            if remaining:
                print(f"- 补 {split}/{source_type}: {remaining} 篇")
    for event_type in EVENT_TYPES:
        if not event_counts.get(event_type, 0):
            print(f"- 补事件类型 {event_type}: 建议 ≥5 篇")

    if gaps:
        print(f"\n结论：存在 {gaps} 项覆盖缺口，尚未达标（返回码 1）。")
        return 1
    print("\n结论：分区与事件类型覆盖全部达标（返回码 0）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
