"""Analyze the latest official warehouse text and append a V4 pre-trade decision."""

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

from scripts.build_lithium_v3_research import (  # noqa: E402
    PREDICATE_FILE,
    RULEBOOK_FILE,
    TEXT_FILE,
    analyze_live_v4_document,
    build_records,
    purged_discovery_records,
    read_path,
    rulebook_hash,
)
from src.ai.gateway import AISettings, OpenAICompatibleGateway  # noqa: E402
from src.lithium.engine import (  # noqa: E402
    _read_csv,
    build_main_continuous,
    map_prediction_to_strategy,
)
RESEARCH_DIR = ROOT / "data" / "research"
LEDGER_FILE = RESEARCH_DIR / "lithium_v4_prospective_decisions.csv"
FIELDS = [
    "decision_id", "recorded_at", "strategy_version", "signal_date",
    "source_doc_id", "source_url", "source_text_sha256", "model",
    "predicate_request_id", "direction_request_id", "rulebook_sha256",
    "direction_score", "zero_shot_score", "confidence", "activated_rule_ids",
    "selected_contract", "baseline_strategy", "enhanced_strategy",
    "momentum_20d", "validation_std", "baseline_position",
    "enhanced_position", "position_delta", "execution_trade_date",
    "execution_rule", "cost_bps", "market_input_sha256",
]
IMMUTABLE_FIELDS = [field for field in FIELDS if field != "recorded_at"]


def hash_payload(value: Any) -> str:
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


def append_decision(path: Path, decision: dict[str, Any]) -> str:
    row = {field: serialize(decision.get(field, "")) for field in FIELDS}
    existing: list[dict[str, str]] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise ValueError("V4 前瞻账本字段发生变化，拒绝写入")
            existing = list(reader)
    matched = [item for item in existing if item["decision_id"] == row["decision_id"]]
    if matched:
        changed = [
            field for field in IMMUTABLE_FIELDS
            if matched[0].get(field, "") != row[field]
        ]
        if changed:
            raise ValueError("已冻结 V4 决策发生变化: " + ", ".join(changed))
        return "already_recorded"
    if any(item["signal_date"] == row["signal_date"] for item in existing):
        raise ValueError(f"signal_date={row['signal_date']} 已存在其他 V4 决策")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return "recorded"


def latest_official_document() -> tuple[dict[str, str], list[dict[str, str]]]:
    warehouse = _read_csv("lithium_warehouse_receipts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    continuous = build_main_continuous(contracts)
    if not warehouse or not continuous:
        raise ValueError("缺少广期所仓单或行情")
    signal_date = str(continuous[-1]["trade_date"])
    latest = next(
        (row for row in reversed(warehouse) if row["trade_date"] == signal_date),
        None,
    )
    if latest is None:
        raise ValueError(f"信号日 {signal_date} 缺少仓单日报")
    change = float(latest["change"])
    direction = "增加" if change >= 0 else "减少"
    amount = abs(change)
    amount_text = str(int(amount)) if amount.is_integer() else format(amount, "g")
    total = float(latest["warehouse_receipt"])
    total_text = str(int(total)) if total.is_integer() else format(total, "g")
    document = {
        "doc_id": f"GFEX-WR-LIVE-{signal_date.replace('-', '')}",
        "source_type": "announcement",
        "title": f"广期所碳酸锂仓单日报：仓单{direction}{amount_text}手",
        "content": (
            f"广州期货交易所{signal_date}仓单日报显示，"
            f"碳酸锂仓单总量为{total_text}手，{direction}{amount_text}手。"
        ),
        "publish_time": signal_date,
        "source_name": latest["source_name"],
        "url": latest["source_url"],
    }
    return document, contracts


def discovery_records(contracts: list[dict[str, str]]) -> list[dict[str, Any]]:
    predicates = {row["doc_id"]: row for row in read_path(PREDICATE_FILE)}
    records = build_records(
        read_path(TEXT_FILE), predicates, build_main_continuous(contracts), contracts
    )
    return purged_discovery_records(records)


def load_rulebook() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in read_path(RULEBOOK_FILE):
        rows.append({
            **row,
            "conditions": [
                item.strip() for item in row["conditions"].split(" AND ")
                if item.strip()
            ],
            "score": float(row["score"]),
            "coverage_positive": float(row["coverage_positive"]),
            "coverage_negative": float(row["coverage_negative"]),
            "support_documents": int(row["support_documents"]),
            "support_dates": int(row["support_dates"]),
        })
    return rows


def build_decision() -> dict[str, Any]:
    settings = AISettings.from_environment()
    if not settings.enabled or settings.provider != "deepseek":
        raise ValueError("需要 DEEPSEEK_API_KEY 才能生成 V4 前瞻决策")
    if settings.chat_model != "deepseek-v4-flash":
        raise ValueError("V4 前瞻决策锁定 deepseek-v4-flash")
    document, contracts = latest_official_document()
    continuous = build_main_continuous(contracts)
    rulebook = load_rulebook()
    result = analyze_live_v4_document(
        document, OpenAICompatibleGateway(settings), rulebook,
        discovery_records(contracts),
    )
    mapping = map_prediction_to_strategy(
        document["publish_time"], result["direction_score"], continuous, contracts
    )
    if mapping.get("status") not in {"mapped", "awaiting_next_trading_day"}:
        raise ValueError(f"策略映射失败: {mapping.get('status')}")
    signal_date = mapping["signal_market_date"]
    selected_contract = next(
        row["contract"] for row in continuous if row["trade_date"] == signal_date
    )
    market_rows = [row for row in contracts if row["trade_date"] <= signal_date]
    identity = {
        "strategy_version": "lithium-v4-rift-prospective-v1",
        "signal_date": signal_date,
        "source_doc_id": document["doc_id"],
        "source_text_sha256": hash_payload(document),
        "rulebook_sha256": rulebook_hash(rulebook),
        "market_input_sha256": hash_payload(market_rows),
    }
    return {
        **identity,
        "decision_id": hash_payload(identity)[:24],
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_url": document["url"],
        "model": result["model"],
        "predicate_request_id": result["predicate_request_id"],
        "direction_request_id": result["request_id"],
        "direction_score": result["direction_score"],
        "zero_shot_score": result["zero_shot_score"],
        "confidence": result["confidence"],
        "activated_rule_ids": [row["rule_id"] for row in result["activated_rules"]],
        "selected_contract": selected_contract,
        "baseline_strategy": mapping["baseline_strategy"],
        "enhanced_strategy": mapping["enhanced_strategy"],
        "momentum_20d": mapping["momentum_20d"],
        "validation_std": mapping["validation_std"],
        "baseline_position": mapping["baseline_position"],
        "enhanced_position": mapping["enhanced_position"],
        "position_delta": mapping["position_delta"],
        "execution_trade_date": mapping.get("execution_trade_date", ""),
        "execution_rule": "signal_close_then_next_available_open",
        "cost_bps": 5.0,
    }


def main() -> None:
    decision = build_decision()
    status = append_decision(LEDGER_FILE, decision)
    print(json.dumps({"status": status, "decision": decision}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
