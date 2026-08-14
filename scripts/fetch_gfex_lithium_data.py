"""Download auditable GFEX carbonate-lithium daily and warehouse data."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
RAW_DIR = ROOT / "data" / "raw" / "lithium_gfex"
DAILY_ENDPOINT = "http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList"
WAREHOUSE_ENDPOINT = "http://www.gfex.com.cn/u/interfacesWebTdWbillWeeklyQuotes/loadList"
DAILY_PAGE = "https://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml"
WAREHOUSE_PAGE = "https://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml"
FETCH_FIELDS = [
    "trade_date", "dataset", "status", "api_code", "raw_sha256", "raw_rows",
    "selected_rows", "request_url", "source_page", "fetched_at",
]
CONTRACT_FIELDS = [
    "trade_date", "contract", "open", "high", "low", "close", "settlement",
    "volume", "open_interest", "source_name", "source_url",
]
WAREHOUSE_FIELDS = [
    "trade_date", "variety", "warehouse_receipt", "change", "source_name", "source_url",
]
_THREAD_LOCAL = threading.local()


def weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def raw_path(dataset: str, day: date) -> Path:
    return RAW_DIR / dataset / f"{day:%Y%m%d}.json.gz"


def thread_session() -> requests.Session:
    if not hasattr(_THREAD_LOCAL, "session"):
        _THREAD_LOCAL.session = requests.Session()
    return _THREAD_LOCAL.session


def request_json(
    session: requests.Session,
    endpoint: str,
    payload: dict[str, str],
    source_page: str,
    retries: int = 4,
) -> tuple[dict[str, Any], str]:
    last_error = ""
    for attempt in range(retries):
        try:
            response = session.post(
                endpoint,
                data=payload,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "http://www.gfex.com.cn",
                    "Referer": source_page,
                    "User-Agent": "Mozilla/5.0 AlphaLensResearch/1.0",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload_json = response.json()
            if str(payload_json.get("code")) != "0":
                raise ValueError(f"GFEX API code={payload_json.get('code')}: {payload_json.get('msg', '')}")
            canonical = json.dumps(payload_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return payload_json, canonical
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_error or "GFEX request failed")


def load_or_fetch(
    session: requests.Session,
    dataset: str,
    day: date,
    delay: float,
) -> tuple[dict[str, Any], str, str]:
    path = raw_path(dataset, day)
    if path.exists():
        canonical = gzip.decompress(path.read_bytes()).decode("utf-8")
        cached_payload = json.loads(canonical)
        if cached_payload.get("data") or day < date.today():
            return cached_payload, canonical, "cached"
        path.unlink()
    if dataset == "daily":
        endpoint = DAILY_ENDPOINT
        payload = {"trade_date": day.strftime("%Y%m%d"), "trade_type": "0"}
        source_page = DAILY_PAGE
    else:
        endpoint = WAREHOUSE_ENDPOINT
        payload = {"gen_date": day.strftime("%Y%m%d")}
        source_page = WAREHOUSE_PAGE
    payload_json, canonical = request_json(session, endpoint, payload, source_page)
    if payload_json.get("data") or day < date.today():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(canonical.encode("utf-8"), compresslevel=9))
    if delay:
        time.sleep(delay)
    return payload_json, canonical, "fetched"


def parse_daily(day: date, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in data:
        if str(item.get("varietyOrder", "")).lower() != "lc":
            continue
        contract = f"LC{str(item.get('delivMonth', '')).strip()}"
        values = [item.get(field) for field in ("open", "high", "low", "close", "clearPrice")]
        try:
            prices = [float(value) for value in values]
        except (TypeError, ValueError):
            continue
        if len(contract) != 6 or any(value <= 0 for value in prices):
            continue
        rows.append({
            "trade_date": day.isoformat(),
            "contract": contract,
            "open": prices[0],
            "high": prices[1],
            "low": prices[2],
            "close": prices[3],
            "settlement": prices[4],
            "volume": int(float(item.get("volumn") or 0)),
            "open_interest": int(float(item.get("openInterest") or 0)),
            "source_name": "广州期货交易所",
            "source_url": DAILY_PAGE,
        })
    return rows


def parse_warehouse(day: date, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lithium = [item for item in data if str(item.get("varietyOrder", "")).lower() == "lc"]
    subtotal = next((item for item in lithium if "小计" in str(item.get("variety", ""))), None)
    if subtotal is not None:
        quantity = float(subtotal.get("wbillQty") or 0)
        change = float(subtotal.get("diff") or 0)
    else:
        warehouse_rows = [item for item in lithium if str(item.get("whType", "")).isdigit()]
        if not warehouse_rows:
            return []
        quantity = sum(float(item.get("wbillQty") or 0) for item in warehouse_rows)
        change = sum(float(item.get("diff") or 0) for item in warehouse_rows)
    return [{
        "trade_date": day.isoformat(),
        "variety": "碳酸锂",
        "warehouse_receipt": quantity,
        "change": change,
        "source_name": "广州期货交易所",
        "source_url": WAREHOUSE_PAGE,
    }]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def process_day(day: date, delay: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    warehouse: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    session = thread_session()
    for dataset, endpoint, source_page, parser_fn in (
        ("daily", DAILY_ENDPOINT, DAILY_PAGE, parse_daily),
        ("warehouse", WAREHOUSE_ENDPOINT, WAREHOUSE_PAGE, parse_warehouse),
    ):
        fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            payload, canonical, cache_status = load_or_fetch(session, dataset, day, delay)
            raw_rows = payload.get("data") or []
            selected = parser_fn(day, raw_rows)
            if dataset == "daily":
                contracts.extend(selected)
            else:
                warehouse.extend(selected)
            audit.append({
                "trade_date": day.isoformat(), "dataset": dataset,
                "status": cache_status if raw_rows else "empty",
                "api_code": payload.get("code", ""),
                "raw_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "raw_rows": len(raw_rows), "selected_rows": len(selected),
                "request_url": endpoint, "source_page": source_page, "fetched_at": fetched_at,
            })
        except RuntimeError as exc:
            audit.append({
                "trade_date": day.isoformat(), "dataset": dataset, "status": f"error:{exc}",
                "api_code": "", "raw_sha256": "", "raw_rows": 0, "selected_rows": 0,
                "request_url": endpoint, "source_page": source_page, "fetched_at": fetched_at,
            })
    return contracts, warehouse, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-07-21")
    parser.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if start > end:
        raise SystemExit("start must not be after end")

    contracts: list[dict[str, Any]] = []
    warehouse: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    days = list(weekdays(start, end))
    worker_count = max(1, min(args.workers, 4))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = executor.map(lambda day: process_day(day, args.delay), days)
        for index, (day_contracts, day_warehouse, day_audit) in enumerate(results, 1):
            contracts.extend(day_contracts)
            warehouse.extend(day_warehouse)
            audit.extend(day_audit)
            if index % 50 == 0 or index == len(days):
                print(
                    f"{index}/{len(days)} weekdays processed; contracts={len(contracts)}, warehouse={len(warehouse)}",
                    flush=True,
                )

    contracts.sort(key=lambda row: (row["trade_date"], row["contract"]))
    warehouse.sort(key=lambda row: (row["trade_date"], row["variety"]))
    write_csv(SAMPLE_DIR / "lithium_contract_daily.csv", CONTRACT_FIELDS, contracts)
    write_csv(SAMPLE_DIR / "lithium_warehouse_receipts.csv", WAREHOUSE_FIELDS, warehouse)
    write_csv(SAMPLE_DIR / "lithium_gfex_fetch_audit.csv", FETCH_FIELDS, audit)
    failures = sum(str(row["status"]).startswith("error:") for row in audit)
    print(f"GFEX import complete: {len(contracts)} LC contract-days, {len(warehouse)} receipt-days, {failures} request errors")


if __name__ == "__main__":
    main()
