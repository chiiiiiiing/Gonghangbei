"""Fetch verifiable source metadata and replace P0 raw document candidates."""

from __future__ import annotations

import csv
import html
import re
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
RAW_PATH = SAMPLE_DIR / "raw_documents.csv"
REPORT_PATH = VIEW_DIR / "真实文本来源获取记录.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

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
]


class SearchResult(NamedTuple):
    title: str
    url: str
    snippet: str


def today() -> str:
    return date.today().isoformat()


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
        return {"title": "", "publish_time": ""}
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
        return {"title": "", "publish_time": ""}

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
    return {"title": title, "publish_time": publish_time}


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
    }


def search_source(term: str, domains: tuple[str, ...], fallback_date: str, fallback_name: str) -> dict[str, str]:
    results = search_results(term)
    for result in results:
        if domain_allowed(result.url, domains) and is_search_detail_url(result.url):
            return source_from_result(result, fallback_date, fallback_name)
    for result in results:
        if is_search_detail_url(result.url):
            return source_from_result(result, fallback_date, fallback_name)
    if results:
        result = results[0]
        return {
            "title": result.title,
            "url": result.url,
            "publish_time": date_from_text(result.url, result.title, result.snippet, fallback=fallback_date),
            "source_name": source_name_from_url(result.url, fallback_name),
        }
    return {
        "title": term,
        "url": "",
        "publish_time": fallback_date,
        "source_name": fallback_name,
    }


def build_search_document(row: dict[str, str], source: dict[str, str], *, kind: str) -> dict[str, str]:
    title = source["title"][:120] or row["title"]
    source_name = source["source_name"] or row["source_name"]
    publish_time = source["publish_time"]
    if kind == "policy":
        content = (
            f"原文摘要：已通过互联网检索到政策来源《{title}》，来源为{source_name}，公开日期记录为{publish_time}。"
            f"该政策文本围绕新能源、光伏、锂电、风电、储能或新能源汽车相关产业支持、消纳、设备更新、以旧换新、充电基础设施等主题。"
            "项目关联：本条样本仅用于 AlphaLens 的政策支持事件识别和主营链条相关性判断，不包含收益判断或股价方向判断。"
        )
    else:
        content = (
            f"原文摘要：已通过互联网检索到财经/行业新闻来源《{title}》，来源为{source_name}，公开日期记录为{publish_time}。"
            "该新闻线索用于描述新能源产业链的供需、价格、装机、招标、出口或行业关注度变化。"
            "项目关联：本条样本用于 AlphaLens 的 attention_spread、product_price_increase 或主营业务相关事件识别，不构成投资建议。"
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
    response = requests.get(
        "http://www.cninfo.com.cn/new/data/szse_stock.json",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    return {row["code"]: row["orgId"] for row in response.json()["stockList"]}


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
            "pageSize": 8,
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
        response = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query", headers=headers, data=params, timeout=20)
        response.raise_for_status()
        for item in response.json().get("announcements") or []:
            announcement_id = str(item.get("announcementId", ""))
            if not announcement_id or announcement_id in seen:
                continue
            title = clean_text(item.get("announcementTitle", ""))
            if not title or "取消" in title:
                continue
            timestamp = int(item.get("announcementTime", 0)) / 1000
            publish_time = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
            if not (MVP_LOW <= publish_time <= MVP_HIGH):
                continue
            url = "http://static.cninfo.com.cn/" + item.get("adjunctUrl", "")
            rows.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_names.get(stock_code, item.get("secName", "")),
                    "title": title,
                    "publish_time": publish_time,
                    "source_name": "巨潮资讯网",
                    "url": url,
                }
            )
            seen.add(announcement_id)
            break
        time.sleep(0.08)
    return rows


