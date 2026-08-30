"""Fetch official ChinaBond and ChinaMoney public data into an auditable CSV.

ChinaBond supplies the 10-year government-bond curve. ChinaMoney's public
historical endpoint supplies FDR007, which is explicitly stored as a historical
proxy for DR007. The current raw DR007 value remains available from the public
homepage JSON and is recorded in the audit file.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import MARKET_FIELDS  # noqa: E402


YEAR = 2026
CGB_URL = (
    "https://yield.chinabond.com.cn/cbweb-mn/yc/downYearBzqx?"
    + urlencode({"year": YEAR, "wrjxCBFlag": 0, "zblx": "txy", "ycDefId": "2c9081e50a2f9606010a3068cae70001", "locale": "en_US"})
)
FDR_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/FrrHis?lang=CN&startDate=2026-01-01&endDate=2026-12-31"
DR_CURRENT_URL = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/prr-md.json"
OUT = ROOT / "data" / "sample" / "rates_market.csv"
AUDIT_OUT = ROOT / "data" / "sample" / "rates_source_audit.json"


def fetch(url: str, method: str = "GET") -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/1.0"}, method=method)
    with urlopen(request, timeout=60) as response:
        return response.read()


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
        raise ValueError("中债官方文件中未找到10y标准期限")
    return result


def main() -> None:
    downloaded_at = datetime.now().astimezone().isoformat(timespec="seconds")
    cgb_bytes = fetch(CGB_URL)
    fdr_bytes = fetch(FDR_URL, method="POST")
    dr_current_bytes = fetch(DR_CURRENT_URL)
    cgb_hash = hashlib.sha256(cgb_bytes).hexdigest()
    fdr_hash = hashlib.sha256(fdr_bytes).hexdigest()
    cgb = extract_10y(cgb_bytes)
    fdr_payload = json.loads(fdr_bytes)
    fdr = {
        row["lfiProducDate"]: float(row["frValueMap"]["FDR007"])
        for row in fdr_payload.get("records", [])
        if row.get("frValueMap", {}).get("FDR007") not in {None, ""}
    }
    dates = sorted(set(cgb) & set(fdr))
    if len(dates) < 25:
        raise ValueError(f"官方数据交集仅 {len(dates)} 天，不足以构建MVP")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MARKET_FIELDS, lineterminator="\n")
        writer.writeheader()
        for trade_date in dates:
            writer.writerow({
                "trade_date": trade_date, "cgb_10y_yield": f"{cgb[trade_date]:.4f}",
                "dr007_proxy": f"{fdr[trade_date]:.4f}", "dr007_proxy_name": "FDR007_FIXING",
                "cgb_source_url": CGB_URL, "liquidity_source_url": FDR_URL,
                "cgb_source_sha256": cgb_hash, "liquidity_source_sha256": fdr_hash,
                "ingested_at": downloaded_at,
            })
    current = json.loads(dr_current_bytes)
    dr007 = next((row for row in current.get("records", []) if row.get("productCode") == "DR007"), {})
    AUDIT_OUT.write_text(json.dumps({
        "generated_at": downloaded_at,
        "china_bond": {"url": CGB_URL, "sha256": cgb_hash, "rows_10y": len(cgb)},
        "china_money_fdr007": {"url": FDR_URL, "sha256": fdr_hash, "rows": len(fdr), "role": "DR007 historical proxy"},
        "china_money_current_dr007": {"url": DR_CURRENT_URL, "sha256": hashlib.sha256(dr_current_bytes).hexdigest(), "record": dr007},
        "merged_rows": len(dates),
        "warning": "FDR007定盘利率不是原始DR007；本MVP仅将其作为公开历史代理。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(dates)} official rows to {OUT}")


if __name__ == "__main__":
    main()
