"""Generate auditable lithium predicate annotations and freeze a RIFT rulebook.

Usage: configure the existing ALPHALENS/DEEPSEEK environment variables, import
controlled CSV data, then run this script explicitly.  It never runs as part of
the web request path because model output requires review before acceptance.
"""

from __future__ import annotations

import json

from src.ai.gateway import AISettings, OpenAICompatibleGateway
from src.lithium.engine import (
    DISCOVERY_END,
    SIGNAL_FIELDS,
    analyze_document,
    build_lithium_outputs,
    build_main_continuous,
    forward_label,
    induce_rulebook,
    validate_controlled_data,
    _induction_records,
    _read_csv,
    _write_csv,
)


def signal_row(result: dict) -> dict:
    return {
        "doc_id": result["doc_id"],
        "publish_time": result["publish_time"],
        "direction_label": result["direction_label"],
        "direction_score": result["direction_score"],
        "zero_shot_score": result["zero_shot_score"],
        "confidence": result["confidence"],
        "horizon_days": result["horizon_days"],
        "activated_rules": json.dumps(result["activated_rules"], ensure_ascii=False, separators=(",", ":")),
        "predicate_consensus": json.dumps(result["predicate_consensus"], ensure_ascii=False, separators=(",", ":")),
        "evidence_text": result["evidence_text"],
        "inference_mode": result["inference_mode"],
        "model": result["model"],
        "request_id": result["request_id"],
    }


def main() -> None:
    texts = _read_csv("lithium_texts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    warehouse = _read_csv("lithium_warehouse_receipts.csv")
    errors = validate_controlled_data(texts, contracts, warehouse)
    if errors:
        raise SystemExit("受控数据校验失败：\n" + "\n".join(errors))
    settings = AISettings.from_environment()
    if not settings.enabled:
        raise SystemExit("未配置模型。请设置 DEEPSEEK_API_KEY 或现有 ALPHALENS 模型变量。")
    gateway = OpenAICompatibleGateway(settings)
    continuous = build_main_continuous(contracts)
    accepted = sorted(
        (row for row in texts if row["review_status"] == "accepted"),
        key=lambda row: (row["publish_time"], row["doc_id"]),
    )

    discovery_results: list[dict] = []
    context_history: list[dict] = []
    for document in accepted:
        if document["publish_time"][:10] > DISCOVERY_END.isoformat():
            continue
        result = analyze_document(document, gateway, [], context_history)
        discovery_results.append(signal_row(result))
        realized = forward_label(document["publish_time"], continuous, contracts=contracts)
        if realized is not None:
            context_history.append({**document, "direction_label": realized["direction_label"]})

    induction, _labels = _induction_records(accepted, discovery_results, continuous, contracts)
    rulebook = induce_rulebook(induction)
    all_results = list(discovery_results)
    for document in accepted:
        if document["publish_time"][:10] <= DISCOVERY_END.isoformat():
            continue
        result = analyze_document(document, gateway, rulebook, context_history)
        all_results.append(signal_row(result))

    _write_csv("lithium_text_signals.csv", SIGNAL_FIELDS, all_results)
    summary = build_lithium_outputs()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
