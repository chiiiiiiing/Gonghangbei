"""Build a source-hashed PBOC policy corpus from official public pages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import TEXT_FIELDS  # noqa: E402


HOST = "https://www.pbc.gov.cn"
OMO_LIST_ROOT = HOST + "/zhengcehuobisi/125207/125213/125431/125475/"
OMO_LIST_ID = "17081"
REPORT_LIST_ROOT = HOST + "/goutongjiaoliu/113456/113469/"
REPORT_LIST_ID = "11040"
MOF_HOST = "https://www.mof.gov.cn"
MOF_LIST_ROOT = MOF_HOST + "/gkml/bulinggonggao/tongzhitonggao/"
OUT = ROOT / "data" / "sample" / "rates_policy_texts.csv"
AUDIT_OUT = ROOT / "data" / "sample" / "rates_policy_source_audit.json"


def fetch(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/2.0"})
            with urlopen(request, timeout=90) as response:
                return response.read()
        except Exception as exc:  # network boundary: retry then surface the original endpoint
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}：{last_error}")


def _meta(source: str, name: str) -> str:
    match = re.search(
        rf'<meta\s+(?:name|Name)=["\']{re.escape(name)}["\']\s+content=["\'](.*?)["\']\s*/?>',
        source, flags=re.IGNORECASE | re.DOTALL,
    )
    return html.unescape(match.group(1).strip()) if match else ""


def _clean_fragment(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.IGNORECASE | re.DOTALL)
    fragment = re.sub(r"</(?:p|tr|div|li|h\d)>", "。", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"</(?:td|th)>", "；", fragment, flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    text = re.sub(r"[\t\r\n\u3000 ]+", "", text)
    text = re.sub(r"[。；]{2,}", "。", text).strip("。；")
    return text[:12000]


def _article_content(source: str) -> str:
    match = re.search(r'<div\s+id=["\']zoom["\'][^>]*>(.*?)<div\s+style=["\']clear:both', source, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(r'<div\s+id=["\']zoom["\'][^>]*>(.*?)</div>', source, flags=re.IGNORECASE | re.DOTALL)
    return _clean_fragment(match.group(1)) if match else _meta(source, "Description")


def _mof_article_content(source: str) -> str:
    match = re.search(
        r'<div\s+class=["\']my_conboxzw["\'][^>]*>(.*?)<div\s+class=["\']conbottom["\']',
        source, flags=re.IGNORECASE | re.DOTALL,
    )
    return _clean_fragment(match.group(1)) if match else _meta(source, "Description")


def _listing_url(root: str, listing_id: str, page: int) -> str:
    return root + ("index.html" if page == 1 else f"{listing_id}-{page}.html")


def _listing_entries(body: bytes, wanted: re.Pattern[str]) -> list[tuple[str, str, str]]:
    source = body.decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<a\s+href="([^"]+/index\.html)"[^>]*?\stitle="([^"]+)"[^>]*>.*?</a>.*?'
        r'<span\s+class="hui12">(\d{4}-\d{2}-\d{2})</span>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [
        (urljoin(HOST, link), html.unescape(title).strip(), day)
        for link, title, day in pattern.findall(source) if wanted.search(title)
    ]


def crawl_listings(
    root: str, listing_id: str, max_pages: int, wanted: re.Pattern[str], start_date: str, workers: int
) -> list[tuple[str, str, str]]:
    entries: dict[str, tuple[str, str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_urls = {
            pool.submit(fetch, _listing_url(root, listing_id, page)): _listing_url(root, listing_id, page)
            for page in range(1, max_pages + 1)
        }
        for future in as_completed(future_urls):
            try:
                rows = _listing_entries(future.result(), wanted)
            except Exception as exc:
                print(f"warning: listing failed {future_urls[future]}: {exc}", file=sys.stderr)
                continue
            for row in rows:
                if row[2] >= start_date:
                    entries[row[0]] = row
    return sorted(entries.values(), key=lambda row: (row[2], row[0]))


def _mof_listing_url(page: int) -> str:
    return MOF_LIST_ROOT + ("index.htm" if page == 1 else f"index_{page}.htm")


def _mof_listing_entries(body: bytes, start_date: str) -> list[tuple[str, str, str]]:
    source = body.decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<li>\s*<a\s+href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']+)["\'][^>]*>.*?</a>'
        r'\s*<span>(\d{4}-\d{2}-\d{2})</span>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    wanted = re.compile(r"(?:国债|政府债券).*(?:发行|续发行)|(?:发行|续发行).*(?:国债|政府债券)")
    return [
        (urljoin(MOF_HOST, link).replace("http://", "https://", 1), html.unescape(title).strip(), day)
        for link, title, day in pattern.findall(source)
        if day >= start_date and wanted.search(title)
    ]


def crawl_mof_listings(max_pages: int, start_date: str, workers: int) -> list[tuple[str, str, str]]:
    entries: dict[str, tuple[str, str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_urls = {
            pool.submit(fetch, _mof_listing_url(page)): _mof_listing_url(page)
            for page in range(1, max_pages + 1)
        }
        for future in as_completed(future_urls):
            try:
                rows = _mof_listing_entries(future.result(), start_date)
            except Exception as exc:
                print(f"warning: MOF listing failed {future_urls[future]}: {exc}", file=sys.stderr)
                continue
            for row in rows:
                entries[row[0]] = row
    return sorted(entries.values(), key=lambda row: (row[2], row[0]))


def select_weekly_omo(entries: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Use the first chronological OMO announcement in every ISO week."""
    selected: dict[tuple[int, int], tuple[str, str, str]] = {}
    for entry in entries:
        day = date.fromisoformat(entry[2])
        key = (day.isocalendar().year, day.isocalendar().week)
        selected.setdefault(key, entry)
    return sorted(selected.values(), key=lambda row: (row[2], row[0]))


