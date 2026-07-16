"""Fetch verifiable source metadata and replace P0 raw document candidates."""

from __future__ import annotations

import argparse
import csv
import html
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable, NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
RAW_PATH = SAMPLE_DIR / "raw_documents.csv"

SOURCE_FIELDS = ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"]
MVP_LOW = "2024-01-01"
MVP_HIGH = "2026-06-30"

OFFICIAL_DOMAINS = ("gov.cn", "miit.gov.cn", "ndrc.gov.cn", "nea.gov.cn", "mof.gov.cn", "mot.gov.cn")
NEWS_DOMAINS = (
    "stcn.com",
    "cs.com.cn",
    "cnstock.com",
    "21jingji.com",
    "cls.cn",
    "xinhuanet.com",
    "people.com.cn",
    "caam.org.cn",
    "cpia.org.cn",
    "bjx.com.cn",
    "sina.com.cn",
    "eastmoney.com",
    "qq.com",
    "zqrb.cn",
    "gov.cn",
    "miit.gov.cn",
    "ndrc.gov.cn",
    "nea.gov.cn",
    "thepaper.cn",
    "stats.gov.cn",
    "mps.gov.cn",
)

POLICY_SEARCH_TERMS = [
    "2024 新能源汽车下乡活动 工业和信息化部",
    "2024 汽车以旧换新补贴实施细则 商务部 财政部",
    "2024 推动大规模设备更新和消费品以旧换新行动方案 中国政府网",
    "2024 节能降碳行动方案 中国政府网 新能源",
    "2024 非化石能源消费 可再生能源消纳 国家能源局",
    "2024 新型储能 并网 调度 国家能源局",
    "2024 新型电力系统 行动方案 国家能源局",
    "2024 可再生能源电力消纳责任权重 国家能源局",
    "2024 智能光伏 试点示范 工业和信息化部",
    "2024 动力电池 回收利用 工业和信息化部",
    "2025 新能源汽车 以旧换新 补贴 商务部",
    "2025 新型储能 发展 国家能源局 通知",
    "2025 能源工作指导意见 国家能源局 新能源",
    "2025 可再生能源 绿色电力 交易 国家发展改革委",
    "2025 充电基础设施 新能源汽车 国家发展改革委",
    "2025 风电 光伏 新能源 消纳 国家能源局",
    "2025 制造业绿色化发展 工业和信息化部 新能源装备",
    "2026 新能源汽车 车辆购置税 减免 目录 工业和信息化部",
    "2026 能源工作指导意见 国家能源局 新能源",
    "2026 新型储能 电力市场 国家能源局",
    "2024 新能源汽车产业标准体系 工业和信息化部",
    "2024 锂离子电池行业规范条件 工业和信息化部",
    "2024 光伏制造行业规范条件 工业和信息化部",
    "2024 绿色低碳先进技术示范 国家发展改革委",
    "2024 配电网高质量发展行动 国家能源局",
    "2025 电力系统调节能力优化专项行动 国家发展改革委",
    "2025 新能源上网电价市场化改革 国家发展改革委",
    "2025 车网互动规模化应用试点 国家发展改革委",
    "2025 工业领域碳达峰碳中和标准体系 工业和信息化部",
    "2026 新能源汽车推广应用安全隐患排查 工业和信息化部",
    "2024 新能源汽车充换电设施补短板 国家发展改革委",
]

