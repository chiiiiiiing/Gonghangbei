"""Append the latest pre-trade lithium position to an immutable decision ledger."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lithium.engine import (
    PROSPECTIVE_DECISION_FILE,
    PROSPECTIVE_START,
    _load_signals,
    _read_csv,
    build_lithium_outputs,
    build_main_continuous,
    build_prospective_decision,
)


SAMPLE_DIR = ROOT / "data" / "sample"
LEDGER_PATH = SAMPLE_DIR / PROSPECTIVE_DECISION_FILE
FIELDS = [
    "decision_id", "recorded_at", "strategy_version", "signal_date",
    "selected_contract", "baseline_strategy", "enhanced_strategy",
    "momentum_20d", "validation_std", "active_text_score",
    "baseline_position", "enhanced_position", "position_delta",
    "text_confirmed_trend", "cost_bps", "execution_rule",
    "market_input_sha256", "signal_input_sha256", "freeze_manifest_sha256",
]
IMMUTABLE_FIELDS = [field for field in FIELDS if field != "recorded_at"]


def _canonical_hash(tagged_rows: list[tuple[str, dict[str, str]]]) -> str:
    normalized = [
        {"dataset": dataset, **{key: row.get(key, "") for key in sorted(row)}}
        for dataset, row in tagged_rows
    ]
    normalized.sort(
        key=lambda row: json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def append_decision(path: Path, decision: dict[str, Any]) -> str:
    serialized = {field: _format_value(decision.get(field, "")) for field in FIELDS}
    existing: list[dict[str, str]] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise ValueError("前瞻决策账本字段发生变化，拒绝继续写入")
            existing = list(reader)
    same_day = [row for row in existing if row.get("signal_date") == serialized["signal_date"]]
    if same_day:
        if len(same_day) != 1:
            raise ValueError(f"signal_date={serialized['signal_date']} 存在重复决策")
        changed = [
            field for field in IMMUTABLE_FIELDS
            if same_day[0].get(field, "") != serialized[field]
        ]
        if changed:
            raise ValueError(
                f"signal_date={serialized['signal_date']} 已冻结决策发生变化: "
                + ", ".join(changed)
            )
        return "already_recorded"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(serialized)
    return "recorded"


def build_latest_decision() -> dict[str, Any]:
    contracts = _read_csv("lithium_contract_daily.csv")
    continuous = build_main_continuous(contracts)
    if not continuous:
        raise ValueError("缺少受控主力连续行情")
    signal_date = str(continuous[-1]["trade_date"])
    if signal_date < PROSPECTIVE_START.isoformat():
        raise ValueError(f"最新行情 {signal_date} 早于前瞻起点 {PROSPECTIVE_START}")
    signals = _load_signals()
    decision = build_prospective_decision(signal_date, continuous, signals, contracts)
    market_rows = [
        ("lithium_contract_daily.csv", row)
        for row in contracts if row.get("trade_date", "") <= signal_date
    ]
    signal_rows = [
        ("lithium_text_signals.csv", row)
        for row in _read_csv("lithium_text_signals.csv")
        if row.get("publish_time", "")[:10] <= signal_date
    ]
    signal_rows.extend(
        ("lithium_rulebook.csv", row) for row in _read_csv("lithium_rulebook.csv")
    )
    freeze_path = SAMPLE_DIR / "lithium_prospective_freeze.json"
    decision.update({
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "market_input_sha256": _canonical_hash(market_rows),
        "signal_input_sha256": _canonical_hash(signal_rows),
        "freeze_manifest_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
    })
    identity = "|".join(
        str(decision[key])
        for key in ("strategy_version", "signal_date", "market_input_sha256", "signal_input_sha256")
    )
    decision["decision_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return decision


def main() -> None:
    decision = build_latest_decision()
    status = append_decision(LEDGER_PATH, decision)
    summary = build_lithium_outputs()
    print(json.dumps({
        "status": status,
        "signal_date": decision["signal_date"],
        "decision_id": decision["decision_id"],
        "baseline_position": decision["baseline_position"],
        "enhanced_position": decision["enhanced_position"],
        "position_delta": decision["position_delta"],
        "research": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
