"""Append-only ledger for user-submitted V5 prospective text signals."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


FREEZE_DATE = "2026-08-15"
MODEL = "deepseek-v4-flash"
FIELDS = [
    "signal_id", "recorded_at", "doc_id", "publish_time", "source_type",
    "source_name", "source_url", "title", "content", "model",
    "predicate_request_id", "direction_request_id", "direction_score",
    "zero_shot_score", "confidence", "zero_shot_confidence",
    "predicate_consensus", "activated_rules", "evidence_text",
    "zero_shot_evidence_text", "rulebook_sha256", "input_sha256",
]
IMMUTABLE_FIELDS = [field for field in FIELDS if field != "recorded_at"]


def hash_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def serialize(value: Any) -> str:
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def build_signal(document: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    identity = {
        "publish_time": document["publish_time"],
        "source_name": document["source_name"],
        "source_url": document.get("url", ""),
        "title": document["title"],
        "content": document["content"],
        "model": result.get("model", ""),
        "request_id": result.get("request_id", ""),
    }
    digest = hash_json(identity)
    return {
        "signal_id": digest[:24],
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "doc_id": f"V5-LIVE-{digest[:16]}",
        "publish_time": document["publish_time"],
        "source_type": document.get("source_type", "news"),
        "source_name": document["source_name"],
        "source_url": document.get("url", ""),
        "title": document["title"],
        "content": document["content"],
        "model": result.get("model", ""),
        "predicate_request_id": result.get("predicate_request_id", ""),
        "direction_request_id": result.get("request_id", ""),
        "direction_score": result.get("direction_score", 0),
        "zero_shot_score": result.get("zero_shot_score", 0),
        "confidence": result.get("confidence", 0),
        "zero_shot_confidence": result.get("zero_shot_confidence", 0),
        "predicate_consensus": result.get("predicate_consensus", []),
        "activated_rules": result.get("activated_rules", []),
        "evidence_text": result.get("evidence_text", ""),
        "zero_shot_evidence_text": result.get("zero_shot_evidence_text", ""),
        "rulebook_sha256": result.get("rulebook_sha256", ""),
        "input_sha256": hash_json(document),
    }


def append_signal(path: Path, signal: dict[str, Any]) -> str:
    row = {field: serialize(signal.get(field, "")) for field in FIELDS}
    existing: list[dict[str, str]] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise ValueError("V5 实时信号账本字段发生变化，拒绝写入")
            existing = list(reader)
    matched = [item for item in existing if item["signal_id"] == row["signal_id"]]
    if matched:
        changed = [
            field for field in IMMUTABLE_FIELDS
            if matched[0].get(field, "") != row[field]
        ]
        if changed:
            raise ValueError("已冻结 V5 实时信号发生变化: " + ", ".join(changed))
        return "already_recorded"
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return "recorded"


def record_if_eligible(
    path: Path,
    document: dict[str, str],
    result: dict[str, Any],
    requested: bool,
) -> dict[str, Any]:
    if not requested:
        return {"status": "not_requested"}
    if document["publish_time"] < FREEZE_DATE:
        return {"status": "not_eligible_historical_date"}
    if not document.get("url"):
        return {"status": "source_url_required"}
    if result.get("model") != MODEL:
        return {"status": "deepseek_v4_flash_required"}
    signal = build_signal(document, result)
    status = append_signal(path, signal)
    return {
        "status": status,
        "signal_id": signal["signal_id"],
        "quality_rule_active": (
            document.get("source_name") != "广州期货交易所"
            and float(result.get("zero_shot_score", 0) or 0) > 0
            and any(
                row.get("name") == "authoritative_source"
                and row.get("status") == "agreed_true"
                for row in result.get("predicate_consensus", [])
            )
        ),
    }