NEWS_SEARCH_TERMS = [
    "2024 光伏 产业链 价格 企稳 证券时报",
    "2024 动力电池 装车量 增长 中汽协",
    "2024 新能源汽车 渗透率 中国证券报",
    "2024 储能 招标 规模 上海证券报",
    "2024 海上风电 项目 核准 证券时报",
    "2025 光伏 行业 产能 出清 证券时报",
    "2025 动力电池 装车量 财联社",
    "2025 新能源汽车 出口 中国证券报",
    "2025 新型储能 装机 国家能源局 新闻",
    "2025 风电 招标 海上风电 上海证券报",
    "2025 光伏 协会 行业 自律 证券时报",
    "2025 锂电 材料 价格 证券时报",
    "2025 新能源车 以旧换新 中国证券报",
    "2025 储能 产业链 订单 证券时报",
    "2025 风电 装机 国家能源局 新闻",
    "2026 光伏 组件 价格 证券时报",
    "2026 动力电池 装车量 中汽协",
    "2026 新能源车 渗透率 财联社",
    "2026 储能 招标 大储 证券时报",
    "2026 海上风电 招标 上海证券报",
    "2024 新能源汽车 产销 数据 中国汽车工业协会",
    "2024 光伏 新增装机 国家能源局",
    "2024 风电 新增装机 国家能源局",
    "2024 储能 装机 数据 北极星",
    "2024 锂电池 出口 证券时报",
    "2025 新能源汽车 产销 中国汽车工业协会",
    "2025 光伏 新增装机 国家能源局",
    "2025 动力电池 出口 证券日报",
    "2025 储能 项目 招标 北极星",
    "2026 新能源汽车 产销 中国汽车工业协会",
    "2024 全国电力工业统计数据 新能源 国家能源局",
    "2025 全国电力工业统计数据 新能源 国家能源局",
    "2025 可再生能源发展情况 新闻发布会 国家能源局",
    "2024 新能源汽车出口 数据 证券日报",
    "2025 海上风电 建设 证券时报",
    "2024 锂离子电池行业运行情况 工业和信息化部",
    "2025 锂离子电池行业运行情况 工业和信息化部",
    "2024 光伏制造行业运行情况 工业和信息化部",
    "2025 光伏制造行业运行情况 工业和信息化部",
    "2024 风电光伏发电量 国家统计局",
    "2025 风电光伏发电量 国家统计局",
    "2024 新能源汽车保有量 公安部",
    "2025 新能源汽车保有量 公安部",
    "2024 储能产业 证券日报",
    "2025 光伏行业 证券日报",
]


ANNOUNCEMENT_EVENT_KEYWORDS = {
    "投资建设": 8,
    "项目投产": 8,
    "扩产": 8,
    "新增产能": 8,
    "中标": 7,
    "重大合同": 7,
    "订单": 6,
    "募投项目": 6,
    "对外投资": 5,
    "行政处罚": 8,
    "立案": 8,
    "问询函": 7,
    "关注函": 7,
    "停产": 7,
    "复产": 7,
    "资产减值": 6,
    "业绩预告": 4,
    "年度报告": 2,
    "半年度报告": 2,
    "可持续发展报告": 2,
    "社会责任报告": 2,
    "ESG报告": 2,
    "投资者关系活动记录表": 2,
    "募集说明书": 2,
}


ANNOUNCEMENT_EXCLUDE_KEYWORDS = [
    "跟踪评级",
    "法律意见书",
    "股东大会",
    "权益分派",
    "股份变动",
    "减持",
    "担保",
    "章程",
    "保荐代表人",
    "受托管理",
    "独立董事",
    "监事会",
    "董事会决议",
]


IR_RELEVANCE_KEYWORDS = [
    "电池",
    "储能",
    "光伏",
    "风电",
    "新能源汽车",
    "订单",
    "产能",
    "项目",
    "技术",
    "销量",
    "出口",
    "交付",
    "客户",
    "价格",
]


GENERIC_SITE_SUMMARY_PHRASES = [
    "由人民日报社主管主办",
    "提供全天候7*24小时",
    "财经自媒体平台",
    "一站式金融理财服务",
]


class SearchResult(NamedTuple):
    title: str
    url: str
    snippet: str


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def looks_mojibake(value: str) -> bool:
    return bool(re.search(r"[äåæçèéðÂÃ]|�", value))


