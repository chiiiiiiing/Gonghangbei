"""Create or verify the frozen V6 quality-text candidate and input prefix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DATE = "2026-08-15"
MANIFEST = ROOT / "data" / "research" / "lithium_v6_candidate_freeze.json"
STATIC_FILES = (
    "src/lithium/walkforward_v6.py",
    "scripts/build_lithium_v6_walkforward.py",
    "scripts/record_lithium_v6_prospective_decision.py",
    "data/research/lithium_v6_walkforward_report.json",
    "data/research/lithium_v6_walkforward_rows.csv",
)
DATASETS = (
    ("data/research/lithium_v3_texts.csv", "publish_time"),
    ("data/research/lithium_v3_signals.csv", "publish_time"),
    ("data/research/lithium_v5_live_signals.csv", "publish_time"),
    ("data/research/lithium_v6_prospective_decisions.csv", "signal_date"),
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def frozen_rows(relative_path: str, date_field: str) -> list[dict[str, str]]:
    with (ROOT / relative_path).open(encoding="utf-8", newline="") as handle:
        rows = [
            row for row in csv.DictReader(handle)
            if row.get(date_field, "")[:10] <= FREEZE_DATE
        ]
    rows.sort(key=lambda row: json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ))
    return rows


def dataset_digest(relative_path: str, date_field: str) -> dict[str, Any]:
    rows = frozen_rows(relative_path, date_field)
    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "date_field": date_field,
        "frozen_rows": len(rows),
        "sha256": sha256_bytes(payload),
    }


def current_manifest() -> dict[str, Any]:
    return {
        "version": "lithium-v6-quality-text-candidate-freeze-v1",
        "freeze_date": FREEZE_DATE,
        "strategy_version": "lithium-v6-quality-text-alpha-v1",
        "historical_evidence_mode": "retrospective_walkforward",
        "strict_evidence_mode": "append_only_pre_trade_decision_ledger",
        "static_files": {
            path: sha256_bytes((ROOT / path).read_bytes())
            for path in STATIC_FILES
        },
        "datasets": {
            path: dataset_digest(path, date_field)
            for path, date_field in DATASETS
        },
    }


def verify_manifest() -> dict[str, Any]:
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = current_manifest()
    mismatches: list[str] = []
    for section in ("static_files", "datasets"):
        expected_section = expected.get(section, {})
        actual_section = actual.get(section, {})
        for path, expected_value in expected_section.items():
            if actual_section.get(path) != expected_value:
                mismatches.append(path)
    for field in (
        "version", "freeze_date", "strategy_version",
        "historical_evidence_mode", "strict_evidence_mode",
    ):
        if expected.get(field) != actual.get(field):
            mismatches.append(field)
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
