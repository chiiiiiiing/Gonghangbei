"""Build a controlled lithium text corpus from verified sources and GFEX facts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.source_quality import cached_fetch_full_text
TEXT_FIELDS = [
    "doc_id", "source_type", "title", "content", "publish_time",
    "source_name", "url", "review_status",
]
AUDIT_FIELDS = [
    "doc_id", "publish_time", "provenance", "fetch_status", "original_chars",
    "selected_chars", "content_sha256", "source_name", "url", "fetched_at",
]
RELEVANT_TERMS = (
    "碳酸锂", "锂盐", "锂矿", "新能源汽车", "动力电池", "储能",
    "电池装车", "电池产量", "电池回收", "电池材料",
)
DISCOVERY_END = date(2024, 12, 31)
WAREHOUSE_EVENT_QUANTILE = 0.75


def read_csv(name: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def normalized_source_type(value: str) -> str:
    return value if value in {"policy", "announcement", "news", "ir_qa"} else "news"


def stable_reuse_status(value: str) -> str:
    prefix = "reused_audited_corpus:"
    while value.startswith(prefix):
        value = value[len(prefix):]
    return f"{prefix}{value or 'unknown'}"


def source_candidates(start: date, end: date, refetch: bool) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    existing_text_by_url = {
        row.get("url", "").rstrip("/").lower(): row.get("content", "").strip()
        for row in read_csv("lithium_texts.csv")
        if row.get("url") and row.get("source_name") != "广州期货交易所"
    }
    existing_audit_by_url = {
        row.get("url", "").rstrip("/").lower(): row
        for row in read_csv("lithium_text_fetch_audit.csv")
        if row.get("url") and row.get("source_name") != "广州期货交易所"
    }
    candidates: list[tuple[str, dict[str, str]]] = []
    for filename in (
        "raw_documents.csv",
        "macro_historical_documents.csv",
        "lithium_text_sources.csv",
    ):
        for row in read_csv(filename):
            try:
                publish_day = datetime.strptime(row.get("publish_time", "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            combined = f"{row.get('title', '')} {row.get('content', '')}"
            if not start <= publish_day <= end or not any(term in combined for term in RELEVANT_TERMS):
                continue
            if not row.get("url", "").startswith(("http://", "https://")):
                continue
            candidates.append((filename, row))

    rows: list[dict[str, str]] = []
    audit: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for filename, row in sorted(candidates, key=lambda item: (item[1]["publish_time"], item[1]["doc_id"])):
        url = row["url"].strip()
        normalized_url = url.rstrip("/").lower()
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        original = row.get("content", "").strip()
        fetch_status = "not_requested"
        selected = original
        if len(existing_text_by_url.get(normalized_url, "")) >= 40:
            selected = existing_text_by_url[normalized_url]
            previous_status = existing_audit_by_url.get(normalized_url, {}).get("fetch_status", "")
            fetch_status = stable_reuse_status(previous_status)
        elif refetch:
            fetched = cached_fetch_full_text(url)
            fetch_status = str(fetched.get("status", "failed"))
            fetched_text = str(fetched.get("text", "")).strip()
            if len(fetched_text) >= 120:
                selected = fetched_text
        selected = selected[:12000].strip()
        if len(selected) < 40:
            continue
        queued_doc_id = row.get("doc_id", "").strip()
        doc_id = (
            queued_doc_id
            if filename == "lithium_text_sources.csv" and queued_doc_id
            else f"LC-TEXT-{len(rows) + 1:04d}"
        )
        rows.append({
            "doc_id": doc_id,
            "source_type": normalized_source_type(row.get("source_type", "news")),
            "title": row.get("title", "").strip(),
            "content": selected,
            "publish_time": row["publish_time"][:10],
            "source_name": row.get("source_name", "").strip(),
            "url": url,
            "review_status": "accepted",
        })
        audit.append({
            "doc_id": doc_id, "publish_time": row["publish_time"][:10],
            "provenance": f"verified_repository_input:{filename}",
            "fetch_status": fetch_status, "original_chars": len(original),
            "selected_chars": len(selected),
            "content_sha256": hashlib.sha256(selected.encode("utf-8")).hexdigest(),
            "source_name": row.get("source_name", "").strip(), "url": url,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    return rows, audit


def warehouse_documents(start: date, end: date) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, str]] = []
    audit: list[dict[str, Any]] = []
    parsed: list[tuple[dict[str, str], date, float, float]] = []
    for item in read_csv("lithium_warehouse_receipts.csv"):
        try:
            day = datetime.strptime(item["trade_date"], "%Y-%m-%d").date()
            quantity = float(item["warehouse_receipt"])
            change = float(item["change"])
        except (KeyError, ValueError):
            continue
        parsed.append((item, day, quantity, change))
    discovery_changes = sorted(
        abs(change) for _, day, _, change in parsed
        if start <= day <= min(end, DISCOVERY_END) and change != 0
    )
    if not discovery_changes:
        return rows, audit
    threshold_index = max(0, math.ceil(WAREHOUSE_EVENT_QUANTILE * len(discovery_changes)) - 1)
    materiality_threshold = discovery_changes[threshold_index]
    for item, day, quantity, change in parsed:
        if not start <= day <= end:
            continue
        if abs(change) < materiality_threshold:
            continue
        if change > 0:
            direction = f"增加{change:g}手"
        elif change < 0:
            direction = f"减少{abs(change):g}手"
        else:
            direction = "较上一交易日持平"
        title = f"广期所碳酸锂仓单日报：仓单{direction}"
        content = (
            f"广州期货交易所{day.isoformat()}仓单日报显示，"
            f"碳酸锂仓单总量为{quantity:g}手，{direction}。"
        )
        doc_id = f"GFEX-WR-{day:%Y%m%d}"
        url = "https://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml"
        rows.append({
            "doc_id": doc_id, "source_type": "announcement", "title": title, "content": content,
            "publish_time": day.isoformat(), "source_name": "广州期货交易所",
            "url": url, "review_status": "accepted",
        })
        audit.append({
            "doc_id": doc_id, "publish_time": day.isoformat(),
            "provenance": (
                "gfex_warehouse_receipt_structured_fact:"
                f"discovery_abs_change_q75_ge_{materiality_threshold:g}"
            ),
            "fetch_status": "derived_from_audited_official_json", "original_chars": len(content),
            "selected_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "source_name": "广州期货交易所", "url": url,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    return rows, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-07-21")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--skip-refetch", action="store_true")
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    sourced, source_audit = source_candidates(start, end, not args.skip_refetch)
    warehouse, warehouse_audit = warehouse_documents(start, end)
    combined = sorted(sourced + warehouse, key=lambda row: (row["publish_time"], row["doc_id"]))
    audit = sorted(source_audit + warehouse_audit, key=lambda row: (row["publish_time"], row["doc_id"]))
    write_csv(SAMPLE_DIR / "lithium_texts.csv", TEXT_FIELDS, combined)
    write_csv(SAMPLE_DIR / "lithium_text_fetch_audit.csv", AUDIT_FIELDS, audit)
    print(f"Lithium corpus: {len(sourced)} sourced texts + {len(warehouse)} GFEX warehouse facts = {len(combined)}")


if __name__ == "__main__":
    main()