def so_results(query: str, *, limit: int = 8) -> list[SearchResult]:
    response = requests.get(
        "https://www.so.com/s",
        params={"q": query},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=(5, 15),
    )
    response.raise_for_status()
    blocks = re.findall(r'<li[^>]+class="[^"]*res-list[^"]*"[^>]*>.*?</li>', response.text, flags=re.S)
    results: list[SearchResult] = []
    for block in blocks:
        match = re.search(r'<a[^>]+data-mdurl="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not match:
            match = re.search(r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</a>', block, flags=re.S)
        if not match:
            continue
        url = html.unescape(match.group(1))
        title = clean_text(match.group(2))
        desc_match = re.search(r'<p[^>]+class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>', block, flags=re.S)
        snippet = clean_text(desc_match.group(1)) if desc_match else ""
        if url.startswith("//"):
            url = "https:" + url
        if title and url.startswith(("http://", "https://")):
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def ddg_results(query: str, *, limit: int = 8) -> list[SearchResult]:
    response = None
    last_error: Exception | None = None
    for url in ["https://html.duckduckgo.com/html/", "https://duckduckgo.com/html/"]:
        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=(5, 15),
                )
                response.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                response = None
                time.sleep(0.4 * (attempt + 1))
        if response is not None:
            break
    if response is None:
        raise RuntimeError(last_error)
    items = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', response.text)
    results: list[SearchResult] = []
    for raw_url, raw_title in items:
        parsed = urlparse(html.unescape(raw_url))
        target = raw_url
        if "duckduckgo.com" in parsed.netloc:
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            target = unquote(uddg)
        if target.startswith("//"):
            target = "https:" + target
        title = clean_text(raw_title)
        if target and title:
            results.append(SearchResult(title=title, url=target, snippet=""))
        if len(results) >= limit:
            break
    return results


def search_results(query: str, *, limit: int = 8) -> list[SearchResult]:
    errors: list[str] = []
    for name, fetcher in [("360 搜索", so_results), ("DuckDuckGo", ddg_results)]:
        try:
            results = fetcher(query, limit=limit)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue
        if results:
            return results
    if errors:
        print(f"[AlphaLens] Search fallback failed for {query}: {' | '.join(errors)}", flush=True)
    return []


def domain_allowed(url: str, domains: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in domains)


def is_search_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.strip("/").lower()
    if not path:
        return False
    broad_paths = {
        "zhengce",
        "xinwen",
        "news",
        "xwzx",
        "zwgk",
        "zfxxgk",
        "article",
        "content",
        "zb",
        "zb/1",
        "zcwj",
        "xxgk",
    }
    if path in broad_paths:
        return False
    if re.search(r"\.(html|htm|shtml|pdf)$", path):
        return True
    if re.search(r"(20\d{2}[-_/]?\d{2}[-_/]?\d{2}|t20\d{6}|art[_/-]|content[_/-]?\d+)", path):
        return True
    segments = [segment for segment in path.split("/") if segment]
    return len(segments) >= 3 and len(path) >= 16 and bool(re.search(r"\d", segments[-1]))


def is_detail_url(url: str, source_type: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if source_type == "announcement":
        return "static.cninfo.com.cn" in host and bool(re.search(r"\.(pdf|doc|docx)$", path))
    if source_type == "ir_qa":
        return "irm.cninfo.com.cn" in host and "questiondetail" in url.lower() and "questionid=" in url.lower()
    return is_search_detail_url(url)


def should_replace(row: dict[str, str]) -> bool:
    if "待人工核验" in row.get("content", ""):
        return True
    source_type = row.get("source_type", "")
    if source_type in {"policy", "announcement", "news", "ir_qa"} and not is_detail_url(row.get("url", ""), source_type):
        return True
    if source_type in {"policy", "news"} and "发布日期按页面或检索结果记录" in row.get("content", ""):
        return True
    if source_type in {"policy", "news"} and "发布日期记录为" in row.get("content", ""):
        return True
    if source_type in {"policy", "news"} and looks_mojibake(row.get("title", "") + row.get("content", "")):
        return True
    return False


def date_from_text(*values: str, fallback: str) -> str:
    joined = " ".join(values)
    patterns = [
        r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, joined)
        if not match:
            continue
        year, month, day = match.groups()
        candidate = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if MVP_LOW <= candidate <= MVP_HIGH:
            return candidate
    return fallback if fallback and MVP_LOW <= fallback <= MVP_HIGH else ""


def fetch_page_metadata(url: str) -> dict[str, str]:
    if not is_search_detail_url(url):
        return {"title": "", "publish_time": "", "summary": ""}
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=(5, 12),
        )
        response.raise_for_status()
    except Exception:
        return {"title": "", "publish_time": "", "summary": ""}

    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "latin-1"}:
        response.encoding = response.apparent_encoding or "utf-8"
    text = response.text[:240_000]
    title = ""
    for pattern in [
        r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']ArticleTitle["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
    ]:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            title = clean_text(match.group(1))
            if looks_mojibake(title):
                title = ""
            break

    date_contexts: list[str] = []
    for pattern in [
        r'<meta[^>]+(?:name|property)=["\'](?:PubDate|publishdate|article:published_time|datePublished|publishDate|date)["\'][^>]+content=["\']([^"\']+)["\']',
        r"(?:发布时间|发布日期|发稿时间|来源时间|更新时间|日期|publishDate|datePublished)[^0-9]{0,80}(20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2})",
        r"(?:发布时间|发布日期|发稿时间|来源时间|更新时间|日期|publishDate|datePublished)[^0-9]{0,80}(20\d{6})",
    ]:
        date_contexts.extend(match.group(1) for match in re.finditer(pattern, text, flags=re.I | re.S))
    publish_time = date_from_text(*date_contexts, fallback="")
    summary = ""
    for pattern in [
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']',
    ]:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            summary = clean_text(match.group(1))
            break
    if any(phrase in summary for phrase in GENERIC_SITE_SUMMARY_PHRASES):
        summary = ""
    if not summary:
        paragraphs = [clean_text(item) for item in re.findall(r"<p[^>]*>(.*?)</p>", text, flags=re.I | re.S)]
        summary = next((item for item in paragraphs if len(item) >= 40 and not looks_mojibake(item)), "")
        summary = re.sub(r"^超大\s+大\s+标准\s+小\s+点赞\s+分享\s*", "", summary)
    if not summary:
        container = re.search(
            r'<(?:div|section)[^>]+class=["\']?(?:TRS_Editor|article_con|article-content|content-detail)',
            text,
            flags=re.I,
        )
        if container:
            candidate = clean_text(text[container.end() : container.end() + 20_000])
            summary = candidate if len(candidate) >= 40 and not looks_mojibake(candidate) else ""
    return {"title": title, "publish_time": publish_time, "summary": summary[:360]}


