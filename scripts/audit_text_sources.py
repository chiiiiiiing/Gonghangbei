"""Audit traceability and source quality for AlphaLens raw documents."""

from __future__ import annotations

import csv
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fetch_verified_text_sources import (
    ANNOUNCEMENT_EXCLUDE_KEYWORDS,
    NEWS_DOMAINS,
    OFFICIAL_DOMAINS,
    is_detail_url,
)


RAW_PATH = ROOT / "data" / "sample" / "raw_documents.csv"
VIEW_DIR = ROOT / "查看材料"
DETAIL_PATH = VIEW_DIR / "源文本核验明细.csv"
REPORT_PATH = VIEW_DIR / "真实文本来源核验报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

FIELDS = [
    "doc_id",
    "source_type",
    "title",
    "publish_time",
    "source_name",
    "url",
    "http_status",
    "detail_url_valid",
    "domain_allowed",
    "url_unique",
    "content_structure_valid",
    "verification_result",
    "verification_note",
]


def read_documents() -> list[dict[str, str]]:
    with RAW_PATH.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def domain_is_allowed(url: str, source_type: str) -> bool:
    host = urlparse(url).netloc.lower()
    if source_type == "announcement":
        return "static.cninfo.com.cn" in host
    if source_type == "ir_qa":
        return "irm.cninfo.com.cn" in host
    domains = OFFICIAL_DOMAINS if source_type == "policy" else NEWS_DOMAINS
    return any(domain in host for domain in domains)


def fetch_status(url: str) -> str:
    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"},
                timeout=(4, 10),
                stream=True,
                allow_redirects=True,
            )
            status = str(response.status_code)
            response.close()
            return status
        except requests.RequestException:
            time.sleep(0.4 * (attempt + 1))
    return "unreachable"


def fetch_statuses(urls: set[str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_url = {executor.submit(fetch_status, url): url for url in urls}
        for future in as_completed(future_to_url):
            statuses[future_to_url[future]] = future.result()
    return statuses


def announcement_is_usable(row: dict[str, str]) -> bool:
    title = row["title"]
    return not any(keyword in title for keyword in ANNOUNCEMENT_EXCLUDE_KEYWORDS)


def audit_row(row: dict[str, str], url_counts: Counter[str], status: str) -> dict[str, str]:
    source_type = row["source_type"]
    details_valid = is_detail_url(row["url"], source_type)
    allowed = domain_is_allowed(row["url"], source_type)
    unique = url_counts[row["url"]] == 1
    content_valid = (
        row["content"].startswith("原文摘要：")
        and "项目关联：" in row["content"]
        and len(row["content"]) >= 80
        and "待人工核验" not in row["content"]
    )
    date_valid = "2024-01-01" <= row["publish_time"] <= "2026-06-30"
    online_ok = status.isdigit() and int(status) < 500
    semantic_ok = True
    semantic_note = ""
    if source_type == "announcement" and not announcement_is_usable(row):
        semantic_ok = False
        semantic_note = "公告属于评级、分红法律意见等不进入研究事件的例行披露"
    if source_type == "ir_qa" and "公司回复摘要" not in row["content"]:
        semantic_ok = False
        semantic_note = "互动问答缺少公司回复摘要"
    if "详情页标题显示该来源围绕" in row["content"]:
        semantic_ok = False
        semantic_note = "详情页正文摘要未成功提取，仅核验到标题"

    failures: list[str] = []
    if not details_valid:
        failures.append("URL 不是详情页")
    if not allowed:
        failures.append("来源域名不在白名单")
    if not unique:
        failures.append("URL 重复")
    if not content_valid:
        failures.append("摘要结构不完整")
    if not date_valid:
        failures.append("日期超出研究窗口或格式异常")
    if not online_ok:
        failures.append(f"联网状态为 {status}")
    if not semantic_ok:
        failures.append(semantic_note)

    return {
        "doc_id": row["doc_id"],
        "source_type": source_type,
        "title": row["title"],
        "publish_time": row["publish_time"],
        "source_name": row["source_name"],
        "url": row["url"],
        "http_status": status,
        "detail_url_valid": bool_text(details_valid),
        "domain_allowed": bool_text(allowed),
        "url_unique": bool_text(unique),
        "content_structure_valid": bool_text(content_valid),
        "verification_result": "pass" if not failures else "revise",
        "verification_note": "；".join(failures) if failures else "来源、日期、详情页、摘要结构和研究相关性自动核验通过",
    }


def write_outputs(rows: list[dict[str, str]]) -> None:
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    with DETAIL_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    result_counts = Counter(row["verification_result"] for row in rows)
    type_counts = Counter(row["source_type"] for row in rows)
    pass_by_type = Counter(row["source_type"] for row in rows if row["verification_result"] == "pass")
    status_counts = Counter(row["http_status"] for row in rows)
    lines = [
        "# AlphaLens 真实文本来源核验报告",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        DISCLAIMER,
        "",
        "## 核验结论",
        "",
        f"- 文本总数：{len(rows)}",
        f"- 自动核验通过：{result_counts['pass']}",
        f"- 需要修正：{result_counts['revise']}",
        f"- URL 唯一数：{len({row['url'] for row in rows})}",
        "",
        "| 来源类型 | 总数 | 通过 | 需修正 |",
        "|----------|------|------|--------|",
    ]
    for source_type in ["policy", "announcement", "news", "ir_qa"]:
        total = type_counts[source_type]
        passed = pass_by_type[source_type]
        lines.append(f"| {source_type} | {total} | {passed} | {total - passed} |")
    lines.extend(
        [
            "",
            "## 核验方法",
            "",
            "- 政策仅接受政府、部委和能源主管部门详情页；新闻仅接受财经媒体、行业协会和行业媒体白名单。",
            "- 公告必须是巨潮资讯网 PDF；优先选择项目、订单、处罚、问询或经营异常文件，年度报告等权威文件可留在证据库，但只有原文明确支持时才抽取事件。",
            "- 互动问答必须是互动易详情页，正文同时保留真实问题和公司回复摘要；单条问答不直接代表提问压力增加。",
            "- 全部文本检查 URL 详情页、日期窗口、摘要结构、URL 唯一性和联网响应状态。",
            "- 本报告属于程序化来源核验与语义规则审计，不能替代版权授权、交易所法律意见或数据供应商认证。",
            "",
            "## 联网状态",
            "",
        ]
    )
    for status, count in sorted(status_counts.items()):
        lines.append(f"- `{status}`：{count} 条")
    lines.extend(
        [
            "",
            "## 明细",
            "",
            "逐条结果见 `查看材料/源文本核验明细.csv`。`verification_result=revise` 的行必须修正后再安全复跑。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    docs = read_documents()
    url_counts = Counter(row["url"] for row in docs)
    statuses = fetch_statuses(set(url_counts))
    rows = [audit_row(row, url_counts, statuses.get(row["url"], "unreachable")) for row in docs]
    write_outputs(rows)
    errors = sum(row["verification_result"] != "pass" for row in rows)
    print(f"text_source_rows={len(rows)}")
    print(f"text_source_errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
