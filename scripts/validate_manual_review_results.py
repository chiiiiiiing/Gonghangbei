"""Validate manual review result values for AlphaLens sampling sheets."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "人工抽检结果校验报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

VALID_RESULTS = {"", "pass", "revise", "drop"}
REVIEW_FILES = [
    ("事件人工抽检样本.csv", ["review_item"]),
    ("谓词人工抽检样本.csv", ["event_id", "predicate_name"]),
]


def today() -> str:
    return date.today().isoformat()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def row_key(row: dict[str, str], key_fields: list[str]) -> str:
    return "/".join(row.get(field, "") for field in key_fields)


def validate_file(filename: str, key_fields: list[str]) -> tuple[list[str], list[str], Counter[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    counts: Counter[str] = Counter()
    header, rows = read_csv(VIEW_DIR / filename)
    if not header:
        errors.append(f"{filename}: missing or empty")
        return errors, warnings, counts
    if "manual_review_result" not in header or "manual_comment" not in header:
        errors.append(f"{filename}: missing manual_review_result or manual_comment column")
        return errors, warnings, counts

    for row in rows:
        result = row.get("manual_review_result", "").strip()
        comment = row.get("manual_comment", "").strip()
        counts[result or "pending"] += 1
        if result not in VALID_RESULTS:
            errors.append(f"{filename}: invalid manual_review_result={result!r} at {row_key(row, key_fields)}")
        if result in {"revise", "drop"} and not comment:
            warnings.append(f"{filename}: {result} at {row_key(row, key_fields)} should include manual_comment")
    return errors, warnings, counts


def write_report(errors: list[str], warnings: list[str], file_counts: dict[str, Counter[str]]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AlphaLens 人工抽检结果校验报告",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 结论",
        "",
        f"- Fatal errors: {len(errors)}",
        f"- Warnings: {len(warnings)}",
        "- 合法取值：空字符串、`pass`、`revise`、`drop`",
        "",
        "## 结果分布",
        "",
        "| 文件 | pending | pass | revise | drop |",
        "|------|---------|------|--------|------|",
    ]
    for filename, counts in file_counts.items():
        lines.append(
            f"| `{filename}` | {counts['pending']} | {counts['pass']} | "
            f"{counts['revise']} | {counts['drop']} |"
        )
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {item}" for item in warnings] or ["- 无"])
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {item}" for item in errors] or ["- 无"])
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    all_errors: list[str] = []
    all_warnings: list[str] = []
    file_counts: dict[str, Counter[str]] = {}
    for filename, key_fields in REVIEW_FILES:
        errors, warnings, counts = validate_file(filename, key_fields)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        file_counts[filename] = counts
    write_report(all_errors, all_warnings, file_counts)
    print(f"Manual review validation report written to {REPORT_PATH}")
    print(f"manual_review_errors={len(all_errors)} warnings={len(all_warnings)}")
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