def fetch_article(entry: tuple[str, str, str]) -> dict[str, str]:
    url, listing_title, listing_date = entry
    body = fetch(url)
    source = body.decode("utf-8", errors="replace")
    title = _meta(source, "ArticleTitle") or listing_title
    publish_date = _meta(source, "PubDate") or listing_date
    visible_time = re.search(r'id=["\']shijian["\']>(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})<', source)
    publish_time = visible_time.group(1) if visible_time and visible_time.group(1).startswith(publish_date) else publish_date + " 09:00:00"
    content = _article_content(source)
    if not title or not publish_date or len(content) < 20:
        raise ValueError(f"央行页面正文或元数据不完整：{url}")
    kind = "REPORT" if "货币政策执行报告" in title else "OMO"
    identity = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return {
        "doc_id": f"PBC-{kind}-{publish_date.replace('-', '')}-{identity}",
        "publish_time": publish_time, "title": title, "content": content,
        "source_name": "中国人民银行", "source_url": url,
        "source_sha256": hashlib.sha256(body).hexdigest(),
    }


def fetch_mof_article(entry: tuple[str, str, str]) -> dict[str, str]:
    url, listing_title, listing_date = entry
    body = fetch(url)
    source = body.decode("utf-8", errors="replace")
    title = _meta(source, "ArticleTitle") or listing_title
    publish_value = _meta(source, "PubDate") or listing_date
    publish_time = publish_value if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", publish_value) else listing_date + " 09:00:00"
    content = _mof_article_content(source)
    if not title or len(content) < 40:
        raise ValueError(f"财政部页面正文或元数据不完整：{url}")
    identity = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return {
        "doc_id": f"MOF-BOND-{listing_date.replace('-', '')}-{identity}",
        "publish_time": publish_time, "title": title, "content": content,
        "source_name": "财政部", "source_url": url,
        "source_sha256": hashlib.sha256(body).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--omo-pages", type=int, default=145)
    parser.add_argument("--report-pages", type=int, default=300)
    parser.add_argument("--mof-pages", type=int, default=22)
    parser.add_argument("--mof-workers", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    date.fromisoformat(args.start_date)
    # The clean submission keeps the verified NBS/MOF sample in this same
    # contract. Preserve those sources while refreshing the PBOC listings so
    # the following augmentation step can run without the removed legacy
    # macro corpus.
    preserved: list[dict[str, str]] = []
    if OUT.exists():
        with OUT.open(encoding="utf-8", newline="") as handle:
            preserved = [
                row for row in csv.DictReader(handle)
                if row.get("source_name") in {"国家统计局", "财政部"}
            ]
    omo = crawl_listings(
        OMO_LIST_ROOT, OMO_LIST_ID, args.omo_pages,
        re.compile(r"^公开市场业务交易公告"), args.start_date, args.workers,
    )
    reports = crawl_listings(
        REPORT_LIST_ROOT, REPORT_LIST_ID, args.report_pages,
        re.compile(r"货币政策执行报告"), args.start_date, args.workers,
    )
    mof = select_weekly_omo(crawl_mof_listings(args.mof_pages, args.start_date, args.workers))
    rows: list[dict[str, str]] = []
    batches = (
        (select_weekly_omo(omo) + reports, fetch_article, args.workers),
        (mof, fetch_mof_article, min(max(1, args.mof_workers), 2)),
    )
    for entries, loader, worker_count in batches:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(loader, entry): entry[0] for entry in entries}
            for future in as_completed(futures):
                try:
                    rows.append(future.result())
                except Exception as exc:
                    print(f"warning: article failed {futures[future]}: {exc}", file=sys.stderr)
    by_url = {row["source_url"]: row for row in preserved + rows if row.get("source_url")}
    rows = sorted(by_url.values(), key=lambda row: (row["publish_time"], row["doc_id"]))
    report_count = sum("货币政策执行报告" in row["title"] for row in rows)
    mof_count = sum(row["source_name"] == "财政部" for row in rows)
    if len(rows) < 150 or not report_count:
        raise ValueError(f"政策文本仅抓取{len(rows)}篇或缺少货币政策报告，拒绝覆盖现有样本")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEXT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    audit = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": {"start": rows[0]["publish_time"], "end": rows[-1]["publish_time"]},
        "documents": len(rows), "pbc_monetary_policy_reports": report_count,
        "pbc_weekly_omo_announcements": sum(row["doc_id"].startswith("PBC-OMO") for row in rows),
        "mof_government_bond_announcements": mof_count,
        "sources": sorted({row["source_name"] for row in rows}),
        "method": "央行官方列表刷新；保留已核验统计局/财政部样本；官方正文、响应SHA-256与发布日期时间对齐",
        "optional_source_warning": "财政部国债发行公告为可选来源；若站点限流，数量不足不覆盖央行文本样本。",
    }
    AUDIT_OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} official policy texts ({report_count} reports, {mof_count} MOF bond announcements) to {OUT}")


if __name__ == "__main__":
    main()