def source_name_from_url(url: str, fallback: str) -> str:
    host = urlparse(url).netloc.lower()
    if "miit.gov.cn" in host:
        return "工业和信息化部"
    if "ndrc.gov.cn" in host:
        return "国家发展改革委"
    if "nea.gov.cn" in host:
        return "国家能源局"
    if "mof.gov.cn" in host:
        return "财政部"
    if "gov.cn" in host:
        return "中国政府网"
    if "stcn.com" in host:
        return "证券时报"
    if "cs.com.cn" in host:
        return "中国证券报"
    if "cnstock.com" in host:
        return "上海证券报"
    if "21jingji.com" in host:
        return "21 世纪经济报道"
    if "cls.cn" in host:
        return "财联社"
    if "caam.org.cn" in host:
        return "中国汽车工业协会"
    if "cpia.org.cn" in host:
        return "中国光伏行业协会"
    if "bjx.com.cn" in host:
        return "北极星储能网/北极星电力网"
    if "sina.com.cn" in host:
        return "新浪财经"
    if "eastmoney.com" in host:
        return "东方财富网"
    if "qq.com" in host:
        return "腾讯新闻"
    if "zqrb.cn" in host:
        return "证券日报"
    if "stats.gov.cn" in host:
        return "国家统计局"
    if "mps.gov.cn" in host:
        return "公安部"
    return fallback


def source_from_result(result: SearchResult, fallback_date: str, fallback_name: str) -> dict[str, str]:
    metadata = fetch_page_metadata(result.url)
    title = metadata["title"] or result.title
    publish_time = metadata["publish_time"] or date_from_text(
        result.url,
        result.title,
        result.snippet,
        fallback=fallback_date,
    )
    return {
        "title": title,
        "url": result.url,
        "publish_time": publish_time,
        "source_name": source_name_from_url(result.url, fallback_name),
        "summary": metadata["summary"] or result.snippet,
    }


