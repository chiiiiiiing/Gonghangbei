"""Verified official historical corpus for the monthly Nowcast layer.

The corpus combines NBS release pages referenced by ``macro_target_history.csv``
with a curated manifest of official energy and industrial policy pages. Each
document retains its first publication date and URL. Target matching always
requires the corresponding target release date to be strictly later than the
document date, so a release page can never reveal the value it is used to
predict.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from src.ai.source_quality import fetch_full_text
from src.pipeline.extract_events_rule_based import build_events
from src.pipeline.ground_predicates_rule_based import ground_event_predicates
from src.pipeline.link_entities import link_documents


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
DOCUMENT_FIELDS = ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"]


def _read_csv(name: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(name: str, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with (SAMPLE_DIR / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:18_000]


def _title_from_page(text: str, period_end: str) -> str:
    first = text.split(" - 国家统计局", 1)[0].strip()
    if 8 <= len(first) <= 120:
        return first
    return f"{period_end[:7]}规模以上工业增加值官方发布"


def ensure_historical_documents() -> list[dict[str, str]]:
    """Fetch once, validate provenance, and persist a separate historical corpus."""
    destination = SAMPLE_DIR / "macro_historical_documents.csv"
    existing = _read_csv(destination.name) if destination.exists() else []
    targets = [
        row for row in _read_csv("macro_target_history.csv")
        if row.get("release_date", "") < "2024-01-01"
        and row.get("source_url", "").startswith("https://www.stats.gov.cn/")
    ]

    policy_manifest = _read_csv("macro_historical_policy_manifest.csv")
    existing_urls = {row["url"] for row in existing}
    policy_pending = [row for row in policy_manifest if row["url"] not in existing_urls]
    if existing and not policy_pending:
        return existing

    def fetch(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any]]:
        return row, fetch_full_text(row["source_url"], timeout=25)

    def fetch_policy(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any]]:
        normalized = {**row, "source_url": row["url"], "release_date": row["publish_time"], "period_end": ""}
        return normalized, fetch_full_text(row["url"], timeout=25)

    results: list[tuple[dict[str, str], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = ([] if existing else [pool.submit(fetch, row) for row in targets])
        futures.extend(pool.submit(fetch_policy, row) for row in policy_pending)
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item[0]["release_date"])

    documents: list[dict[str, str]] = list(existing)
    audit: list[dict[str, Any]] = _read_csv("macro_historical_fetch_audit.csv")
    for index, (target, fetched) in enumerate(results, start=1):
        status = str(fetched.get("status", "failed"))
        text = _clean_text(str(fetched.get("text", "")))
        accepted = status == "ok" and len(text) >= 500
        doc_id = target.get("doc_id") or f"HIST-NBS-{index:03d}"
        audit.append({
            "doc_id": doc_id, "release_date": target["release_date"],
            "period_end_described": target["period_end"], "fetch_status": status,
            "fetched_chars": int(fetched.get("fetched_chars", 0)),
            "accepted": "true" if accepted else "false",
            "source_url": target["source_url"], "error": str(fetched.get("error", ""))[:300],
        })
        if not accepted:
            continue
        is_policy = bool(target.get("doc_id"))
        documents.append({
            "doc_id": doc_id, "source_type": target.get("source_type", "news"),
            "title": target.get("title") or _title_from_page(text, target["period_end"]),
            "content": text, "publish_time": target["release_date"],
            "source_name": target.get("source_name") or "国家统计局", "url": target["source_url"],
        })
    documents.sort(key=lambda row: (row["publish_time"], row["doc_id"]))
    _write_csv(destination.name, DOCUMENT_FIELDS, documents)
    _write_csv(
        "macro_historical_fetch_audit.csv",
        ["doc_id", "release_date", "period_end_described", "fetch_status", "fetched_chars", "accepted", "source_url", "error"],
        audit,
    )
    return documents


def build_historical_structures(documents: list[dict[str, str]]) -> None:
    """Run the same deterministic entity/event/19-predicate functions in memory."""
    links = link_documents(documents)
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in links:
        links_by_doc[str(link["doc_id"])].append({key: str(value) for key, value in link.items()})
    events = build_events({row["doc_id"]: row for row in documents}, links_by_doc)
    for index, event in enumerate(events, start=1):
        event["event_id"] = f"ME{index:04d}"
    link_lookup = {
        (str(row["doc_id"]), str(row["stock_code"])): str(row["industry"])
        for row in links
    }
    doc_lookup = {row["doc_id"]: row for row in documents}
    predicates: list[dict[str, Any]] = []
    for event in events:
        doc = doc_lookup[str(event["doc_id"])]
        sector = link_lookup[(str(event["doc_id"]), str(event["stock_code"]))]
        predicates.extend(ground_event_predicates({key: str(value) for key, value in event.items()}, doc, sector))
    _write_csv(
        "macro_historical_entity_links.csv",
        ["doc_id", "stock_code", "stock_name", "industry", "confidence", "evidence"], links,
    )
    _write_csv(
        "macro_historical_events.csv",
        ["event_id", "doc_id", "stock_code", "event_type", "event_time", "subject", "object", "impact_path", "evidence_text", "evidence_strength"], events,
    )
    _write_csv(
        "macro_historical_predicates.csv",
        ["event_id", "predicate_name", "value", "confidence", "rationale"], predicates,
    )


def build_historical_text_outputs() -> None:
    documents = ensure_historical_documents()
    build_historical_structures(documents)
    print(f"历史文本层完成：{len(documents)} 篇国家统计局官方发布文本")
