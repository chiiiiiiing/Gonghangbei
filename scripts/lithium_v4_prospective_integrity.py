"""Create or verify the immutable prefix for the V4 prospective study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DATE = "2026-08-14"
MANIFEST = ROOT / "data" / "research" / "lithium_v4_prospective_freeze.json"
DATASETS = (
    ("data/sample/lithium_contract_daily.csv", "trade_date"),
    ("data/sample/lithium_warehouse_receipts.csv", "trade_date"),
    ("data/research/lithium_v3_texts.csv", "publish_time"),
    ("data/research/lithium_v3_predicates.csv", "publish_time"),
    ("data/research/lithium_v3_directions.csv", "publish_time"),
    ("data/research/lithium_v3_signals.csv", "publish_time"),
    ("data/research/lithium_v3_rulebook.csv", None),
    ("data/research/lithium_v4_prospective_signals.csv", "publish_time"),
    ("data/research/lithium_v4_prospective_decisions.csv", "signal_date"),
)


def frozen_rows(relative_path: str, date_field: str | None) -> list[dict[str, str]]:
    path = ROOT / relative_path
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if date_field:
        rows = [row for row in rows if row.get(date_field, "")[:10] <= FREEZE_DATE]
    rows.sort(
        key=lambda row: json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    return rows


def dataset_digest(relative_path: str, date_field: str | None) -> dict[str, Any]:
    rows = frozen_rows(relative_path, date_field)
    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "date_field": date_field or "",
        "frozen_rows": len(rows),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def current_manifest() -> dict[str, Any]:
    return {
        "version": "lithium-v4-prospective-prefix-v1",
        "freeze_date": FREEZE_DATE,
        "model": "deepseek-v4-flash",
        "strategy_version": "lithium-v4-rift-prospective-v1",
        "execution_rule": "signal_close_then_next_available_open",
        "datasets": {
            path: dataset_digest(path, date_field)
            for path, date_field in DATASETS
        },
    }


def verify_manifest() -> dict[str, Any]:
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = current_manifest()
    mismatches: list[str] = []
    for path, expected_value in expected["datasets"].items():
        actual_value = actual["datasets"].get(path)
        if actual_value != expected_value:
            mismatches.append(path)
    return {"verified": not mismatches, "mismatches": mismatches, "actual": actual}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true")
    action.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.create:
        if MANIFEST.exists():
            raise SystemExit(f"冻结清单已存在，拒绝覆盖: {MANIFEST}")
        payload = current_manifest()
        MANIFEST.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    result = verify_manifest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