def search_source(term: str, domains: tuple[str, ...], fallback_date: str, fallback_name: str) -> dict[str, str]:
    results = search_results(term)
    for result in results:
        if domain_allowed(result.url, domains) and is_search_detail_url(result.url):
            return source_from_result(result, fallback_date, fallback_name)
    return {
        "title": term,
        "url": "",
        "publish_time": "",
        "source_name": fallback_name,
        "summary": "",
    }


def build_search_document(row: dict[str, str], source: dict[str, str], *, kind: str) -> dict[str, str]:
    title = source["title"][:120] or row["title"]
    source_name = source["source_name"] or row["source_name"]
    publish_time = source["publish_time"]
    summary = clean_text(source.get("summary", ""))
    source_fact = summary[:260] if summary else f"详情页标题显示该来源围绕《{title}》发布信息。"
    if kind == "policy":
        content = (
            f"原文摘要：{source_fact} 来源为{source_name}，首次公开日期核验为{publish_time}。"
            "项目关联：本条样本仅用于 AlphaLens 的政策事件与主营链条相关性研究，不包含收益判断或股价方向判断。"
        )
    else:
        content = (
            f"原文摘要：{source_fact} 来源为{source_name}，首次公开日期核验为{publish_time}。"
            "项目关联：本条样本用于 AlphaLens 的行业事实、关注扩散与主营业务相关性研究，不构成投资建议。"
        )
    return {
        "doc_id": row["doc_id"],
        "source_type": row["source_type"],
        "title": title,
        "content": content,
        "publish_time": publish_time,
        "source_name": source_name,
        "url": source["url"],
    }


def cninfo_org_map() -> dict[str, str]:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.get(
                "https://www.cninfo.com.cn/new/data/szse_stock.json",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"},
                timeout=20,
            )
            response.raise_for_status()
            return {row["code"]: row["orgId"] for row in response.json()["stockList"]}
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"巨潮证券代码映射连续失败: {last_error}")


def announcement_score(title: str) -> int:
    if any(keyword in title for keyword in ANNOUNCEMENT_EXCLUDE_KEYWORDS):
        return -100
    return sum(weight for keyword, weight in ANNOUNCEMENT_EVENT_KEYWORDS.items() if keyword in title)


def extract_pdf_summary(url: str) -> str:
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 20))
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf_file:
            pdf_file.write(response.content)
            pdf_file.flush()
            result = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "2", "-layout", pdf_file.name, "-"],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
    except (OSError, subprocess.SubprocessError, requests.RequestException):
        return ""
    text = clean_text(result.stdout)
    text = re.sub(r"证券代码[:：]?\s*\d{6}.*?公告编号[:：]?\s*[\w-]+", "", text)
    return text[:420]


