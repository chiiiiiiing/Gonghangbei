"""Repair duplicate signal rows and recover interrupted local-LLM audit state."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_lithium_local_annotations import (  # noqa: E402
    AUDIT_FIELDS,
    PROMPT_VERSION,
    annotation_fingerprint,
    eligible_for_annotation,
)
from src.lithium.engine import (  # noqa: E402
    DISCOVERY_END,
    SIGNAL_FIELDS,
    _load_rulebook,
    _read_csv,
    _write_csv,
    build_main_continuous,
)


PROSPECTIVE_START = "2026-08-14"


def main() -> None:
    texts = _read_csv("lithium_texts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    continuous = build_main_continuous(contracts)
    eligible = {
        row["doc_id"]: row
        for row in texts
        if eligible_for_annotation(row, continuous, contracts)
    }
    frozen_signal_ids = {
        row["doc_id"]
        for row in _read_csv("lithium_text_labels.csv")
        if row.get("publish_time", "") < PROSPECTIVE_START
    }
    signals = {
        row["doc_id"]: row
        for row in _read_csv("lithium_text_signals.csv")
        if row.get("doc_id") in eligible
        and (
            eligible[row["doc_id"]].get("publish_time", "") >= PROSPECTIVE_START
            or row["doc_id"] in frozen_signal_ids
        )
    }
    audit = {
        row["doc_id"]: row
        for row in _read_csv("lithium_local_llm_audit.csv")
        if row.get("doc_id") in eligible
    }
    rulebook = _load_rulebook()
    model_name = "Qwen/Qwen2.5-0.5B-Instruct"
    recovered = 0
    for doc_id, document in eligible.items():
        rules = [] if document["publish_time"] <= DISCOVERY_END.isoformat() else rulebook
        fingerprint = annotation_fingerprint(document, rules, model_name)
        row = audit.get(doc_id)
        if doc_id in signals:
            if row is None:
                row = {
                    "doc_id": doc_id, "publish_time": document["publish_time"],
                    "status": "accepted", "model": signals[doc_id].get("model", model_name),
                    "request_id": signals[doc_id].get("request_id", ""), "error": "",
                    "annotated_at": "",
                }
            row.update({"annotation_input_sha256": fingerprint, "prompt_version": PROMPT_VERSION})
            audit[doc_id] = row
            continue
        if row is None or row.get("status") == "accepted":
            recovered += 1
            row = {
                "doc_id": doc_id, "publish_time": document["publish_time"],
                "status": "recovered_prior_rejection", "model": model_name,
                "request_id": "",
                "error": "未在冻结信号清单中；仅恢复历史拒绝状态，不伪造原始错误",
                "annotated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        row.update({"annotation_input_sha256": fingerprint, "prompt_version": PROMPT_VERSION})
        audit[doc_id] = row

    _write_csv("lithium_text_signals.csv", SIGNAL_FIELDS, signals.values())
    _write_csv("lithium_local_llm_audit.csv", AUDIT_FIELDS, audit.values())
    print(
        f"Annotation state repaired: {len(signals)} unique signals, "
        f"{len(audit)} audit rows, {recovered} recovered rejection records"
    )


if __name__ == "__main__":
    main()
