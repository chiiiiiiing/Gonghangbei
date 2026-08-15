"""Append the latest frozen V6 baseline and text-alpha positions."""

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

from src.lithium.engine import _read_csv, build_main_continuous  # noqa: E402
from src.lithium.walkforward import mature_baseline_positions  # noqa: E402
from src.lithium.walkforward_v6 import (  # noqa: E402
    V6_COST_BPS,
    V6_TEXT_WEIGHT,
    active_quality_text_score_v6,
    quality_text_events_v6,
)


RESEARCH_DIR = ROOT / "data" / "research"
LEDGER_FILE = RESEARCH_DIR / "lithium_v6_prospective_decisions.csv"
SOURCE_FILE = ROOT / "src" / "lithium" / "walkforward_v6.py"
REPORT_FILE = RESEARCH_DIR / "lithium_v6_walkforward_report.json"
FIELDS = [
    "decision_id", "recorded_at", "strategy_version", "signal_date",
    "selected_contract", "baseline_position", "active_text_score",
    "enhanced_position", "position_delta", "accepted_signal_ids",
    "cost_bps", "execution_rule", "execution_trade_date",
    "candidate_source_sha256", "report_sha256", "market_input_sha256",
    "signal_input_sha256",
]
IMMUTABLE_FIELDS = [field for field in FIELDS if field != "recorded_at"]


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_json(value: Any) -> str:
    return hash_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def serialize(value: Any) -> str:
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def append_decision(path: Path, decision: dict[str, Any]) -> str:
    row = {field: serialize(decision.get(field, "")) for field in FIELDS}
    existing: list[dict[str, str]] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise ValueError("V6 前瞻账本字段发生变化，拒绝写入")
            existing = list(reader)
    matched = [item for item in existing if item["decision_id"] == row["decision_id"]]
    if matched:
        changed = [
            field for field in IMMUTABLE_FIELDS
            if matched[0].get(field, "") != row[field]
        ]
        if changed:
            raise ValueError("已冻结 V6 决策发生变化: " + ", ".join(changed))
        return "already_recorded"
    if any(item["signal_date"] == row["signal_date"] for item in existing):
        raise ValueError(f"signal_date={row['signal_date']} 已存在其他 V6 决策")
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return "recorded"


def build_decision() -> dict[str, Any]:
    contracts = _read_csv("lithium_contract_daily.csv")
    continuous = build_main_continuous(contracts)
    days = [str(row["trade_date"]) for row in continuous]
    latest_index = len(days) - 1
    baseline = mature_baseline_positions(continuous, contracts)
    if latest_index not in baseline:
        raise ValueError("最新交易日缺少 V6 成熟基准仓位")
    historical_signals = read_csv(RESEARCH_DIR / "lithium_v3_signals.csv")
    historical_texts = read_csv(RESEARCH_DIR / "lithium_v3_texts.csv")
    live_path = RESEARCH_DIR / "lithium_v5_live_signals.csv"
    live_signals = read_csv(live_path) if live_path.exists() else []
    signals = [*historical_signals, *live_signals]
    texts = [*historical_texts, *live_signals]
    events, _ = quality_text_events_v6(days, signals, texts)
    text_score = active_quality_text_score_v6(latest_index, events)
    baseline_position = float(baseline[latest_index]["position"])
    enhanced_position = (
        max(-1.0, min(1.0, baseline_position + V6_TEXT_WEIGHT * text_score))
        if baseline_position > 0 and text_score > 0
        else baseline_position
    )
    active_ids = [
        event["doc_id"]
        for event_index in range(max(0, latest_index - 4), latest_index + 1)
        for event in events.get(event_index, [])
    ]
    source_hash = hash_bytes(SOURCE_FILE.read_bytes())
    report_hash = hash_bytes(REPORT_FILE.read_bytes())
    market_rows = [row for row in contracts if row["trade_date"] <= days[latest_index]]
    known_signals = [
        row for row in signals
        if str(row.get("publish_time", ""))[:10] <= days[latest_index]
    ]
    identity = {
        "strategy_version": "lithium-v6-quality-text-alpha-v1",
        "signal_date": days[latest_index],
        "candidate_source_sha256": source_hash,
        "report_sha256": report_hash,
        "market_input_sha256": hash_json(market_rows),
        "signal_input_sha256": hash_json(known_signals),
    }
    return {
        **identity,
        "decision_id": hash_json(identity)[:24],
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selected_contract": continuous[latest_index]["contract"],
        "baseline_position": baseline_position,
        "active_text_score": text_score,
        "enhanced_position": enhanced_position,
        "position_delta": enhanced_position - baseline_position,
        "accepted_signal_ids": active_ids,
        "cost_bps": V6_COST_BPS,
        "execution_rule": "signal_close_then_next_available_open_to_following_open",
        "execution_trade_date": "",
    }


def main() -> None:
    decision = build_decision()
    status = append_decision(LEDGER_FILE, decision)
    print(json.dumps({
        "status": status, "decision": decision
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