def fetch_cninfo_announcements(stock_codes: list[str], stock_names: dict[str, str]) -> list[dict[str, str]]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    }
    org_map = cninfo_org_map()
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for stock_code in stock_codes:
        print(f"[AlphaLens] Fetch CNINFO announcements {stock_code} ...", flush=True)
        params = {
            "pageNum": 1,
            "pageSize": 30,
            "column": "sse" if stock_code.startswith("6") else "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{stock_code},{org_map[stock_code]}" if stock_code in org_map else "",
            "searchkey": stock_code if stock_code.startswith("6") else "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": "2024-01-01~2026-06-30",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        candidates: list[tuple[int, dict[str, object]]] = []
        for page_num in range(1, 6):
            params["pageNum"] = page_num
            response = None
            last_error: Exception | None = None
            for attempt in range(4):
                try:
                    response = requests.post(
                        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                        headers=headers,
                        data=params,
                        timeout=20,
                    )
                    response.raise_for_status()
                    break
                except requests.RequestException as exc:
                    response = None
                    last_error = exc
                    time.sleep(1.0 * (attempt + 1))
            if response is None:
                raise RuntimeError(f"巨潮公告接口连续失败 {stock_code}: {last_error}")
            items = response.json().get("announcements") or []
            for item in items:
                announcement_id = str(item.get("announcementId", ""))
                if not announcement_id or announcement_id in seen:
                    continue
                title = clean_text(item.get("announcementTitle", ""))
                if not title or "取消" in title:
                    continue
                score = announcement_score(title)
                if score <= 0:
                    continue
                timestamp = int(item.get("announcementTime", 0)) / 1000
                publish_time = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                if not (MVP_LOW <= publish_time <= MVP_HIGH):
                    continue
                candidates.append((score, item))
            if any(score >= 5 for score, _ in candidates) or not items:
                break
            time.sleep(0.25)
        if candidates:
            _, item = max(
                candidates,
                key=lambda pair: (pair[0], int(pair[1].get("announcementTime", 0))),
            )
            announcement_id = str(item["announcementId"])
            title = clean_text(str(item["announcementTitle"]))
            publish_time = datetime.fromtimestamp(int(item["announcementTime"]) / 1000).strftime("%Y-%m-%d")
            url = "http://static.cninfo.com.cn/" + str(item.get("adjunctUrl", ""))
            rows.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_names.get(stock_code, str(item.get("secName", ""))),
                    "title": title,
                    "publish_time": publish_time,
                    "source_name": "巨潮资讯网",
                    "url": url,
                    "summary": extract_pdf_summary(url),
                }
            )
            seen.add(announcement_id)
        time.sleep(0.35)
    return rows


