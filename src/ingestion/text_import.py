"""Validate a curated source manifest and stage locked-schema document rows."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from src.ingestion.common import atomic_write_csv, sha256


FIELDS = ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"]
SOURCE_TYPES = {"policy", "announcement", "news", "ir_qa"}


def validate_manifest(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"采集清单字段必须严格等于：{','.join(FIELDS)}")
        rows = list(reader)
    errors: list[str] = []
    seen_doc_ids: set[str] = set()
    seen_urls: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        prefix = f"第 {line_number} 行"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", row["doc_id"]):
            errors.append(f"{prefix} doc_id 不合法")
        if row["doc_id"] in seen_doc_ids:
            errors.append(f"{prefix} doc_id 重复")
        seen_doc_ids.add(row["doc_id"])
        if row["source_type"] not in SOURCE_TYPES:
            errors.append(f"{prefix} source_type 不合法")
        try:
            datetime.strptime(row["publish_time"], "%Y-%m-%d")
        except ValueError:
            errors.append(f"{prefix} publish_time 必须为 YYYY-MM-DD")
        parsed_url = urlparse(row["url"])
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            errors.append(f"{prefix} URL 不是正文详情页地址")
        if row["url"] in seen_urls:
            errors.append(f"{prefix} URL 重复")
        seen_urls.add(row["url"])
        if len(row["title"].strip()) < 6 or len(row["content"].strip()) < 40:
            errors.append(f"{prefix} 标题或正文摘要过短")
    return rows, errors


def stage_manifest(manifest: Path, staging_dir: Path) -> Path:
    rows, errors = validate_manifest(manifest)
    staging_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "manifest": str(manifest),
        "manifest_sha256": sha256(manifest),
        "row_count": len(rows),
        "source_type_counts": dict(sorted(Counter(row["source_type"] for row in rows).items())),
        "errors": errors,
        "status": "pass" if not errors else "fail",
    }
    (staging_dir / "文本导入校验报告.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if errors:
        raise ValueError("采集清单校验失败，详见暂存区报告")
    output = staging_dir / "待导入真实文本.csv"
    atomic_write_csv(output, FIELDS, rows)
    return output


def merge_documents(
    existing: list[dict[str, str]], incoming: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Pure merge: replace rows by doc_id, append new rows, reject duplicate URLs."""
    incoming_by_id = {row["doc_id"]: row for row in incoming}
    merged = [incoming_by_id.pop(row["doc_id"], row) for row in existing]
    merged.extend(incoming_by_id.values())
    if len({row["url"] for row in merged}) != len(merged):
        raise ValueError("合并后出现重复 URL，未修改 raw_documents.csv")
    return merged


def apply_staged(staged: Path, destination: Path) -> dict[str, int]:
    with destination.open(encoding="utf-8", newline="") as handle:
        existing = list(csv.DictReader(handle))
    with staged.open(encoding="utf-8", newline="") as handle:
        incoming = list(csv.DictReader(handle))
    merged = merge_documents(existing, incoming)
    atomic_write_csv(destination, FIELDS, merged)
    return {"existing": len(existing), "incoming": len(incoming), "merged": len(merged)}
