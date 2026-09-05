"""Fetch vintage-safe macro inputs for the rates research layer.

The script deliberately keeps each observation's statistical period separate
from its first usable public timestamp.  CPI/PPI/PMI and AFRE are downloaded
from public data interfaces, while MLF observations are parsed from the
official PBOC monthly notices and bond issuance is obtained from CNINFO's
public bond tables through AkShare.  Every row retains the endpoint/hash used
to build it so a later run can be audited or replaced by a primary-source file.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
import time
from calendar import monthrange
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import STRUCTURED_FIELDS, STRUCTURED_INDICATORS, validate_structured_row  # noqa: E402


DATA_DIR = ROOT / "data" / "sample"
TARGET_PATH = DATA_DIR / "macro_target_history.csv"
OUT = DATA_DIR / "rates_structured_data.csv"
AUDIT_OUT = DATA_DIR / "rates_structured_data_audit.json"

EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
AFRE_URL = "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery"
AFRE_LANDING_URL = "https://data.mofcom.gov.cn/gnmy/shrzgm.shtml"
PBC_HOST = "https://www.pbc.gov.cn"
MLF_WORK_ROOT = PBC_HOST + "/zhengcehuobisi/125207/125213/125437/125446/125873/"
MLF_WORK_LIST_ID = "17099"
MLF_WORK_PAGES = 8
AFRE_STOCK_PDF_URL = "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/2025110511314347909.pdf"
AFRE_STOCK_RELEASE_TIME = "2025-11-05 23:59:59"


def _get_json(url: str, params: dict[str, str] | None = None, method: str = "GET") -> tuple[Any, bytes]:
    response = requests.request(
        method, url, params=params, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/2.0"}, timeout=90
    )
    response.raise_for_status()
    return response.json(), response.content


def _get_bytes(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.get(
                url, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/2.0"}, timeout=45
            )
            response.raise_for_status()
            return response.content
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}：{last_error}")


def _month(value: str) -> tuple[str, str, str]:
    digits = re.sub(r"[^0-9]", "", str(value))
    if len(digits) < 6:
        raise ValueError(f"无法解析统计月份：{value}")
    year, month = int(digits[:4]), int(digits[4:6])
    last = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}", f"{year:04d}-{month:02d}"


def _conservative_release(period_end: str, target_releases: dict[str, str]) -> str:
    # The industrial-value-added release is later than CPI/PPI/PMI in most
    # months.  Using it as a common upper bound sacrifices timeliness but
    # prevents a vintage leak when a release calendar changes.
    month_key = period_end[:7]
    release = target_releases.get(month_key)
    if release:
        return release + " 23:59:59"
    fallback = date.fromisoformat(period_end) + timedelta(days=20)
    return fallback.isoformat() + " 23:59:59"


def _target_releases() -> dict[str, str]:
    if not TARGET_PATH.exists():
        return {}
    with TARGET_PATH.open(encoding="utf-8", newline="") as handle:
        return {row["period_end"][:7]: row["release_date"] for row in csv.DictReader(handle) if row.get("release_date")}


def _existing_structured_rows(indicator: str) -> list[dict[str, str]]:
    if not OUT.exists():
        return []
    with OUT.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("indicator") == indicator]


def _row(
    *, observation_date: str, release_time: str, period_start: str, period_end: str,
    indicator: str, value: float, unit: str, source_name: str, source_url: str,
    source_sha256: str, vintage: str = "first_publication",
) -> dict[str, str]:
    return {
        "observation_date": observation_date, "release_time": release_time,
        "period_start": period_start, "period_end": period_end,
        "indicator": indicator, "value": f"{float(value):.8f}", "unit": unit,
        "source_name": source_name, "source_url": source_url,
        "source_sha256": source_sha256, "vintage": vintage,
    }


def fetch_nbs_mirrored_series(target_releases: dict[str, str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    definitions = {
        "RPT_ECONOMY_CPI": (
            "cpi_yoy", "NATIONAL_SAME", "居民消费价格同比（%）",
            "国家统计局（数据中心镜像）", "REPORT_DATE,TIME,NATIONAL_SAME",
        ),
        "RPT_ECONOMY_PPI": (
            "ppi_yoy", "BASE_SAME", "工业生产者出厂价格同比（%）",
            "国家统计局（数据中心镜像）", "REPORT_DATE,TIME,BASE_SAME",
        ),
        "RPT_ECONOMY_PMI": (
            "pmi_manufacturing", "MAKE_INDEX", "制造业PMI指数",
            "国家统计局（数据中心镜像）", "REPORT_DATE,TIME,MAKE_INDEX",
        ),
    }
    rows: list[dict[str, str]] = []
    audits: list[dict[str, Any]] = []
    for report_name, (indicator, field, unit, source_name, columns) in definitions.items():
        payload, raw = _get_json(EASTMONEY_URL, {
            "columns": columns, "pageNumber": "1", "pageSize": "2000",
            "sortColumns": "REPORT_DATE", "sortTypes": "-1", "source": "WEB",
            "client": "WEB", "reportName": report_name, "p": "1", "pageNo": "1", "pageNum": "1",
        })
        data = (payload.get("result") or {}).get("data") or []
        source_hash = hashlib.sha256(raw).hexdigest()
        accepted = 0
        for item in data:
            try:
                period_start, period_end, _month_key = _month(str(item["TIME"]))
                value = float(item[field])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(_row(
                observation_date=period_end, release_time=_conservative_release(period_end, target_releases),
                period_start=period_start, period_end=period_end, indicator=indicator, value=value, unit=unit,
                source_name=source_name, source_url=EASTMONEY_URL, source_sha256=source_hash,
                vintage="retrospective_snapshot_conservative_release",
            ))
            accepted += 1
        audits.append({"indicator": indicator, "endpoint": EASTMONEY_URL, "source_sha256": source_hash,
                       "rows_received": len(data), "rows_accepted": accepted,
                       "release_policy": "conservative bound using official industrial release date",
                       "model_policy": "audit_only because the endpoint does not expose historical vintages"})
    return rows, {"nbs_series": audits}


def fetch_afre(target_releases: dict[str, str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    payload, raw = _get_json(AFRE_URL, method="POST")
    source_hash = hashlib.sha256(raw).hexdigest()
    rows: list[dict[str, str]] = []
    field_map = {
        "tiosfs": "afre_flow", "rmblaon": "afre_rmb_loans", "government_bonds": "afre_government_bonds",
    }
    accepted = 0
    for item in payload if isinstance(payload, list) else []:
        try:
            period_start, period_end, _month_key = _month(str(item["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        for source_field, indicator in field_map.items():
            if source_field not in item or item[source_field] in {None, ""}:
                continue
            rows.append(_row(
                observation_date=period_end, release_time=_conservative_release(period_end, target_releases),
                period_start=period_start, period_end=period_end, indicator=indicator,
                value=float(item[source_field]), unit="亿元",
                source_name="商务部数据中心（人民银行统计口径）", source_url=AFRE_LANDING_URL,
                source_sha256=source_hash, vintage="retrospective_snapshot_conservative_release",
            ))
            accepted += 1
    return rows, {"afre": {"endpoint": AFRE_URL, "landing_page": AFRE_LANDING_URL,
                            "source_sha256": source_hash, "rows_received": len(payload) if isinstance(payload, list) else 0,
                            "rows_accepted": accepted,
                            "model_policy": "audit_only because the endpoint does not expose historical vintages"}}


def _mlf_listing_url(page: int) -> str:
    return MLF_WORK_ROOT + ("index.html" if page == 1 else f"{MLF_WORK_LIST_ID}-{page}.html")


def _mlf_detail_entries() -> list[tuple[str, str]]:
    """Collect the official PBOC MLF monthly announcement URLs.

    The PBOC list is paginated oldest-first by static HTML.  We inspect the
    title rather than guessing article IDs, so a changed pagination layout
    cannot silently fabricate a historical observation.
    """
    entries: dict[str, str] = {}
    for page in range(1, MLF_WORK_PAGES + 1):
        body = _get_bytes(_mlf_listing_url(page))
        soup = BeautifulSoup(body, "html.parser")
        for anchor in soup.select("a[href]"):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if not re.search(r"中期借贷便利(?:开展情况|招标公告)$", title):
                continue
            url = urljoin(PBC_HOST, anchor.get("href", ""))
            if url.startswith(PBC_HOST) and url.endswith("/index.html"):
                entries[url] = title
    return sorted(entries.items(), key=lambda item: item[0])


def _mlf_amount(content: str) -> float | None:
    patterns = (
        r"(?:开展|进行|操作|投放|净投放)[^。；\n]{0,45}?(\d+(?:\.\d+)?)\s*亿元[^。；\n]{0,25}?(?:中期借贷便利|MLF)",
        r"(?:中期借贷便利|MLF)[^。；\n]{0,65}?(?:操作共|操作|开展|投放|净投放)\s*(\d+(?:\.\d+)?)\s*亿元",
    )
    values: list[float] = []
    for pattern in patterns:
        values.extend(float(match) for match in re.findall(pattern, content, flags=re.I))
    return values[0] if values else None


def _mlf_rate(content: str) -> float | None:
    # Prefer the one-year operation when a notice contains multiple tenors;
    # this is the comparable policy-rate series used by the model.
    all_rates = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", content)]
    if "利率分别" in content and "1年" in content and all_rates:
        return all_rates[-1]
    one_year = re.findall(
        r"1年(?:期)?[^。；\n]{0,100}?(?:利率(?:为)?[^。；\n]{0,20}?)?(\d+(?:\.\d+)?)\s*%", content
    )
    if one_year:
        return float(one_year[-1])
    uniform = re.findall(
        r"利率[^。；\n]{0,20}?(\d+(?:\.\d+)?)\s*%", content
    )
    return float(uniform[-1]) if uniform else None


def _period_from_month_title(title: str, fallback_day: str) -> tuple[str, str, str]:
    match = re.search(r"(20\d{2})年(\d{1,2})月", title)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        last = monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-{last:02d}", f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"
    quarter = re.search(r"(20\d{2})年([1-4])季度", title)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        start_month, end_month = (number - 1) * 3 + 1, number * 3
        last = monthrange(year, end_month)[1]
        return f"{year:04d}-{end_month:02d}-{last:02d}", f"{year:04d}-{start_month:02d}-01", f"{year:04d}-{end_month:02d}-{last:02d}"
    return fallback_day, fallback_day, fallback_day


def _mlf_detail(entry: tuple[str, str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    url, listing_title = entry
    body = _get_bytes(url)
    source_hash = hashlib.sha256(body).hexdigest()
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else listing_title
    zoom = soup.find(id="zoom")
    content = " ".join(zoom.get_text("。", strip=True).split()) if zoom else ""
    stamp = soup.find(id="shijian")
    publish_time = " ".join(stamp.get_text(" ", strip=True).split()) if stamp else ""
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", publish_time):
        match = re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", body.decode("utf-8", errors="ignore"))
        publish_time = match.group(0) if match else ""
    if not publish_time or len(content) < 20:
        raise ValueError(f"MLF正文或发布时间不完整：{url}")
    day = publish_time[:10]
    observation_date, period_start, period_end = _period_from_month_title(listing_title, day)
    amount = _mlf_amount(content)
    rate = _mlf_rate(content)
    rows: list[dict[str, str]] = []
    for indicator, value, unit in (("mlf_amount", amount, "亿元"), ("mlf_rate", rate, "%")):
        if value is None:
            continue
        rows.append(_row(
            observation_date=observation_date, release_time=publish_time,
            period_start=period_start, period_end=period_end,
            indicator=indicator, value=value, unit=unit, source_name="中国人民银行",
            source_url=url, source_sha256=source_hash, vintage="official_pboc_mlf_monthly",
        ))
    return rows, {"url": url, "title": title, "amount_found": amount is not None, "rate_found": rate is not None}


def fetch_pboc_mlf() -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        entries = [
            entry for entry in _mlf_detail_entries()
            if int(re.search(r"20\d{2}", entry[1]).group(0)) >= 2015
        ]
    except Exception as exc:
        return [], {"mlf_official": {"rows_accepted": 0, "reason": str(exc)[:300]}}
    rows: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_mlf_detail, entry): entry for entry in entries}
        for future in as_completed(futures):
            url, _title = futures[future]
            try:
                loaded, _detail = future.result()
            except Exception as exc:
                failures.append({"url": url, "error": str(exc)[:300]})
                continue
            rows.extend(loaded)
    return rows, {
        "mlf_official": {
            "list_root": MLF_WORK_ROOT, "pages_crawled": MLF_WORK_PAGES,
            "detail_pages": len(entries), "rows_accepted": len(rows),
            "amount_rows": sum(row["indicator"] == "mlf_amount" for row in rows),
            "rate_rows": sum(row["indicator"] == "mlf_rate" for row in rows),
            "failures": failures, "method": "央行中期借贷便利工作信息逐月公告正文",
        }
    }


def parse_pboc_afre_stock_text(text: str) -> list[tuple[str, float]]:
    """Extract the 2017-01--2019-12 government-bond stock column.

    The official one-page PDF prints October as ``2017.1``/``2018.1`` in
    places.  Row order is authoritative; we therefore map the first 36 data
    rows to consecutive months and require the full 11-column table.
    """
    candidates: list[list[float]] = []
    # pdfplumber preserves the table's horizontal layout and avoids the
    # character-by-character extraction produced by pypdf for this PDF.
    # Keep the pypdf text parser above as a deterministic fallback for tests.
    for match in re.finditer(r"^\s*20\d{2}\.\d{1,2}\s+(.+)$", text, flags=re.MULTILINE):
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", match.group(1))
        # The PDF repeats a month header (2019.1, 2019.2, ...) that also
        # matches the date pattern.  Stock rows begin with AFRE values in
        # hundred-millions of yuan (over one million); growth-rate rows begin
        # with small percentages.
        if len(numbers) >= 11 and float(numbers[0]) > 100_000:
            candidates.append([float(number) for number in numbers[:11]])
    if len(candidates) < 36:
        return []
    start = date(2017, 1, 1)
    parsed: list[tuple[str, float]] = []
    for index, values in enumerate(candidates[:36]):
        year = start.year + (start.month - 1 + index) // 12
        month = (start.month - 1 + index) % 12 + 1
        last = monthrange(year, month)[1]
        parsed.append((f"{year:04d}-{month:02d}-{last:02d}", values[7]))
    return parsed


def fetch_pboc_afre_government_bonds() -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        raw = _get_bytes(AFRE_STOCK_PDF_URL)
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            text = "\n".join(
                page.extract_text(x_tolerance=1, y_tolerance=3, layout=True) or ""
                for page in pdf.pages
            )
        parsed = parse_pboc_afre_stock_text(text)
    except Exception as exc:
        return [], {"afre_government_bonds_official": {"rows_accepted": 0, "reason": str(exc)[:300]}}
    source_hash = hashlib.sha256(raw).hexdigest()
    rows = [
        _row(
            observation_date=period_end, release_time=AFRE_STOCK_RELEASE_TIME,
            period_start=period_end[:7] + "-01", period_end=period_end,
            indicator="afre_government_bonds", value=value, unit="亿元",
            source_name="中国人民银行", source_url=AFRE_STOCK_PDF_URL,
            source_sha256=source_hash, vintage="official_pboc_afre_stock_reconstruction_2025",
        )
        for period_end, value in parsed
    ]
    return rows, {
        "afre_government_bonds_official": {
            "source_url": AFRE_STOCK_PDF_URL, "source_sha256": source_hash,
            "release_time": AFRE_STOCK_RELEASE_TIME, "rows_accepted": len(rows),
            "period": {"start": parsed[0][0] if parsed else None, "end": parsed[-1][0] if parsed else None},
            "method": "人民银行社会融资规模存量官方PDF第1表政府债券列",
            "vintage_note": "历史列在2025-11-05官方PDF中追溯发布；release_time按PDF公开时间记录，不回填到早期实时特征",
        }
    }


def fetch_bond_issuance() -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError as exc:
        preserved = _existing_structured_rows("government_bond_issuance")
        return preserved, {"government_bond_issuance": {
            "rows_accepted": len(preserved), "preserved_existing_rows": len(preserved),
            "reason": f"akshare unavailable: {exc}",
        }}
    frames: list[Any] = []
    audit: dict[str, Any] = {}
    failed_years: set[tuple[str, int]] = set()
    for name, function in (("treasury", ak.bond_treasure_issue_cninfo), ("local_government", ak.bond_local_government_issue_cninfo)):
        received = 0
        errors: list[str] = []
        for year in range(2015, date.today().year + 1):
            end = min(date(year, 12, 31), date.today())
            frame = None
            last_error: Exception | None = None
            for attempt in range(4):
                try:
                    frame = function(f"{year}0101", end.strftime("%Y%m%d"))
                    break
                except Exception as exc:  # intermittent CNINFO connection resets
                    last_error = exc
                    if attempt < 3:
                        time.sleep(1.0 * (attempt + 1))
            if frame is None:
                errors.append(f"{year}: {str(last_error)[:120]}")
                failed_years.add((name, year))
                continue
            frames.append((name, frame))
            received += int(len(frame))
        audit[name] = {"rows_received": received, "errors": errors,
                       "source": "CNINFO public bond issue table"}
    issue_values: dict[tuple[str, str, str, str], float] = {}
    for kind, frame in frames:
        for _, item in frame.iterrows():
            start = str(item.get("发行起始日", ""))[:10]
            announced = str(item.get("公告日期", ""))[:10]
            try:
                start_day = date.fromisoformat(start)
                announced_day = date.fromisoformat(announced)
                value = float(item.get("计划发行总量"))
            except (TypeError, ValueError):
                continue
            if start_day < date(2015, 1, 1) or start_day < announced_day:
                continue
            # The same sovereign issue can be listed under different market
            # codes.  The official bond name is stable across those aliases.
            issue_id = str(item.get("债券名称") or item.get("债券简称") or item.get("债券代码"))
            key = (kind, issue_id, announced, start)
            issue_values[key] = max(issue_values.get(key, 0.0), value)
    daily_values: dict[tuple[str, str, str], float] = {}
    for (kind, _issue_id, announced, start), value in issue_values.items():
        key = (kind, announced, start)
        daily_values[key] = daily_values.get(key, 0.0) + value
    rows: list[dict[str, str]] = []
    for (kind, announced, start), value in sorted(daily_values.items()):
        raw = f"{kind}|{announced}|{start}|{value:.8f}".encode("utf-8")
        rows.append(_row(
            observation_date=start, release_time=announced + " 23:59:59", period_start=start,
            period_end=start, indicator="government_bond_issuance", value=value, unit="亿元",
            source_name="巨潮资讯（中国证监会数据中心）", source_url="http://webapi.cninfo.com.cn/",
            source_sha256=hashlib.sha256(raw).hexdigest(), vintage=kind,
        ))
    preserved = [
        row for row in _existing_structured_rows("government_bond_issuance")
        if (row.get("vintage", ""), int(row["observation_date"][:4])) in failed_years
    ]
    rows.extend(preserved)
    audit["government_bond_issuance"] = {
        "unique_issues": len(issue_values), "daily_aggregates": len(rows),
        "preserved_existing_rows": len(preserved),
        "deduplication": "max planned issuance per official bond name/date across market aliases, then sum once per announcement/start date",
        "vintage_field": "planned issuance known at announcement; actual post-auction issuance is deliberately excluded",
        "model_policy": "model_eligible as a dated known-in-advance event; engine sums plans within the next seven calendar days and expires them afterward",
        "source_hash_scope": "SHA-256 of canonical planned-issuance record; AkShare does not expose the raw upstream response",
    }
    return rows, audit


def main() -> None:
    target_releases = _target_releases()
    rows: list[dict[str, str]] = []
    audits: dict[str, Any] = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    for loader in (fetch_nbs_mirrored_series, fetch_afre):
        loaded, audit = loader(target_releases)
        rows.extend(loaded); audits.update(audit)
    for loader in (
        fetch_pboc_mlf,
        fetch_pboc_afre_government_bonds,
        fetch_bond_issuance,
    ):
        loaded, audit = loader()
        rows.extend(loaded); audits.update(audit)
    preserved_indicators: dict[str, int] = {}
    generated_counts = Counter(row["indicator"] for row in rows)
    for indicator in STRUCTURED_INDICATORS:
        if generated_counts[indicator]:
            continue
        preserved = _existing_structured_rows(indicator)
        if preserved:
            rows.extend(preserved)
            preserved_indicators[indicator] = len(preserved)
    audits["preserved_existing_indicators"] = preserved_indicators
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        validate_structured_row(row)
        key = (row["observation_date"], row["release_time"], row["indicator"], row["source_sha256"])
        unique[key] = row
    rows = sorted(unique.values(), key=lambda row: (row["release_time"], row["indicator"], row["observation_date"]))
    if len(rows) < 100:
        raise SystemExit(f"结构化数据仅生成{len(rows)}行，拒绝覆盖既有样本")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STRUCTURED_FIELDS, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    indicator_counts = Counter(row["indicator"] for row in rows)
    model_counts = Counter(
        row["indicator"] for row in rows
        if "reconstruction" not in row.get("vintage", "").lower()
        and "retrospective_snapshot" not in row.get("vintage", "").lower()
    )
    audits.update({"rows": len(rows), "indicators": sorted(indicator_counts),
                   "indicator_counts": dict(sorted(indicator_counts.items())),
                   "model_eligible_indicator_counts": dict(sorted(model_counts.items())),
                   "indicator_status": {
                       name: (
                           "sufficient" if model_counts[name]
                           else "audit_only" if indicator_counts[name]
                           else "missing"
                       )
                       for name in STRUCTURED_INDICATORS
                   },
                   "period": {"start": min(row["observation_date"] for row in rows),
                              "end": max(row["observation_date"] for row in rows)},
                   "vintage_rule": (
                       "release_time is the earliest allowed availability; retrospective snapshots and "
                       "historical reconstructions remain audit-only unless point-in-time vintages are available"
                   )})
    AUDIT_OUT.write_text(json.dumps(audits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} structured observations to {OUT}")


if __name__ == "__main__":
    main()
