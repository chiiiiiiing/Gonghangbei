"""Fetch multi-year official ChinaBond and ChinaMoney rates data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import MARKET_FIELDS  # noqa: E402


START_YEAR = 2018
CGB_BASE_URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/downYearBzqx"
FDR_BASE_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/FrrHis"
DR_CURRENT_URL = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/prr-md.json"
OUT = ROOT / "data" / "sample" / "rates_market.csv"
AUDIT_OUT = ROOT / "data" / "sample" / "rates_source_audit.json"


def fetch(url: str, method: str = "GET") -> bytes:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/2.0"}, method=method)
            with urlopen(request, timeout=120) as response:
                return response.read()
        except Exception as exc:  # network boundary: retry then fail closed
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}：{last_error}")


def cgb_url(year: int) -> str:
    return CGB_BASE_URL + "?" + urlencode({
        "year": year, "wrjxCBFlag": 0, "zblx": "txy",
        "ycDefId": "2c9081e50a2f9606010a3068cae70001", "locale": "en_US",
    })


def fdr_url(year: int) -> str:
    return FDR_BASE_URL + "?" + urlencode({
        "lang": "CN", "startDate": f"{year}-01-01", "endDate": f"{year}-12-31",
    })


def extract_10y(workbook: bytes) -> dict[str, float]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    result: dict[str, float] = {}
    for row in root.findall(".//x:row", namespace):
        values: dict[str, str] = {}
        for cell in row.findall("x:c", namespace):
            column = "".join(char for char in cell.attrib.get("r", "") if char.isalpha())
            inline = cell.find("x:is/x:t", namespace)
            numeric = cell.find("x:v", namespace)
            values[column] = inline.text if inline is not None else numeric.text if numeric is not None else ""
        if values.get("B") == "10y" and values.get("A") and values.get("D"):
            result[values["A"].replace("/", "-")] = float(values["D"])
    if not result:
        raise ValueError("中债官方文件中未找到10年标准期限")
    return result


def extract_fdr007(payload: bytes) -> dict[str, float]:
    parsed = json.loads(payload)
    return {
        row["lfiProducDate"]: float(row["frValueMap"]["FDR007"])
        for row in parsed.get("records", [])
        if row.get("frValueMap", {}).get("FDR007") not in {None, ""}
    }


def collect(start_year: int, end_year: int) -> tuple[list[dict[str, str]], dict[str, object]]:
    downloaded_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows: list[dict[str, str]] = []
    annual_audit: list[dict[str, object]] = []
    for year in range(start_year, end_year + 1):
        bond_url = cgb_url(year)
        liquidity_url = fdr_url(year)
        cgb_bytes = fetch(bond_url)
        fdr_bytes = fetch(liquidity_url, method="POST")
        cgb = extract_10y(cgb_bytes)
        fdr = extract_fdr007(fdr_bytes)
        cgb_hash = hashlib.sha256(cgb_bytes).hexdigest()
        fdr_hash = hashlib.sha256(fdr_bytes).hexdigest()
        dates = sorted(set(cgb) & set(fdr))
        if len(dates) < (100 if year == end_year else 200):
            raise ValueError(f"{year}年官方数据交集仅{len(dates)}天，拒绝静默生成残缺年度")
        for trade_date in dates:
            rows.append({
                "trade_date": trade_date, "cgb_10y_yield": f"{cgb[trade_date]:.4f}",
                "dr007_proxy": f"{fdr[trade_date]:.4f}", "dr007_proxy_name": "FDR007_FIXING",
                "cgb_source_url": bond_url, "liquidity_source_url": liquidity_url,
                "cgb_source_sha256": cgb_hash, "liquidity_source_sha256": fdr_hash,
                "ingested_at": downloaded_at,
            })
        annual_audit.append({
            "year": year, "china_bond_url": bond_url, "china_bond_sha256": cgb_hash,
            "china_bond_10y_rows": len(cgb), "china_money_url": liquidity_url,
            "china_money_sha256": fdr_hash, "fdr007_rows": len(fdr), "merged_rows": len(dates),
        })
    current_bytes = fetch(DR_CURRENT_URL)
    current = json.loads(current_bytes)
    dr007 = next((row for row in current.get("records", []) if row.get("productCode") == "DR007"), {})
    audit: dict[str, object] = {
        "generated_at": downloaded_at, "period": {"start": rows[0]["trade_date"], "end": rows[-1]["trade_date"]},
        "annual_sources": annual_audit,
        "china_money_current_dr007": {
            "url": DR_CURRENT_URL, "sha256": hashlib.sha256(current_bytes).hexdigest(), "record": dr007,
        },
        "merged_rows": len(rows),
        "warning": "FDR007定盘利率不是原始DR007；公开历史MVP仅将其作为银行间流动性代理。",
    }
    return rows, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=START_YEAR)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    args = parser.parse_args()
    if args.start_year < 2006 or args.end_year < args.start_year:
        raise ValueError("年份范围不合法")
    rows, audit = collect(args.start_year, args.end_year)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MARKET_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    AUDIT_OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} official rows ({rows[0]['trade_date']}..{rows[-1]['trade_date']}) to {OUT}")


if __name__ == "__main__":
    main()
