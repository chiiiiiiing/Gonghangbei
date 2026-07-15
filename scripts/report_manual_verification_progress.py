"""Report manual source verification progress for AlphaLens raw documents."""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "真实文本核验进度.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

SOURCE_TYPES = ["policy", "announcement", "news", "ir_qa"]
FIRST_BATCH_TARGETS = {
    "policy": 20,
    "announcement": 20,
    "news": 20,
    "ir_qa": 10,
}
BASELINE_DEMO_DOC_COUNT = 20


def today() -> str:
    return date.today().isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def doc_number(doc_id: str) -> int | None:
    match = re.fullmatch(r"S(\d{3})", doc_id)
    if not match:
        return None
    return int(match.group(1))


def is_candidate_summary(row: dict[str, str]) -> bool:
    return "待人工核验" in row.get("content", "")


def is_p0_verification_slot(row: dict[str, str]) -> bool:
    number = doc_number(row.get("doc_id", ""))
    return is_candidate_summary(row) or (
        number is not None and BASELINE_DEMO_DOC_COUNT < number <= 120
    )


def is_generic_homepage(url: str) -> bool:
    if not url:
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return True
    path = parsed.path.strip("/")
    return path == ""


def build_summary_rows(docs: list[dict[str, str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for source_type in SOURCE_TYPES:
        source_docs = [row for row in docs if row.get("source_type") == source_type]
        p0_docs = [row for row in source_docs if is_p0_verification_slot(row)]
        pending_p0_docs = [row for row in p0_docs if is_candidate_summary(row)]
        completed_p0_docs = [row for row in p0_docs if not is_candidate_summary(row)]
        target = FIRST_BATCH_TARGETS[source_type]
        completed_for_target = min(len(completed_p0_docs), target)
        remaining_for_target = max(target - completed_for_target, 0)
        generic_homepage_count = sum(
            1 for row in completed_p0_docs if is_generic_homepage(row.get("url", ""))
        )
        rows.append(
            {
                "source_type": source_type,
                "total_documents": len(source_docs),
                "p0_scope_documents": len(p0_docs),
                "p0_completed_estimate": len(completed_p0_docs),
                "p0_pending_candidates": len(pending_p0_docs),
                "first_batch_target": target,
                "first_batch_remaining": remaining_for_target,
                "completed_with_generic_url": generic_homepage_count,
            }
        )
    return rows


def write_report(docs: list[dict[str, str]], rows: list[dict[str, object]]) -> None:
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    source_counts = Counter(row.get("source_type", "") for row in docs)
    total_p0_completed = sum(int(row["p0_completed_estimate"]) for row in rows)
    total_p0_pending = sum(int(row["p0_pending_candidates"]) for row in rows)
    total_first_batch_remaining = sum(int(row["first_batch_remaining"]) for row in rows)
    generic_url_total = sum(int(row["completed_with_generic_url"]) for row in rows)

    lines = [
        "# AlphaLens 真实文本核验进度",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 统计口径",
        "",
        "- P0 范围按当前 100 条候选摘要槽位估算：`S021` 至 `S120`，或正文仍含“待人工核验”的行。",
        "- 已完成数量按 P0 范围内正文不再含“待人工核验”估算；最终是否通过仍以人工来源核验为准。",
        "- 第一批目标：政策 20 条、公告 20 条、新闻 20 条、互动问答 10 条。",
        "",
        "## 总览",
        "",
        f"- 文本总数：{len(docs)}",
        f"- P0 已替换候选摘要：{total_p0_completed}",
        f"- P0 仍待替换候选摘要：{total_p0_pending}",
        f"- 第一批目标剩余：{total_first_batch_remaining}",
        f"- 已替换 P0 中 URL 仍像首页/空链接的数量：{generic_url_total}",
        "",
        "## 来源类型分布",
        "",
    ]
    for source_type in SOURCE_TYPES:
        lines.append(f"- {source_type}: {source_counts[source_type]}")

    lines.extend(
        [
            "",
            "## P0 核验进度",
            "",
            "| 来源类型 | 文本总数 | P0 范围 | P0 已替换 | P0 待替换 | 第一批目标 | 第一批剩余 | 已替换但 URL 像首页 |",
            "|----------|----------|---------|-----------|-----------|------------|------------|----------------------|",
        ]
    )
    for row in rows:
        lines.append(
            "| {source_type} | {total_documents} | {p0_scope_documents} | "
            "{p0_completed_estimate} | {p0_pending_candidates} | {first_batch_target} | "
            "{first_batch_remaining} | {completed_with_generic_url} |".format(**row)
        )

    lines.extend(
        [
            "",
            "## 安全复跑命令",
            "",
            "人工替换一批真实文本后，使用保留输入的安全模式重刷下游结果：",
            "",
            "```bash",
            ".venv/bin/python run_pipeline.py --preserve-inputs",
            "```",
            "",
            "如只想重算 B 线实体链接、事件和谓词，可运行：",
            "",
            "```bash",
            ".venv/bin/python run_b_pipeline.py --skip-sample-generation",
            "```",
            "",
            "不要在真实文本写入后使用 `--force-sample-generation`。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    docs = read_csv(SAMPLE_DIR / "raw_documents.csv")
    rows = build_summary_rows(docs)
    write_report(docs, rows)
    print(f"Manual verification progress written to {REPORT_PATH}")
    print(
        "manual_verification_progress "
        f"p0_completed={sum(int(row['p0_completed_estimate']) for row in rows)} "
        f"p0_pending={sum(int(row['p0_pending_candidates']) for row in rows)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
