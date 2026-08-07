"""Best-effort source-link fetching and deterministic source/completeness scoring.

Real-time analysis only. 一切抓取都是尽力而为：网络失败、站点反爬、JS 渲染
都不应该阻断分析链路 —— 抓取失败时系统降级为仅按用户摘要分析，并在审计面板
如实标注，同时压低 AI 置信度上限。冻结回放路径不经过这里。
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Any

import requests

# 伪装常规浏览器 UA，降低被反爬拦截的概率（仍可能失败，属预期）。
FETCH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 抽取文本低于该字数视为页面主体未渲染成功（如 JS 动态渲染站点）。
MIN_FETCHED_CHARS = 200

# 演示/重复分析时避免对同一链接反复抓取。
_FETCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FETCH_TTL_SECONDS = 900


class _TextExtractor(HTMLParser):
    """Stdlib HTML → visible text, dropping script/style/nav/header/footer."""

    _SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "iframe"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)


def _strip_html(raw: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw)
    except Exception:
        pass
    text = " ".join(parser._parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _extract_pdf(raw: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def fetch_full_text(url: str, timeout: int = 20) -> dict[str, Any]:
    """尽力抓取链接全文，返回 {status, text, fetched_chars, error, content_type}。

    status: ok / partial（正文过短，疑为 JS 渲染）/ failed / no_url
    """
    url = (url or "").strip()
    if not url:
        return {"status": "no_url", "text": "", "fetched_chars": 0, "error": "", "content_type": ""}
    try:
        response = requests.get(
            url,
            headers={"User-Agent": FETCH_UA},
            timeout=timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 网络问题一律降级，不阻断分析
        return {
            "status": "failed",
            "text": "",
            "fetched_chars": 0,
            "error": f"链接抓取失败：{exc}",
            "content_type": "",
        }
    content_type = (response.headers.get("Content-Type", "") or "").lower()
    is_pdf = "pdf" in content_type or url.lower().endswith(".pdf")
    try:
        if is_pdf:
            text = _extract_pdf(response.content)
        else:
            text = _strip_html(response.text[:4_000_000])
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "text": "",
            "fetched_chars": 0,
            "error": f"链接文本抽取失败：{exc}",
            "content_type": content_type,
        }
    status = "ok" if len(text) >= MIN_FETCHED_CHARS else "partial"
    return {
        "status": status,
        "text": text,
        "fetched_chars": len(text),
        "error": "",
        "content_type": content_type,
    }


def cached_fetch_full_text(url: str, timeout: int = 20) -> dict[str, Any]:
    """带短 TTL 缓存的抓取，演示中多次分析同一链接时不重复请求。"""
    now = time.time()
    entry = _FETCH_CACHE.get(url)
    if entry and now - entry[0] < _FETCH_TTL_SECONDS:
        return entry[1]
    result = fetch_full_text(url, timeout=timeout)
    _FETCH_CACHE[url] = (now, result)
    return result


def _classify_link(url: str) -> str:
    lowered = (url or "").lower()
    if "gov.cn" in lowered:
        return "government"
    if "cninfo.com.cn" in lowered:
        return "cninfo"
    if any(host in lowered for host in ("sse.com.cn", "szse.cn", "szse.com")):
        return "exchange"
    if any(host in lowered for host in ("qq.com", "sina", "eastmoney", "163.com", "sohu", "10jqka")):
        return "media"
    if lowered.startswith(("http://", "https://")):
        return "other"
    return "none"


AUTHORITY_LABELS = {
    "government": "权威（政府部门）",
    "exchange": "权威（交易所）",
    "cninfo": "公告（公司正式披露）",
    "media": "媒体",
    "other": "未知",
    "none": "未知",
}

_FULL_CAPS = {
    "权威（政府部门）": 0.95,
    "权威（交易所）": 0.95,
    "公告（公司正式披露）": 0.90,
    "媒体": 0.85,
    "未知": 0.80,
}


def _confidence_cap(completeness: str, authority_label: str, has_url: bool) -> float:
    """确定性置信度硬上限：AI 置信度不得超过该值，同时作为 AI 校准指引。"""
    if completeness == "full":
        return _FULL_CAPS.get(authority_label, 0.80)
    if completeness == "partial":
        return 0.75
    if not has_url:
        return 0.70  # 用户直接提供正文，无链接可核验
    return 0.60  # 有链接但抓取失败，只能按摘要判断


def assess_source(
    doc: dict[str, str],
    fetch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对实时文档做确定性「来源与完整性评估」，喂给 AI 校准并写入审计。

    四个维度：政策类型(policy_type) / 来源(source_name) / 链接是什么(link_type)
    / 全不全(completeness)。
    """
    url = str(doc.get("url", "")).strip()
    content = str(doc.get("content", "")).strip()
    link_type = _classify_link(url)
    authority = AUTHORITY_LABELS.get(link_type, "未知")
    has_url = bool(url)

    fetch_status = (fetch or {}).get("status", "no_url") if has_url else "no_url"
    fetched_chars = int((fetch or {}).get("fetched_chars", 0))
    if has_url and fetch_status == "ok" and fetched_chars < MIN_FETCHED_CHARS:
        fetch_status = "partial"

    if fetch_status == "ok":
        completeness = "full"
    elif fetch_status == "partial":
        completeness = "partial"
    else:
        completeness = "summary_only"

    completeness_score = {"full": 1.0, "partial": 0.75, "summary_only": 0.5}[completeness]
    cap = _confidence_cap(completeness, authority, has_url)

    if not has_url:
        reason = "未提供正文链接，无法核验全文"
    elif fetch_status == "ok":
        reason = f"已抓取全文（{fetched_chars} 字），来源为{authority}"
    elif fetch_status == "partial":
        reason = f"链接正文过短（{fetched_chars} 字），疑为页面未完整渲染"
    else:
        reason = f"链接抓取失败（{(fetch or {}).get('error', '') or '网络异常'}），仅按摘要判断"

    return {
        "policy_type": str(doc.get("source_type", "")).strip() or "unknown",
        "source_name": str(doc.get("source_name", "")).strip(),
        "url": url,
        "link_type": link_type,
        "authority": authority,
        "fetch_status": fetch_status,
        "completeness": completeness,
        "completeness_score": completeness_score,
        "summary_chars": len(content),
        "fetched_chars": fetched_chars,
        "confidence_cap": cap,
        "reason": reason,
    }
