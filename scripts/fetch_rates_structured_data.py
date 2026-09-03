"""Fetch vintage-safe macro inputs for the rates research layer.

The script deliberately keeps each observation's statistical period separate
from its first usable public timestamp.  CPI/PPI/PMI and AFRE are downloaded
from public data interfaces, while MLF observations are parsed from the
source-hashed PBOC corpus and bond issuance is obtained from CNINFO's public
bond tables through AkShare.  Every row retains the endpoint/hash used to
build it so a later run can be audited or replaced by a primary-source file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import STRUCTURED_FIELDS, STRUCTURED_INDICATORS, validate_structured_row  # noqa: E402


DATA_DIR = ROOT / "data" / "sample"
TEXT_PATH = DATA_DIR / "rates_policy_texts.csv"
TARGET_PATH = DATA_DIR / "macro_target_history.csv"
OUT = DATA_DIR / "rates_structured_data.csv"
AUDIT_OUT = DATA_DIR / "rates_structured_data_audit.json"

EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
AFRE_URL = "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery"
AFRE_LANDING_URL = "https://data.mofcom.gov.cn/gnmy/shrzgm.shtml"


def _get_json(url: str, params: dict[str, str] | None = None, method: str = "GET") -> tuple[Any, bytes]:
    response = requests.request(
        method, url, params=params, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/2.0"}, timeout=90
    )
    response.raise_for_status()
    return response.json(), response.content


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
            ))
            accepted += 1
        audits.append({"indicator": indicator, "endpoint": EASTMONEY_URL, "source_sha256": source_hash,
                       "rows_received": len(data), "rows_accepted": accepted,
                       "release_policy": "conservative bound using official industrial release date"})
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
                source_sha256=source_hash,
            ))
            accepted += 1
    return rows, {"afre": {"endpoint": AFRE_URL, "landing_page": AFRE_LANDING_URL,
                            "source_sha256": source_hash, "rows_received": len(payload) if isinstance(payload, list) else 0,
                            "rows_accepted": accepted}}


def parse_mlf_from_texts() -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not TEXT_PATH.exists():
        return [], {"mlf": {"rows_accepted": 0, "reason": "policy text corpus missing"}}
    with TEXT_PATH.open(encoding="utf-8", newline="") as handle:
        texts = list(csv.DictReader(handle))
    rows: list[dict[str, str]] = []
    amount_pattern = re.compile(r"(?:MLF|中期借贷便利|定向中期借贷便利)[^。；\n]{0,100}?(\d+(?:\.\d+)?)\s*亿元")
    rate_pattern = re.compile(
        r"(?:MLF|中期借贷便利|定向中期借贷便利)[^。；\n]{0,220}?"
        r"(?:操作|中标)?利率(?:为|下调至|降至)?\s*(\d+(?:\.\d+)?)\s*%",
        flags=re.I,
    )
    for text in texts:
        content = f"{text.get('title', '')}。{text.get('content', '')}"
        if not re.search(r"MLF|中期借贷便利", content, flags=re.I):
            continue
        published = str(text.get("publish_time", ""))
        day = published[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        period_end = day
        for indicator, pattern, unit in (("mlf_amount", amount_pattern, "亿元"), ("mlf_rate", rate_pattern, "%")):
            match = pattern.search(content)
            if not match:
                continue
            rows.append(_row(
                observation_date=day, release_time=published, period_start=day, period_end=period_end,
                indicator=indicator, value=float(match.group(1)), unit=unit, source_name="中国人民银行",
                source_url=text["source_url"], source_sha256=text["source_sha256"],
            ))
    return rows, {"mlf": {"source": "source-hashed PBOC policy corpus", "rows_accepted": len(rows)}}


def fetch_bond_issuance() -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError as exc:
        return [], {"government_bond_issuance": {"rows_accepted": 0, "reason": f"akshare unavailable: {exc}"}}
    frames: list[Any] = []
    audit: dict[str, Any] = {}
    for name, function in (("treasury", ak.bond_treasure_issue_cninfo), ("local_government", ak.bond_local_government_issue_cninfo)):
        received = 0
        errors: list[str] = []
        for year in range(2015, date.today().year + 1):
            end = min(date(year, 12, 31), date.today())
            try:
                frame = function(f"{year}0101", end.strftime("%Y%m%d"))
            except Exception as exc:  # optional source: preserve other structured series
                errors.append(f"{year}: {str(exc)[:120]}")
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
    audit["government_bond_issuance"] = {
        "unique_issues": len(issue_values), "daily_aggregates": len(rows),
        "deduplication": "max planned issuance per official bond name/date across market aliases, then sum once per announcement/start date",
        "vintage_field": "planned issuance known at announcement; actual post-auction issuance is deliberately excluded",
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
    for loader in (parse_mlf_from_texts, fetch_bond_issuance):
        loaded, audit = loader()
        rows.extend(loaded); audits.update(audit)
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
    audits.update({"rows": len(rows), "indicators": sorted(indicator_counts),
                   "indicator_counts": dict(sorted(indicator_counts.items())),
                   "indicator_status": {
                       name: "sufficient" if indicator_counts[name] else "missing"
                       for name in STRUCTURED_INDICATORS
                   },
                   "period": {"start": min(row["observation_date"] for row in rows),
                              "end": max(row["observation_date"] for row in rows)},
                   "vintage_rule": "release_time is the earliest allowed availability; no backfilled value is used before it"})
    AUDIT_OUT.write_text(json.dumps(audits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} structured observations to {OUT}")


if __name__ == "__main__":
    main()
