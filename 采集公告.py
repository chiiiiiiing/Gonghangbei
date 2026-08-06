"""从巨潮资讯（cninfo）抓取股票池公司 2024—2025 真实公告，生成采集清单。

- 对股票池每只股票调用 cninfo 公告检索 API，取 2024-01-01~2025-12-31 公告。
- 优先挑选标题含事件关键词（扩产/中标/投资/业绩/回购/减持/质押等）的公告，
  保证公告既填补 discovery/announcement 覆盖缺口，又能产生事件。
- 下载公告 PDF 并用 pypdf 抽取正文摘要作为 content。
- 产物为锁定字段的采集清单，可交给 预演导入.py 与 导入真实文本.py。

用法：
    .venv/bin/python 采集公告.py --out data/external/文本导入暂存/discovery公告.csv [--limit 25]
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from src.pipeline.extract_events_rule_based import EVENT_KEYWORDS

try:
    import pypdf
except ImportError:  # pragma: no cover
    raise SystemExit("请先安装 pypdf：.venv/bin/pip install pypdf")

ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "data" / "sample"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_PREFIX = "http://static.cninfo.com.cn/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "http://www.cninfo.com.cn/",
}
# 能触发事件提取的标题关键词（与 src/pipeline/extract_events_rule_based 对齐）
EVENT_TITLE_KEYWORDS = [
    keyword for keyword_list in EVENT_KEYWORDS.values() for keyword in keyword_list
]
# 明确排除的杂项公告类型
SKIP_MARKERS = [
    "章程", "议事规则", "法律意见书", "股东大会决议", "股东大会通知",
    "会议资料", "专项意见", "独立董事", "审计报告", "信用评级",
    "付息", "兑付", "交易异常波动", "更正公告", "回购", "增持", "减持",
    "质押", "解除质押",
]

FIELDS = ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"]


def cninfo_column(code: str) -> str:
    return "szse" if code.startswith(("0", "3")) else "sse"


def build_stock_map() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for kind in ("szse_stock", "sse_stock"):
        try:
            payload = requests.get(
                f"http://www.cninfo.com.cn/new/data/{kind}.json", headers=HEADERS, timeout=30
            ).json()
        except Exception:  # noqa: BLE001
            continue
        for stock in payload.get("stockList", []):
            code = str(stock.get("code", ""))
            if code:
                result[code] = {"orgId": str(stock.get("orgId", "")), "name": str(stock.get("zwjc") or "")}
    return result


def query_announcements(stock_code: str, org_id: str) -> list[dict[str, str]]:
    params = {
        "pageNum": 1,
        "pageSize": 30,
        "column": cninfo_column(stock_code),
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{stock_code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "2024-01-01~2025-12-31",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(QUERY_URL, data=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    rows = []
    for item in response.json().get("announcements", []):
        rows.append(
            {
                "title": str(item.get("announcementTitle", "")).strip(),
                "adjunctUrl": str(item.get("adjunctUrl", "")).strip(),
            }
        )
    return rows


def extract_pdf_text(adjunct_url: str) -> str:
    response = requests.get(PDF_PREFIX + adjunct_url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    reader = pypdf.PdfReader(io.BytesIO(response.content))
    parts: list[str] = []
    for page in reader.pages[:3]:
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def clean_text(text: str, max_len: int = 600) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def main() -> int:
    parser = argparse.ArgumentParser(description="AlphaLens 股票池公司公告采集")
    parser.add_argument("--out", type=Path, default=Path("data/external/文本导入暂存/discovery公告.csv"))
    parser.add_argument("--limit", type=int, default=25, help="最多生成多少篇公告")
    args = parser.parse_args()

    with (SAMPLE_DIR / "stock_pool.csv").open(encoding="utf-8", newline="") as handle:
        import csv

        pool = list(csv.DictReader(handle))
    live_ids: set[str] = set()
    if (SAMPLE_DIR / "raw_documents.csv").exists():
        with (SAMPLE_DIR / "raw_documents.csv").open(encoding="utf-8", newline="") as handle:
            live_ids = {row["doc_id"] for row in csv.DictReader(handle)}
    stock_map = build_stock_map()

    candidates: list[dict[str, str]] = []
    for stock in pool:
        code = stock["stock_code"]
        info = stock_map.get(code)
        if not info or not info["orgId"]:
            print(f"跳过 {code}（未找到 cninfo orgId）")
            continue
        try:
            announcements = query_announcements(code, info["orgId"])
        except Exception as exc:  # noqa: BLE001
            print(f"查询失败 {code}: {exc}")
            continue
        for ann in announcements:
            title = ann["title"]
            if any(marker in title for marker in SKIP_MARKERS):
                continue
            if not any(keyword in title for keyword in EVENT_TITLE_KEYWORDS):
                continue
            candidates.append(ann)
        time.sleep(0.3)

    candidates.sort(key=lambda item: item["title"])
    seen_urls: set[str] = set()
    rows: list[dict[str, str]] = []
    applied_numbers = {int(doc_id[5:]) for doc_id in live_ids if doc_id.startswith("DANN-")}
    doc_index = max(applied_numbers, default=0) + 1
    for ann in candidates:
        if ann["adjunctUrl"] in seen_urls:
            continue
        seen_urls.add(ann["adjunctUrl"])
        try:
            pdf_text = extract_pdf_text(ann["adjunctUrl"])
        except Exception as exc:  # noqa: BLE001
            print(f"下载失败 {ann['title'][:30]}: {exc}")
            continue
        excerpt = clean_text(pdf_text)
        if len(excerpt) < 40:
            continue  # 扫描件/无文本，跳过
        date = re.search(r"(\d{4}-\d{2}-\d{2})", ann["adjunctUrl"])
        publish_time = date.group(1) if date else "2025-12-31"
        rows.append(
            {
                "doc_id": f"DANN-{doc_index:03d}",
                "source_type": "announcement",
                "title": ann["title"],
                "content": (
                    f"原文摘要：{excerpt} 来源为巨潮资讯网，首次公开日期核验为{publish_time}。"
                ),
                "publish_time": publish_time,
                "source_name": "巨潮资讯网",
                "url": PDF_PREFIX + ann["adjunctUrl"],
            }
        )
        doc_index += 1
        print(f"DANN-{doc_index - 1:03d}: {publish_time} {ann['title'][:36]}")
        if len(rows) >= args.limit:
            break
        time.sleep(0.3)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n完成：{len(rows)} 篇公告 → {args.out}")
    if len(rows) < args.limit:
        print(f"注意：未达到 {args.limit} 篇，只有 {len(rows)} 篇（可能受扫描件/API 限制）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