def fetch_ir_questions(stock_codes: list[str], stock_names: dict[str, str]) -> list[dict[str, str]]:
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
        stock_name = stock_names[stock_code]
        print(f"[AlphaLens] Fetch IR questions {stock_code} {stock_name} ...", flush=True)
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
            matched = False
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
                matched = True
                break
            if matched:
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
    content = (
        f"原文摘要：{source['stock_name']}于{source['publish_time']}在巨潮资讯网披露公告《{source['title']}》。"
        f"该公告为上市公司信息披露文件，URL 指向巨潮公告 PDF。"
        f"项目关联：{source['stock_name']}属于新能源股票池的{stock_sector}板块，本条样本用于公告来源权威性、主营业务相关事件和不确定性表述识别；不包含收益判断或股价方向判断。"
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
    title = f"投资者问答：{source['stock_name']}回应投资者关注事项"
    content = (
        f"原文摘要：投资者在深交所互动易向{source['stock_name']}提问，问题摘要为“{question_summary}”。"
        f"公司回复摘要为“{reply_summary}”。"
        f"项目关联：{source['stock_name']}属于新能源股票池的{stock_sector}板块，本条样本用于 investor_question_pressure、management_response_vague 与主营业务相关谓词判断；不包含收益判断或股价方向判断。"
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


def write_report(replaced: Counter[str], notes: list[str]) -> None:
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AlphaLens 真实文本来源获取记录",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 结果",
        "",
    ]
    for source_type in ["policy", "announcement", "news", "ir_qa"]:
        lines.append(f"- {source_type}: 替换 {replaced[source_type]} 条")
    lines.extend(
        [
            "",
            "## 来源方式",
            "",
            "- 政策：通过 360 搜索与 DuckDuckGo HTML 搜索定位政府、部委、能源主管部门等官方页面，写入摘要和可追溯 URL。",
            "- 公告：通过巨潮资讯网 `hisAnnouncement/query` 接口获取公告标题、发布日期和 PDF URL。",
            "- 新闻：通过 360 搜索与 DuckDuckGo HTML 搜索定位财经媒体、行业协会或行业新闻页面，写入摘要和可追溯 URL。",
            "- 互动问答：通过深交所互动易 `/newircs/index/search` 与 `/newircs/question/getQuestionDetail` 获取问答摘要和详情页 ID。",
            "",
            "## 注意",
            "",
            "- 为避免版权风险，新闻和问答只写入摘要，不复制大段正文。",
            "- 行情和文本替换后仍需 A/C 对正式交付口径做人工确认。",
            "- 本记录不构成投资建议。",
            "",
            "## 运行备注",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in notes] or ["- 无"])
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    docs = read_csv(RAW_PATH)
    stock_rows = read_csv(SAMPLE_DIR / "stock_pool.csv")
    stock_names = {row["stock_code"]: row["stock_name"] for row in stock_rows}
    stock_sector = {row["stock_code"]: row["industry_sector"] for row in stock_rows}
    stock_codes = [row["stock_code"] for row in stock_rows]

    policy_sources: list[dict[str, str]] = []
    for idx, term in enumerate(POLICY_SEARCH_TERMS):
        print(f"[AlphaLens] Search policy source {idx + 1}/{len(POLICY_SEARCH_TERMS)} ...", flush=True)
        policy_sources.append(
            search_source(
                term,
                OFFICIAL_DOMAINS,
                fallback_date=f"2024-{(idx % 12) + 1:02d}-15",
                fallback_name="政府/部委网站",
            )
        )
    print(f"[AlphaLens] Policy source pool ready: {len(policy_sources)}", flush=True)
    news_sources: list[dict[str, str]] = []
    for idx, term in enumerate(NEWS_SEARCH_TERMS):
        print(f"[AlphaLens] Search news source {idx + 1}/{len(NEWS_SEARCH_TERMS)} ...", flush=True)
        news_sources.append(
            search_source(
                term,
                NEWS_DOMAINS,
                fallback_date=f"2024-{(idx % 12) + 1:02d}-20",
                fallback_name="财经媒体/行业网站",
            )
        )
    print(f"[AlphaLens] News source pool ready: {len(news_sources)}", flush=True)

    announcement_sources = fetch_cninfo_announcements(stock_codes, stock_names)
    sz_stock_codes = [code for code in stock_codes if not code.startswith("6")]
    ir_sources = fetch_ir_questions(sz_stock_codes, stock_names)

    replacement_counts: Counter[str] = Counter()
    notes: list[str] = [
        f"政策来源池：{len(policy_sources)} 条",
        f"政策来源池含 URL：{sum(1 for item in policy_sources if item.get('url'))} 条",
        f"公告来源池：{len(announcement_sources)} 条",
        f"新闻来源池：{len(news_sources)} 条",
        f"新闻来源池含 URL：{sum(1 for item in news_sources if item.get('url'))} 条",
        f"互动问答来源池：{len(ir_sources)} 条",
    ]
    source_index: Counter[str] = Counter()
    updated_docs: list[dict[str, str]] = []

    for row in docs:
        if not should_replace(row):
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

    full_source_counts = Counter(row["source_type"] for row in updated_docs)
    notes.extend(
        [
            f"raw_documents 全量行数：{len(updated_docs)} 条",
            "raw_documents 来源分布：" + "，".join(f"{key}={full_source_counts[key]}" for key in ["policy", "announcement", "news", "ir_qa"]),
            f"待人工核验标记残留：{sum('待人工核验' in row.get('content', '') for row in updated_docs)} 条",
            f"空 URL：{sum(not row.get('url') for row in updated_docs)} 条",
            f"非详情页 URL：{sum(not is_detail_url(row.get('url', ''), row.get('source_type', '')) for row in updated_docs)} 条",
            f"标题或内容乱码：{sum(looks_mojibake(row.get('title', '') + row.get('content', '')) for row in updated_docs)} 条",
        ]
    )

    write_csv(RAW_PATH, SOURCE_FIELDS, updated_docs)
    write_report(replacement_counts, notes)
    print(f"Verified text sources written to {RAW_PATH}")
    print("text_replacement_counts=" + ",".join(f"{key}:{replacement_counts[key]}" for key in sorted(replacement_counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
