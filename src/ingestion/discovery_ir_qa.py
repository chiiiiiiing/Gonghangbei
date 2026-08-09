"""Stage only verifiable Discovery-period Q&A candidates from 深交所互动易.

This module deliberately does *not* write ``raw_documents.csv``.  The public
endpoint is treated as a candidate source rather than a trusted archive: every
record is filtered again by the timestamp returned in the listing and by the
timestamp returned from the question-detail endpoint.  This matters because a
remote date parameter can be ignored, truncated, or change semantics without
notice.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from src.ingestion.common import atomic_write_csv


DISCOVERY_START = date(2024, 1, 1)
DISCOVERY_END = date(2025, 12, 31)
DETAIL_URL = "https://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={}"
CANDIDATE_FIELDS = [
    "candidate_id",
    "stock_code",
    "stock_name",
    "question_id",
    "title",
    "content",
    "publish_time",
    "source_name",
    "url",
    "content_sha256",
    "review_status",
]


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _date_from_timestamp(value: Any) -> date | None:
    """Return a local calendar date from an API epoch timestamp, or ``None``."""
    try:
        timestamp = int(str(value))
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        # The source is a mainland-exchange platform.  Pinning the calendar
        # conversion avoids a collector running in another host timezone from
        # moving an otherwise valid item across a Discovery/OOS date boundary.
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Asia/Shanghai")).date()
    except (OverflowError, OSError, ValueError):
        return None


def _in_discovery_window(value: Any, start: date, end: date) -> bool:
    event_date = _date_from_timestamp(value)
    return event_date is not None and start <= event_date <= end


def _candidate_hash(question: str, reply: str) -> str:
    return hashlib.sha256(f"{question}\n{reply}".encode("utf-8")).hexdigest()


def _candidate_from_detail(
    stock: dict[str, str], question_id: str, payload: dict[str, Any], start: date, end: date
) -> dict[str, str] | None:
    data = payload.get("data") or {}
    if str(data.get("stockCode", "")) != stock["stock_code"]:
        return None
    publish_date = _date_from_timestamp(data.get("questionDate"))
    if publish_date is None or not start <= publish_date <= end:
        return None
    question = _clean_text(data.get("questionContent"))
    reply = _clean_text(data.get("replyContent"))
    if not question or not reply:
        return None
    title_excerpt = question[:70] + ("…" if len(question) > 70 else "")
    content = f"投资者提问原文：{question}\n公司回复原文：{reply}"
    return {
        "candidate_id": f"IRM-{question_id}",
        "stock_code": stock["stock_code"],
        "stock_name": stock["stock_name"],
        "question_id": question_id,
        "title": f"投资者问答：{stock['stock_name']}回应“{title_excerpt}”",
        "content": content,
        "publish_time": publish_date.isoformat(),
        "source_name": "深交所互动易",
        "url": DETAIL_URL.format(question_id),
        "content_sha256": _candidate_hash(question, reply),
        "review_status": "pending_manual_review",
    }


class OfficialIRMClient:
    """Small client for the public, read-only 深交所互动易 endpoints."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; AlphaLens/1.0 research)",
                "Referer": "https://irm.cninfo.com.cn/ircs/search",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json,text/plain,*/*",
            }
        )

    def resolve_org_id(self, stock_code: str) -> str | None:
        response = self.session.post(
            "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo",
            data={"keyWord": stock_code},
            timeout=(5, 15),
        )
        response.raise_for_status()
        for item in response.json().get("data") or []:
            if str(item.get("stockCode")) == stock_code and item.get("secid"):
                return str(item["secid"])
        return None

    def list_questions(
        self, stock_code: str, org_id: str, page_num: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        # The platform's own company page uses query parameters for this POST.
        # Returned timestamps, not these request parameters, decide inclusion.
        response = self.session.post(
            "https://irm.cninfo.com.cn/newircs/company/question",
            params={
                "stockcode": stock_code,
                "orgId": org_id,
                "pageSize": 10,
                "pageNum": page_num,
                "keyWord": "",
                "startDay": start.isoformat(),
                "endDay": end.isoformat(),
            },
            timeout=(5, 15),
        )
        response.raise_for_status()
        return list(response.json().get("rows") or [])

    def question_detail(self, question_id: str) -> dict[str, Any]:
        response = self.session.get(
            "https://irm.cninfo.com.cn/newircs/question/getQuestionDetail",
            params={"questionId": question_id},
            timeout=(5, 15),
        )
        response.raise_for_status()
        return dict(response.json())


def collect_candidates(
    stocks: Iterable[dict[str, str]],
    client: Any,
    *,
    start: date = DISCOVERY_START,
    end: date = DISCOVERY_END,
    pages_per_stock: int = 3,
    max_candidates: int = 25,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Fetch candidates without trusting remote filters or changing sample data."""
    if pages_per_stock < 1 or max_candidates < 1:
        raise ValueError("pages_per_stock 与 max_candidates 必须为正整数")
    candidates: list[dict[str, str]] = []
    seen_questions: set[str] = set()
    seen_hashes: set[str] = set()
    audit: Counter[str] = Counter()
    unmapped_stocks: list[str] = []
    failed_stock_codes: list[str] = []
    date_filter_mismatches: list[str] = []

    for stock in stocks:
        if len(candidates) >= max_candidates:
            break
        stock_code = str(stock["stock_code"])
        try:
            org_id = client.resolve_org_id(stock_code)
        except Exception:
            audit["org_lookup_failed"] += 1
            failed_stock_codes.append(stock_code)
            continue
        if not org_id:
            unmapped_stocks.append(stock_code)
            continue
        audit["resolved_stock_count"] += 1
        listing_in_window = 0
        listing_total = 0
        for page_num in range(1, pages_per_stock + 1):
            try:
                rows = client.list_questions(stock_code, org_id, page_num, start, end)
            except Exception:
                audit["listing_request_failed"] += 1
                failed_stock_codes.append(stock_code)
                break
            if not rows:
                break
            listing_total += len(rows)
            for row in rows:
                if str(row.get("stockCode", "")) != stock_code:
                    audit["listing_stock_mismatch"] += 1
                    continue
                question_id = str(row.get("indexId") or "")
                if not question_id or question_id in seen_questions:
                    audit["duplicate_question_id"] += 1
                    continue
                if not _in_discovery_window(row.get("pubDate"), start, end):
                    audit["listing_out_of_window"] += 1
                    continue
                listing_in_window += 1
                seen_questions.add(question_id)
                audit["detail_requests"] += 1
                try:
                    detail = client.question_detail(question_id)
                except Exception:
                    audit["detail_request_failed"] += 1
                    continue
                candidate = _candidate_from_detail(stock, question_id, detail, start, end)
                if candidate is None:
                    audit["detail_rejected"] += 1
                    continue
                if candidate["content_sha256"] in seen_hashes:
                    audit["duplicate_content"] += 1
                    continue
                seen_hashes.add(candidate["content_sha256"])
                candidates.append(candidate)
                audit["candidate_count"] += 1
                if len(candidates) >= max_candidates:
                    break
            if len(candidates) >= max_candidates:
                break
        audit["listing_total"] += listing_total
        if listing_total and not listing_in_window:
            date_filter_mismatches.append(stock_code)

    report = {
        "status": "pending_manual_review" if candidates else "no_verified_candidates",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "candidate_count": len(candidates),
        "requested_pages_per_stock": pages_per_stock,
        "requested_max_candidates": max_candidates,
        "audit_counts": dict(sorted(audit.items())),
        "unmapped_stock_codes": unmapped_stocks,
        "failed_stock_codes": sorted(set(failed_stock_codes)),
        "date_filter_mismatch_stock_codes": date_filter_mismatches,
        "next_step": (
            "仅将人工逐条确认过的候选复制到互动问答采集模板.csv，再运行导入真实文本.py。"
        ),
    }
    return candidates, report


def write_candidate_bundle(
    candidates: list[dict[str, str]], report: dict[str, Any], output_dir: Path
) -> tuple[Path, Path]:
    """Write an ignored external staging bundle; no locked sample CSV is touched."""
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "Discovery互动问答候选.csv"
    report_path = output_dir / "Discovery互动问答候选报告.json"
    atomic_write_csv(candidates_path, CANDIDATE_FIELDS, candidates)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return candidates_path, report_path