def fetch_ir_questions(
    stock_codes: list[str], stock_names: dict[str, str], *, target_count: int = 30
) -> list[dict[str, str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://irm.cninfo.com.cn/ircs/search",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for stock_code in stock_codes:
        if len(rows) >= target_count:
            break
        stock_name = stock_names[stock_code]
        print(f"[AlphaLens] Fetch IR questions {stock_code} {stock_name} ...", flush=True)
        stock_count = 0
        for page_num in range(1, 14):
            response = session.post(
                "https://irm.cninfo.com.cn/newircs/index/search",
                params={"keyWord": stock_name, "pageNum": page_num, "pageSize": 20, "_t": int(time.time())},
                timeout=(5, 10),
            )
            response.raise_for_status()
            items = response.json().get("results") or []
            if not items:
                break
            for item in items:
                if item.get("stockCode") != stock_code or str(item.get("contentType")) not in {"1", "11"}:
                    continue
                qid = str(item.get("indexId", ""))
                if not qid or qid in seen:
                    continue
                try:
                    detail = session.get(
                        "https://irm.cninfo.com.cn/newircs/question/getQuestionDetail",
                        params={"questionId": qid, "_t": int(time.time())},
                        timeout=(5, 10),
                    )
                    detail.raise_for_status()
                except Exception:
                    continue
                data = detail.json().get("data") or {}
                reply = clean_text(data.get("replyContent", ""))
                question = clean_text(data.get("questionContent", item.get("mainContent", "")))
                question_ts = int(data.get("questionDate") or item.get("pubDate") or 0) / 1000
                publish_time = datetime.fromtimestamp(question_ts).strftime("%Y-%m-%d")
                if not (MVP_LOW <= publish_time <= MVP_HIGH) or not reply:
                    continue
                rows.append(
                    {
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "question_id": qid,
                        "question": question,
                        "reply": reply,
                        "publish_time": publish_time,
                        "source_name": "深交所互动易",
                        "url": f"https://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={qid}",
                    }
                )
                seen.add(qid)
                stock_count += 1
                if stock_count >= 5 or len(rows) >= target_count:
                    break
            if stock_count >= 5 or len(rows) >= target_count:
                break
            time.sleep(0.05)
        time.sleep(0.08)
    return rows


def stock_name_in_text(text: str, stock_rows: list[dict[str, str]]) -> dict[str, str] | None:
    for stock in sorted(stock_rows, key=lambda item: len(item["stock_name"]), reverse=True):
        if stock["stock_name"] in text:
            return stock
    return None


def announcement_document(row: dict[str, str], source: dict[str, str], stock_sector: str) -> dict[str, str]:
    source_summary = clean_text(source.get("summary", ""))
    source_fact = source_summary[:320] if source_summary else f"公告标题为《{source['title']}》。"
    content = (
        f"原文摘要：{source['stock_name']}于{source['publish_time']}在巨潮资讯网披露《{source['title']}》。{source_fact}"
        f"项目关联：{source['stock_name']}属于新能源股票池的{stock_sector}板块；仅在原文事实明确支持时抽取事件，不包含收益判断或股价方向判断。"
    )
    return {
        "doc_id": row["doc_id"],
        "source_type": row["source_type"],
        "title": source["title"],
        "content": content,
        "publish_time": source["publish_time"],
        "source_name": source["source_name"],
        "url": source["url"],
    }


def ir_document(row: dict[str, str], source: dict[str, str], stock_sector: str) -> dict[str, str]:
    question_summary = source["question"][:80]
    reply_summary = source["reply"][:100]
    title = f"投资者问答：{source['stock_name']}回应“{question_summary[:36]}”"
    content = (
        f"原文摘要：投资者在深交所互动易向{source['stock_name']}提问，问题摘要为“{question_summary}”。"
        f"公司回复摘要为“{reply_summary}”。"
        f"项目关联：{source['stock_name']}属于新能源股票池的{stock_sector}板块。单条问答只作为证据文本，不直接代表提问压力增加；不包含收益判断或股价方向判断。"
    )
    return {
        "doc_id": row["doc_id"],
        "source_type": row["source_type"],
        "title": title,
        "content": content,
        "publish_time": source["publish_time"],
        "source_name": source["source_name"],
        "url": source["url"],
    }


def next_cycle(items: list[dict[str, str]], index: int) -> dict[str, str]:
    if not items:
        raise RuntimeError("no items available")
    return items[index % len(items)]


def deduplicate_sources(items: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        url = item.get("url", "")
        if not url or url in seen or not item.get("publish_time") or not item.get("title"):
            continue
        seen.add(url)
        unique.append(item)
    return unique


def existing_search_sources(
    docs: list[dict[str, str]], source_type: str, domains: tuple[str, ...]
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in docs:
        url = row.get("url", "")
        if row.get("source_type") != source_type or url in seen:
            continue
        if not domain_allowed(url, domains) or not is_search_detail_url(url):
            continue
        metadata = fetch_page_metadata(url)
        publish_time = metadata["publish_time"] or row.get("publish_time", "")
        if not (MVP_LOW <= publish_time <= MVP_HIGH) or not metadata["summary"]:
            continue
        sources.append(
            {
                "title": metadata["title"] or row.get("title", ""),
                "url": url,
                "publish_time": publish_time,
                "source_name": source_name_from_url(url, row.get("source_name", "")),
                "summary": metadata["summary"],
            }
        )
        seen.add(url)
    return sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="获取并核验 AlphaLens 真实文本来源")
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="刷新全部 120 条文本；默认只替换待核验或 URL 不合格的行",
    )
    parser.add_argument(
        "--refresh-current-search-pages",
        action="store_true",
        help="仅按当前 URL 重抓政策/新闻正文元数据，不更换公告和互动问答来源",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    docs = read_csv(RAW_PATH)
    if args.refresh_current_search_pages:
        refreshed: list[dict[str, str]] = []
        counts: Counter[str] = Counter()
        for row in docs:
            if row["source_type"] not in {"policy", "news"}:
                refreshed.append(row)
                continue
            metadata = fetch_page_metadata(row["url"])
            if not metadata["summary"]:
                refreshed.append(row)
                continue
            source = {
                "title": metadata["title"] or row["title"],
                "url": row["url"],
                "publish_time": metadata["publish_time"] or row["publish_time"],
                "source_name": source_name_from_url(row["url"], row["source_name"]),
                "summary": metadata["summary"],
            }
            refreshed.append(build_search_document(row, source, kind=row["source_type"]))
            counts[row["source_type"]] += 1
        write_csv(RAW_PATH, SOURCE_FIELDS, refreshed)
        print("current_search_pages_refreshed=" + ",".join(f"{key}:{counts[key]}" for key in sorted(counts)))
        return 0
    stock_rows = read_csv(SAMPLE_DIR / "stock_pool.csv")
    stock_names = {row["stock_code"]: row["stock_name"] for row in stock_rows}
    stock_sector = {row["stock_code"]: row["industry_sector"] for row in stock_rows}
    stock_codes = [row["stock_code"] for row in stock_rows]

    policy_sources = existing_search_sources(docs, "policy", OFFICIAL_DOMAINS)
    for idx, term in enumerate(POLICY_SEARCH_TERMS):
        print(f"[AlphaLens] Search policy source {idx + 1}/{len(POLICY_SEARCH_TERMS)} ...", flush=True)
        policy_sources.append(
            search_source(term, OFFICIAL_DOMAINS, fallback_date="", fallback_name="政府/部委网站")
        )
    policy_sources = deduplicate_sources(policy_sources)
    policy_sources = [source for source in policy_sources if source.get("summary")]
    print(f"[AlphaLens] Policy source pool ready: {len(policy_sources)}", flush=True)
    news_sources = existing_search_sources(docs, "news", NEWS_DOMAINS)
    for idx, term in enumerate(NEWS_SEARCH_TERMS):
        print(f"[AlphaLens] Search news source {idx + 1}/{len(NEWS_SEARCH_TERMS)} ...", flush=True)
        news_sources.append(search_source(term, NEWS_DOMAINS, fallback_date="", fallback_name="财经媒体/行业网站"))
    news_sources = deduplicate_sources(news_sources)
    news_sources = [source for source in news_sources if source.get("summary")]
    policy_urls = {source["url"] for source in policy_sources}
    news_sources = [source for source in news_sources if source["url"] not in policy_urls]
    print(f"[AlphaLens] News source pool ready: {len(news_sources)}", flush=True)

    announcement_sources = fetch_cninfo_announcements(stock_codes, stock_names)
    sz_stock_codes = [code for code in stock_codes if not code.startswith("6")]
    ir_sources = fetch_ir_questions(sz_stock_codes, stock_names)

    minimum_counts = {
        "policy": 30,
        "announcement": 30,
        "news": 30,
        "ir_qa": 30,
    }
    actual_counts = {
        "policy": len(policy_sources),
        "announcement": len(announcement_sources),
        "news": len(news_sources),
        "ir_qa": len(ir_sources),
    }
    shortages = [
        f"{source_type}={actual_counts[source_type]}/{required}"
        for source_type, required in minimum_counts.items()
        if actual_counts[source_type] < required
    ]
    if args.refresh_all and shortages:
        raise RuntimeError("刷新前来源池不足，未写入 raw_documents.csv: " + "，".join(shortages))

    replacement_counts: Counter[str] = Counter()
    source_index: Counter[str] = Counter()
    updated_docs: list[dict[str, str]] = []

    for row in docs:
        if not args.refresh_all and not should_replace(row):
            updated_docs.append(row)
            continue
        source_type = row["source_type"]
        if source_type == "policy":
            source = next_cycle(policy_sources, source_index[source_type])
            updated_docs.append(build_search_document(row, source, kind="policy"))
        elif source_type == "news":
            source = next_cycle(news_sources, source_index[source_type])
            updated_docs.append(build_search_document(row, source, kind="news"))
        elif source_type == "announcement":
            source = next_cycle(announcement_sources, source_index[source_type])
            sector = stock_sector.get(source["stock_code"], "")
            updated_docs.append(announcement_document(row, source, sector))
        elif source_type == "ir_qa":
            source = next_cycle(ir_sources, source_index[source_type])
            sector = stock_sector.get(source["stock_code"], "")
            updated_docs.append(ir_document(row, source, sector))
        else:
            updated_docs.append(row)
            continue
        source_index[source_type] += 1
        replacement_counts[source_type] += 1

    write_csv(RAW_PATH, SOURCE_FIELDS, updated_docs)
    print(f"Verified text sources written to {RAW_PATH}")
    print("text_replacement_counts=" + ",".join(f"{key}:{replacement_counts[key]}" for key in sorted(replacement_counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
