"""Create or verify the immutable pre-prospective lithium research snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
MANIFEST_PATH = SAMPLE_DIR / "lithium_prospective_freeze.json"
PROSPECTIVE_START = "2026-08-14"

FILE_SPECS: dict[str, dict[str, Any]] = {
    "lithium_contract_daily.csv": {"date_field": "trade_date"},
    "lithium_warehouse_receipts.csv": {"date_field": "trade_date"},
    "lithium_texts.csv": {"date_field": "publish_time"},
    "lithium_text_signals.csv": {"date_field": "publish_time"},
    "lithium_rulebook.csv": {"date_field": None},
    "lithium_gfex_fetch_audit.csv": {
        "date_field": "trade_date",
        "fields": [
            "trade_date", "dataset", "api_code", "raw_sha256", "raw_rows",
            "selected_rows", "request_url", "source_page",
        ],
    },
    "lithium_text_fetch_audit.csv": {
        "date_field": "publish_time",
        "fields": [
            "doc_id", "publish_time", "provenance", "fetch_status",
            "original_chars", "selected_chars", "content_sha256", "source_name", "url",
        ],
    },
}

STRATEGY_FREEZE = {
    "version": "lithium-prospective-v2",
    "frozen_at": "2026-08-14",
    "prospective_start": PROSPECTIVE_START,
    "selection_split": "2025-01-01/2025-12-31",
    "mode": "trend_agreement_overlay",
    "text_weight": 1.0,
    "cost_bps": 5,
    "trend_lookback_days": 20,
    "active_text_window_days": 5,
    "bootstrap_block_trading_days": 63,
    "formula": (
        "trend_score; when trend_score * active_text_score > 0 use "
        "clip(trend_score + active_text_score, -1, 1), otherwise keep trend_score"
    ),
}


def _canonical_hash(rows: list[dict[str, str]]) -> str:
    canonical_rows = sorted(
        ({key: row[key] for key in sorted(row)} for row in rows),
        key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    payload = json.dumps(
        canonical_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _frozen_rows(name: str, spec: dict[str, Any]) -> list[dict[str, str]]:
    path = SAMPLE_DIR / name
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date_field = spec.get("date_field")
    if date_field:
        rows = [row for row in rows if row.get(date_field, "") < PROSPECTIVE_START]
    fields = spec.get("fields")
    if fields:
        rows = [{field: row.get(field, "") for field in fields} for row in rows]
    return rows


def build_snapshot() -> dict[str, Any]:
    files: dict[str, Any] = {}
    for name, spec in FILE_SPECS.items():
        rows = _frozen_rows(name, spec)
        date_field = spec.get("date_field")
        dates = [row.get(date_field, "") for row in rows] if date_field else []
        files[name] = {
            "rows": len(rows),
            "max_frozen_date": max((value for value in dates if value), default=""),
            "canonical_sha256": _canonical_hash(rows),
        }
    return {
        "manifest_version": 1,
        "strategy": STRATEGY_FREEZE,
        "frozen_inputs": files,
    }


def create_manifest(force: bool = False) -> dict[str, Any]:
    if MANIFEST_PATH.exists() and not force:
        raise FileExistsError(f"{MANIFEST_PATH} already exists; use --verify instead")
    manifest = {
        **build_snapshot(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"missing freeze manifest: {MANIFEST_PATH}")
    expected = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    actual = build_snapshot()
    mismatches: list[str] = []
    if expected.get("strategy") != actual["strategy"]:
        mismatches.append("frozen strategy definition changed")
    expected_files = expected.get("frozen_inputs", {})
    for name, value in actual["frozen_inputs"].items():
        if expected_files.get(name) != value:
            mismatches.append(
                f"{name} changed: expected={expected_files.get(name)} actual={value}"
            )
    return {
        "verified": not mismatches,
        "manifest": str(MANIFEST_PATH.relative_to(ROOT)),
        "manifest_sha256": hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mismatches": mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.create:
        result = create_manifest(force=args.force)
    else:
        result = verify_manifest()
        if not result["verified"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
