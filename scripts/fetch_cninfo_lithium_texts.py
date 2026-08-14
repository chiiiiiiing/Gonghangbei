"""Collect audited historical lithium-industry announcements from CNINFO.

Selection is based only on issuer, title and document relevance. Market returns
are deliberately unavailable to this script so the corpus cannot be chosen by
backtest outcome.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

try:
    import pypdf
except ImportError:  # pragma: no cover
    pypdf = None


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STOCK_LIST_URL = "http://www.cninfo.com.cn/new/data/{kind}.json"
PDF_PREFIX = "https://static.cninfo.com.cn/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.cninfo.com.cn/",
}
ISSUERS = {
    "002460": "赣锋锂业",
    "002466": "天齐锂业",
    "002240": "盛新锂能",
    "002497": "雅化集团",
    "002756": "永兴材料",
    "000792": "盐湖股份",
    "000408": "藏格矿业",
    "002176": "江特电机",
    "300390": "天华新能",
    "002738": "中矿资源",
    "000762": "西藏矿业",
    "300750": "宁德时代",
    "002594": "比亚迪",
}
DEMAND_ISSUERS = {"300750", "002594"}
EVENT_TITLE_MARKERS = (
    "项目", "投产", "停产", "复产", "产能", "扩建", "技改", "锂矿", "矿权",
    "采矿", "探矿", "盐湖", "资源量", "储量", "收购", "出售", "投资", "协议", "交割",
    "减值", "业绩预告", "经营情况", "产品价格", "期货", "套期保值",
    "重大合同", "进展", "产销快报", "销量",
)
SKIP_TITLE_MARKERS = (
    "章程", "议事规则", "法律意见书", "股东会", "董事会决议", "监事会决议",
    "独立董事", "审计报告", "社会责任报告", "ESG", "分红", "派息", "付息",
    "兑付", "提示性公告", "股票交易异常波动", "减持", "增持", "质押",
    "解除限售", "更正公告", "募集说明书", "募集资金", "发行预案", "发行方案",
    "问询函回复", "投资者关系活动记录", "年度报告", "半年度报告", "季度报告",
    "债券", "担保", "薪酬", "管理制度", "工作制度", "辞职", "聘任", "选举",
    "可行性分析报告", "核查意见", "审核报告",
)
LITHIUM_TERMS = (
    "碳酸锂", "氢氧化锂", "锂盐", "锂矿", "锂辉石", "盐湖提锂", "锂云母",
    "原卤", "锂资源", "锂产品",
)
DEMAND_TERMS = (
    "动力电池", "储能电池", "新能源汽车", "电池装车", "电池销量", "电池产能",
)
SOURCE_FIELDS = [
    "doc_id", "source_type", "title", "content", "publish_time",
    "source_name", "url", "review_status", "stock_code", "issuer_name",
]
AUDIT_FIELDS = [
    "doc_id", "stock_code", "issuer_name", "publish_time", "announcement_id",
    "url", "pdf_sha256", "content_sha256", "pdf_bytes", "pdf_pages",
    "selected_chars", "queried_at", "selection_policy",
]


def clean_title(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def cninfo_column(code: str) -> str:
    return "szse" if code.startswith(("0", "3")) else "sse"


def build_stock_map(session: requests.Session) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for kind in ("szse_stock", "sse_stock"):
        response = session.get(STOCK_LIST_URL.format(kind=kind), headers=HEADERS, timeout=30)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        for stock in response.json().get("stockList", []):
            code = str(stock.get("code", ""))
            if code:
                result[code] = {
                    "org_id": str(stock.get("orgId", "")),
                    "name": str(stock.get("zwjc") or ""),
                }
    return result


def query_all(
    session: requests.Session,
    stock_code: str,
    org_id: str,
    start: str,
    end: str,
    delay: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = {
            "pageNum": page,
            "pageSize": 30,
            "column": cninfo_column(stock_code),
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{stock_code},{org_id}",
            "searchkey": "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{start}~{end}",
            "sortName": "time",
            "sortType": "asc",
            "isHLtitle": "true",
        }
        response = session.post(QUERY_URL, data=payload, headers=HEADERS, timeout=30)
        response.raise_for_status()
        body = response.json()
        page_rows = body.get("announcements") or []
        rows.extend(page_rows)
        total = int(body.get("totalAnnouncement") or len(rows))
        if not page_rows or len(rows) >= total:
            break
        page += 1
        time.sleep(delay)
    return rows


def title_selected(stock_code: str, title: str) -> bool:
    if any(marker in title for marker in SKIP_TITLE_MARKERS):
        return False
    if not any(marker in title for marker in EVENT_TITLE_MARKERS):
        return False
    if stock_code in DEMAND_ISSUERS:
        return any(marker in title for marker in ("电池", "新能源", "产销", "销量", "项目", "产能"))
    return True


def extract_pdf(pdf_bytes: bytes, max_pages: int = 8) -> tuple[str, int]:
    if pypdf is None:
        raise RuntimeError("请先安装 pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    parts: list[str] = []
    for page in reader.pages[:max_pages]:
        text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip(), len(reader.pages)


def content_selected(stock_code: str, title: str, content: str) -> bool:
    combined = f"{title}\n{content}"
    required = DEMAND_TERMS if stock_code in DEMAND_ISSUERS else LITHIUM_TERMS
    return any(term in combined for term in required)


def announcement_date(item: dict[str, Any]) -> str:
    value = item.get("announcementTime")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).date().isoformat()
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(item.get("adjunctUrl", "")))
    if not match:
        raise ValueError("公告缺少可核验日期")
    return match.group(1)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-07-21")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--max-per-company", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "research" / "lithium_v3_cninfo_sources.csv",
    )
    args = parser.parse_args()
    session = requests.Session()
    stock_map = build_stock_map(session)
    raw_dir = ROOT / "data" / "raw" / "lithium_cninfo"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for stock_code, expected_name in ISSUERS.items():
        info = stock_map.get(stock_code)
        if not info or not info["org_id"]:
            failures.append({"stock_code": stock_code, "error": "missing_org_id"})
            continue
        announcements = query_all(
            session, stock_code, info["org_id"], args.start, args.end, args.delay
        )
        candidates = [
            item for item in announcements
            if title_selected(stock_code, clean_title(str(item.get("announcementTitle", ""))))
        ]
        accepted = 0
        for item in candidates:
            adjunct = str(item.get("adjunctUrl", "")).strip()
            if not adjunct:
                continue
            announcement_id = str(item.get("announcementId") or Path(adjunct).stem)
            cache_path = raw_dir / f"{announcement_id}.pdf"
            try:
                if cache_path.exists():
                    pdf_bytes = cache_path.read_bytes()
                else:
                    response = session.get(PDF_PREFIX + adjunct, headers=HEADERS, timeout=45)
                    response.raise_for_status()
                    pdf_bytes = response.content
                    cache_path.write_bytes(pdf_bytes)
                    time.sleep(args.delay)
                content, page_count = extract_pdf(pdf_bytes)
            except Exception as exc:  # noqa: BLE001
                failures.append({"stock_code": stock_code, "error": f"{adjunct}: {exc}"})
                continue
            title = clean_title(str(item.get("announcementTitle", "")))
            if len(content) < 80 or not content_selected(stock_code, title, content):
                continue
            publish_time = announcement_date(item)
            selected = content[:12000]
            doc_id = f"CNINFO-{stock_code}-{announcement_id}"
            source_name = "巨潮资讯网"
            url = PDF_PREFIX + adjunct
            rows.append({
                "doc_id": doc_id,
                "source_type": "announcement",
                "title": title,
                "content": selected,
                "publish_time": publish_time,
                "source_name": source_name,
                "url": url,
                "review_status": "accepted",
                "stock_code": stock_code,
                "issuer_name": info["name"] or expected_name,
            })
            audit.append({
                "doc_id": doc_id,
                "stock_code": stock_code,
                "issuer_name": info["name"] or expected_name,
                "publish_time": publish_time,
                "announcement_id": announcement_id,
                "url": url,
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "content_sha256": hashlib.sha256(selected.encode("utf-8")).hexdigest(),
                "pdf_bytes": len(pdf_bytes),
                "pdf_pages": page_count,
                "selected_chars": len(selected),
                "queried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "selection_policy": "issuer_title_and_document_relevance_without_market_returns",
            })
            accepted += 1
            if accepted >= args.max_per_company:
                break
        print(f"{stock_code} {info['name']}: {accepted}/{len(announcements)}", flush=True)
    rows.sort(key=lambda row: (row["publish_time"], row["doc_id"]))
    audit.sort(key=lambda row: (row["publish_time"], row["doc_id"]))
    write_csv(args.out, SOURCE_FIELDS, rows)
    audit_path = args.out.with_name(f"{args.out.stem}_audit.csv")
    write_csv(audit_path, AUDIT_FIELDS, audit)
    print(json.dumps({
        "documents": len(rows),
        "audit_rows": len(audit),
        "failures": len(failures),
        "output": str(args.out.relative_to(ROOT)),
        "audit": str(audit_path.relative_to(ROOT)),
        "failure_sample": failures[:5],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
