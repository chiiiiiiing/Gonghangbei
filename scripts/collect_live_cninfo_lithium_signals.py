"""Collect recent CNINFO lithium-industry announcements and append eligible V5/V6 live signals.

This is the non-GFEX side of the prospective V6 pipeline.  The existing V6 daily
updater records a decision every trading day, but its text alpha can only become
non-zero when post-freeze, URL-backed DeepSeek signals exist in
``data/research/lithium_v5_live_signals.csv``.  This script fetches recent
CNINFO PDFs, reuses the same V4 predicate/direction protocol, and writes only
eligible append-only live signals.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_lithium_v3_research import (  # noqa: E402
    PREDICATE_FILE,
    RULEBOOK_FILE,
    TEXT_FILE,
    analyze_live_v4_document,
    build_records,
    purged_discovery_records,
    read_path,
)
from scripts.fetch_cninfo_lithium_texts import (  # noqa: E402
    DEMAND_ISSUERS,
    HEADERS,
    ISSUERS,
    PDF_PREFIX,
    announcement_date,
    build_stock_map,
    clean_title,
    content_selected,
    extract_pdf,
    query_all,
    title_selected,
)
from src.ai.gateway import AISettings, OpenAICompatibleGateway  # noqa: E402
from src.lithium.engine import _read_csv, build_main_continuous  # noqa: E402
from src.lithium.live_signal_ledger import record_if_eligible  # noqa: E402


RESEARCH_DIR = ROOT / "data" / "research"
LIVE_SIGNAL_FILE = RESEARCH_DIR / "lithium_v5_live_signals.csv"
RUN_LOG_FILE = RESEARCH_DIR / "lithium_v6_live_collector_runs.csv"
RAW_DIR = ROOT / "data" / "raw" / "lithium_cninfo_live"
RUN_FIELDS = [
    "run_at", "start_date", "end_date", "candidates", "api_called",
    "recorded", "already_recorded", "not_eligible", "failed", "notes",
]


def load_v3_rulebook() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in read_path(RULEBOOK_FILE):
        rows.append({
            "rule_id": row["rule_id"],
            "target_label": row["target_label"],
            "conditions": [
                part.strip() for part in row["conditions"].split(" AND ")
                if part.strip()
            ],
            "score": float(row["score"]),
            "coverage_positive": float(row["coverage_positive"]),
            "coverage_negative": float(row["coverage_negative"]),
            "support_documents": int(row["support_documents"]),
            "support_dates": int(row["support_dates"]),
            "status": row["status"],
        })
    return rows


def discovery_records() -> list[dict[str, Any]]:
    predicates = {row["doc_id"]: row for row in read_path(PREDICATE_FILE)}
    contracts = _read_csv("lithium_contract_daily.csv")
    records = build_records(
        read_path(TEXT_FILE),
        predicates,
        build_main_continuous(contracts),
        contracts,
    )
    return purged_discovery_records(records)


def append_run_log(row: dict[str, Any]) -> None:
    write_header = not RUN_LOG_FILE.exists() or RUN_LOG_FILE.stat().st_size == 0
    if RUN_LOG_FILE.exists():
        with RUN_LOG_FILE.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != RUN_FIELDS:
                raise ValueError("V6 live collector run log 字段发生变化，拒绝写入")
    with RUN_LOG_FILE.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in RUN_FIELDS})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-per-company", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def collect_candidates(
    session: requests.Session,
    stock_map: dict[str, dict[str, str]],
    start: str,
    end: str,
    delay: float,
    max_per_company: int,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    failures: list[str] = []
    for stock_code, expected_name in ISSUERS.items():
        info = stock_map.get(stock_code)
        if not info or not info.get("org_id"):
            failures.append(f"{stock_code}:missing_org_id")
            continue
        announcements = query_all(
            session, stock_code, info["org_id"], start, end, delay
        )
        accepted = 0
        for item in announcements:
            title = clean_title(str(item.get("announcementTitle", "")))
            if not title_selected(stock_code, title):
                continue
            adjunct = str(item.get("adjunctUrl", "")).strip()
            if not adjunct:
                continue
            announcement_id = str(item.get("announcementId") or Path(adjunct).stem)
            cache_path = RAW_DIR / f"{announcement_id}.pdf"
            try:
                if cache_path.exists():
                    pdf_bytes = cache_path.read_bytes()
                else:
                    response = session.get(
                        PDF_PREFIX + adjunct, headers=HEADERS, timeout=45
                    )
                    response.raise_for_status()
                    pdf_bytes = response.content
                    RAW_DIR.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(pdf_bytes)
                    time.sleep(delay)
                content, _page_count = extract_pdf(pdf_bytes)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{stock_code}:{adjunct}:{exc}")
                continue
            if len(content) < 80 or not content_selected(stock_code, title, content):
                continue
            publish_time = announcement_date(item)
            candidates.append({
                "doc_id": f"CNINFO-LIVE-{stock_code}-{announcement_id}",
                "source_type": "announcement",
                "title": title,
                "content": content[:12000],
                "publish_time": publish_time,
                "source_name": "巨潮资讯网",
                "url": PDF_PREFIX + adjunct,
                "review_status": "accepted",
                "stock_code": stock_code,
                "issuer_name": info.get("name") or expected_name,
            })
            accepted += 1
            if accepted >= max_per_company:
                break
    if failures:
        print("fetch_failures=" + json.dumps(failures, ensure_ascii=False))
    return candidates


def main() -> int:
    args = parse_args()
    try:
        end_day = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit("end 必须使用 YYYY-MM-DD") from exc
    start_day = end_day - timedelta(days=args.days - 1)
    session = requests.Session()
    stock_map = build_stock_map(session)
    candidates = collect_candidates(
        session,
        stock_map,
        start_day.isoformat(),
        end_day.isoformat(),
        args.delay,
        args.max_per_company,
    )
    eligible = [
        row for row in candidates
        if row["publish_time"][:10] >= "2026-08-15" and row.get("url")
    ]
    print(json.dumps({
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "candidates": len(candidates),
        "eligible_post_freeze": len(eligible),
    }, ensure_ascii=False))
    if args.dry_run:
        for row in eligible:
            print(json.dumps({
                "doc_id": row["doc_id"],
                "publish_time": row["publish_time"],
                "title": row["title"],
                "url": row["url"],
            }, ensure_ascii=False))
        return 0
    if not eligible:
        append_run_log({
            "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "candidates": len(candidates),
            "api_called": 0,
            "recorded": 0,
            "already_recorded": 0,
            "not_eligible": 0,
            "failed": 0,
            "notes": "no_eligible_post_freeze_documents",
        })
        print(json.dumps({
            "status": "no_eligible_post_freeze_documents",
            "candidates": len(candidates),
            "eligible_post_freeze": 0,
        }, ensure_ascii=False))
        return 0

    settings = AISettings.from_environment()
    if not settings.enabled or settings.provider != "deepseek":
        raise SystemExit("需要 DEEPSEEK_API_KEY 才能生成实时碳酸锂方向信号")
    if settings.chat_model != "deepseek-v4-flash":
        raise SystemExit("实时前瞻信号锁定 deepseek-v4-flash")
    gateway = OpenAICompatibleGateway(settings)
    rulebook = load_v3_rulebook()
    contexts = discovery_records()
    counts = {
        "candidates": len(candidates),
        "eligible_post_freeze": len(eligible),
        "api_called": 0,
        "recorded": 0,
        "already_recorded": 0,
        "not_eligible": 0,
        "failed": 0,
    }
    notes: list[str] = []
    for document in eligible:
        try:
            result = analyze_live_v4_document(
                document, gateway, rulebook, contexts
            )
            counts["api_called"] += 1
            recording = record_if_eligible(
                LIVE_SIGNAL_FILE, document, result, True
            )
            counts[recording["status"]] = counts.get(recording["status"], 0) + 1
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            notes.append(f"{document['doc_id']}:{type(exc).__name__}:{exc}")
    append_run_log({
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "candidates": counts["candidates"],
        "api_called": counts["api_called"],
        "recorded": counts["recorded"],
        "already_recorded": counts["already_recorded"],
        "not_eligible": counts["not_eligible"],
        "failed": counts["failed"],
        "notes": " | ".join(notes)[:2000],
    })
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
