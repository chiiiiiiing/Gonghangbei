"""Build an isolated, rulebook-consistent lithium v3 research candidate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.gateway import AIServiceError, AISettings, OpenAICompatibleGateway  # noqa: E402
from src.lithium.engine import (  # noqa: E402
    DISCOVERY_END,
    PREDICATE_DEFINITIONS,
    PROSPECTIVE_STRATEGY,
    RULE_FIELDS,
    _induction_records,
    _metrics,
    _read_csv,
    _strategy_rows,
    activated_rules,
    block_bootstrap_increment,
    build_main_continuous,
    deterministic_predicates,
    forward_label,
    induce_rulebook,
    predicate_consensus,
)


RESEARCH_DIR = ROOT / "data" / "research"
CNINFO_FILE = RESEARCH_DIR / "lithium_v3_cninfo_sources.csv"
TEXT_FILE = RESEARCH_DIR / "lithium_v3_texts.csv"
PREDICATE_FILE = RESEARCH_DIR / "lithium_v3_predicates.csv"
AUDIT_FILE = RESEARCH_DIR / "lithium_v3_annotation_audit.csv"
RULEBOOK_FILE = RESEARCH_DIR / "lithium_v3_rulebook.csv"
SIGNAL_FILE = RESEARCH_DIR / "lithium_v3_signals.csv"
REPORT_FILE = RESEARCH_DIR / "lithium_v3_report.json"
PROMPT_VERSION = "lithium-v3-deepseek-v4-flash-predicate-v6-conservative"
TEXT_FIELDS = [
    "doc_id", "source_type", "title", "content", "publish_time",
    "source_name", "url", "review_status",
]
PREDICATE_FIELDS = [
    "doc_id", "publish_time", "predicate_consensus", "model", "request_id",
    "annotation_input_sha256", "prompt_version",
]
AUDIT_FIELDS = [
    "doc_id", "publish_time", "status", "error", "annotation_input_sha256",
    "prompt_version", "annotated_at",
]
SIGNAL_FIELDS = [
    "doc_id", "publish_time", "direction_label", "direction_score",
    "confidence", "horizon_days", "activated_rules", "predicate_consensus",
    "inference_mode", "rulebook_sha256",
]
META_PREDICATES = {"authoritative_source", "quantitative_evidence", "uncertainty_high"}
ECONOMIC_PREDICATES = set(PREDICATE_DEFINITIONS) - META_PREDICATES
EXCERPT_TERMS = (
    "碳酸锂", "氢氧化锂", "锂盐", "锂矿", "锂辉石", "盐湖", "锂云母",
    "投产", "停产", "复产", "减产", "扩产", "产能", "产量", "销量",
    "新能源汽车", "动力电池", "储能", "库存", "价格", "成本", "交割",
)


def read_path(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_path(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def annotation_hash(document: dict[str, str], model_name: str) -> str:
    payload = json.dumps({
        "prompt_version": PROMPT_VERSION,
        "model": model_name,
        "title": document.get("title", ""),
        "content": document.get("content", ""),
        "publish_time": document.get("publish_time", ""),
        "source_name": document.get("source_name", ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def relevant_excerpt(document: dict[str, str], max_chars: int = 4000) -> str:
    title = document.get("title", "").strip()
    content = document.get("content", "").strip()
    segments = [part.strip() for part in re.split(r"(?<=[。！？；])", content) if part.strip()]
    selected: list[str] = []
    for segment in segments[:2]:
        if segment not in selected:
            selected.append(segment)
    for segment in segments:
        if any(term in segment for term in EXCERPT_TERMS) and segment not in selected:
            selected.append(segment)
    excerpt = f"来源：{document.get('source_name', '')}\n{title}\n" + "\n".join(selected)
    return excerpt[:max_chars]


def v3_deterministic_predicates(document: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Add source-specific deterministic candidates before LLM verification."""
    deterministic = deterministic_predicates(document)
    title = document.get("title", "")
    text = f"{title}\n{document.get('content', '')}"
    source_name = document.get("source_name", "")
    quantity_match = re.search(
        r"\d+(?:[,.]\d+)*(?:%|万吨|吨|亿元|万辆|GWh|GW|手|台|个)",
        text,
        re.IGNORECASE,
    )
    if quantity_match:
        deterministic["quantitative_evidence"] = {
            "value": True,
            "evidence_text": quantity_match.group(0),
            "confidence": 0.70,
        }
    if document.get("source_type") == "official_company_announcement" or "巨潮资讯" in source_name:
        deterministic["authoritative_source"] = {
            "value": True,
            "evidence_text": source_name,
            "confidence": 0.70,
        }
    if "产销快报" in title and "新能源汽车" in text:
        deterministic["demand_ev_positive"] = {
            "value": True,
            "evidence_text": "产销快报",
            "confidence": 0.70,
        }
    if "储能" in text and any(term in text for term in ("投产", "新增产能", "产能释放", "装机增长")):
        deterministic["demand_storage_positive"] = {
            "value": True,
            "evidence_text": "储能",
            "confidence": 0.70,
        }
    return deterministic


def combine_texts() -> list[dict[str, str]]:
    existing = _read_csv("lithium_texts.csv")
    new_rows = read_path(CNINFO_FILE)
    by_source_key: dict[str, dict[str, str]] = {}
    for row in [*existing, *new_rows]:
        normalized = row.get("url", "").rstrip("/").lower()
        source_key = (
            f"doc:{row.get('doc_id', '')}"
            if row.get("doc_id", "").startswith("GFEX-WR-")
            else f"url:{normalized}"
        )
        if not normalized or source_key in by_source_key:
            continue
        by_source_key[source_key] = {field: row.get(field, "") for field in TEXT_FIELDS}
    rows = sorted(by_source_key.values(), key=lambda row: (row["publish_time"], row["doc_id"]))
    write_path(TEXT_FILE, TEXT_FIELDS, rows)
    return rows


def ai_predicates_from_consensus(consensus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": row["name"],
            "value": bool(row.get("ai_value")),
            "confidence": float(row.get("confidence", 0) or 0),
            "evidence_text": row.get("evidence_text", "") if row.get("ai_value") else "",
        }
        for row in consensus
    ]


def v3_predicate_consensus(
    document: dict[str, str],
    stored_consensus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deterministic = v3_deterministic_predicates(document)
    text = (
        f"来源：{document.get('source_name', '')}\n"
        f"{document.get('title', '')}\n{document.get('content', '')}"
    )
    return predicate_consensus(
        deterministic,
        ai_predicates_from_consensus(stored_consensus),
        text,
    )


def predicate_schema() -> dict[str, Any]:
    predicate_value = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_text": {"type": "string"},
        },
        "required": ["value", "confidence", "evidence_text"],
    }


def recover_exact_evidence(evidence: str, source_text: str) -> str:
    """Map whitespace-normalized model quotes back to an exact source span."""
    if evidence in source_text:
        return evidence
    normalized_source: list[str] = []
    source_indexes: list[int] = []
    for index, character in enumerate(source_text):
        if character.isspace():
            continue
        normalized_source.append(character)
        source_indexes.append(index)
    normalized_evidence = "".join(character for character in evidence if not character.isspace())
    if not normalized_evidence:
        return ""
    offset = "".join(normalized_source).find(normalized_evidence)
    if offset < 0:
        return ""
    start = source_indexes[offset]
    end = source_indexes[offset + len(normalized_evidence) - 1] + 1
    return source_text[start:end]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: predicate_value for name in PREDICATE_DEFINITIONS},
        "required": list(PREDICATE_DEFINITIONS),
    }


def extract_predicates_only(
    document: dict[str, str],
    gateway: OpenAICompatibleGateway,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_text = relevant_excerpt(document)
    payload = {
        "task": "仅从文本中抽取碳酸锂产业谓词，不预测涨跌，不使用文本发布后信息。",
        "predicate_schema": PREDICATE_DEFINITIONS,
        "document": source_text,
        "constraints": [
            "17个谓词都是 JSON 的必填字段，不得省略。",
            "value=true 时 evidence_text 必须是 document 中的连续原文，不得改写；value=false 时证据留空。",
            "只标记文本明确陈述的事实；计划、预计和尚待审批需同时考虑 uncertainty_high。",
            "不得把普通价格数字误标为产能、产量、销量或库存事件。",
        ],
    }
    raw, metadata = gateway.chat_json(
        [
            {"role": "system", "content": "你是审慎的碳酸锂产业谓词标注员，只输出符合 Schema 的 JSON。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        predicate_schema(),
        "lithium_v3_predicates",
    )
    full_source_text = (
        f"来源：{document.get('source_name', '')}\n"
        f"{document.get('title', '')}\n{document.get('content', '')}"
    )
    deterministic = v3_deterministic_predicates(document)
    ai_predicates: list[dict[str, Any]] = []
    normalization_notes: list[str] = []
    for name in PREDICATE_DEFINITIONS:
        value = raw.get(name)
        if value is False:
            value = {"value": False, "confidence": 0.8, "evidence_text": ""}
        elif value is True:
            if deterministic[name].get("value"):
                value = {
                    "value": True,
                    "confidence": 0.8,
                    "evidence_text": deterministic[name]["evidence_text"],
                }
                normalization_notes.append(f"{name}:true_shorthand_anchored")
            else:
                value = {"value": False, "confidence": 0.5, "evidence_text": ""}
                normalization_notes.append(f"{name}:unverifiable_true_demoted")
        if not isinstance(value, dict):
            raise ValueError(f"谓词 {name} 必须返回 object")
        if value.get("value") is True:
            evidence = str(value.get("evidence_text", "")).strip()
            recovered = recover_exact_evidence(evidence, full_source_text)
            if not recovered:
                if deterministic[name].get("value"):
                    recovered = str(deterministic[name]["evidence_text"])
                    normalization_notes.append(f"{name}:quote_replaced_by_rule_anchor")
                else:
                    value = {"value": False, "confidence": 0.5, "evidence_text": ""}
                    normalization_notes.append(f"{name}:unverifiable_quote_demoted")
            if recovered:
                value = {**value, "evidence_text": recovered}
        ai_predicates.append({"name": name, **value})
    # Validate the model output independently before deterministic/LLM agreement.
    predicate_consensus(
        {name: {"value": False, "evidence_text": "", "confidence": 0.65} for name in PREDICATE_DEFINITIONS},
        ai_predicates,
        full_source_text,
    )
    consensus = predicate_consensus(
        deterministic, ai_predicates, full_source_text
    )
    return consensus, {**metadata, "normalization_notes": normalization_notes}


def annotate_missing(
    texts: list[dict[str, str]],
    model_name: str,
    limit: int,
    workers: int,
) -> dict[str, dict[str, Any]]:
    predicates = {
        row["doc_id"]: row for row in read_path(PREDICATE_FILE)
        if row.get("prompt_version") == PROMPT_VERSION and row.get("model") == model_name
    }
    audit = {
        row["doc_id"]: row for row in read_path(AUDIT_FILE)
        if row.get("prompt_version") == PROMPT_VERSION
    }
    missing = [
        row for row in texts
        if row.get("review_status") == "accepted" and row["doc_id"] not in predicates
    ]
    if limit:
        missing = missing[:limit]
    settings = AISettings.from_environment()
    if not settings.enabled or settings.provider != "deepseek":
        raise SystemExit("未配置 DeepSeek API，请设置 DEEPSEEK_API_KEY")
    if settings.chat_model != model_name or model_name != "deepseek-v4-flash":
        raise SystemExit("本研究批次锁定模型 deepseek-v4-flash")
    gateway = OpenAICompatibleGateway(settings)

    def annotate(document: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
        consensus, metadata = extract_predicates_only(document, gateway)
        return document, consensus, metadata

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(annotate, document): document for document in missing}
        for future in as_completed(futures):
            document = futures[future]
            completed += 1
            fingerprint = annotation_hash(document, model_name)
            try:
                _, consensus, metadata = future.result()
                predicates[document["doc_id"]] = {
                    "doc_id": document["doc_id"],
                    "publish_time": document["publish_time"],
                    "predicate_consensus": json.dumps(
                        consensus, ensure_ascii=False, separators=(",", ":")
                    ),
                    "model": str(metadata.get("model", model_name)),
                    "request_id": str(metadata.get("request_id", "")),
                    "annotation_input_sha256": fingerprint,
                    "prompt_version": PROMPT_VERSION,
                }
                audit[document["doc_id"]] = {
                    "doc_id": document["doc_id"],
                    "publish_time": document["publish_time"],
                    "status": "accepted",
                    "error": ";".join(metadata.get("normalization_notes", [])),
                    "annotation_input_sha256": fingerprint,
                    "prompt_version": PROMPT_VERSION,
                    "annotated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            except (AIServiceError, RuntimeError, ValueError) as exc:
                audit[document["doc_id"]] = {
                    "doc_id": document["doc_id"],
                    "publish_time": document["publish_time"],
                    "status": "rejected",
                    "error": str(exc),
                    "annotation_input_sha256": fingerprint,
                    "prompt_version": PROMPT_VERSION,
                    "annotated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            write_path(PREDICATE_FILE, PREDICATE_FIELDS, sorted(predicates.values(), key=lambda row: (row["publish_time"], row["doc_id"])))
            write_path(AUDIT_FILE, AUDIT_FIELDS, sorted(audit.values(), key=lambda row: (row["publish_time"], row["doc_id"])))
            if completed % 10 == 0 or completed == len(missing):
                print(f"v3 DeepSeek predicates {completed}/{len(missing)}", flush=True)
    return predicates


def rulebook_hash(rulebook: list[dict[str, Any]]) -> str:
    payload = json.dumps(rulebook, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rule_score(active: list[dict[str, Any]]) -> float:
    bullish = max((float(rule["score"]) for rule in active if rule["target_label"] == "bullish"), default=0.0)
    bearish = max((float(rule["score"]) for rule in active if rule["target_label"] == "bearish"), default=0.0)
    denominator = bullish + bearish
    return (bullish - bearish) / denominator if denominator else 0.0


def build_records(
    texts: list[dict[str, str]],
    predicates: dict[str, dict[str, Any]],
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for document in texts:
        predicate = predicates.get(document["doc_id"])
        if predicate is None:
            continue
        label = forward_label(document["publish_time"], continuous, contracts=contracts)
        if label is None:
            continue
        consensus = v3_predicate_consensus(
            document,
            json.loads(predicate["predicate_consensus"]),
        )
        records.append({
            **document,
            "direction_label": label["direction_label"],
            "forward_open_return": label["forward_open_return"],
            "predicate_status": {row["name"]: row["status"] for row in consensus},
            "predicate_consensus": consensus,
        })
    return records


def temporal_rule_diagnostics(
    records: list[dict[str, Any]],
    rulebook: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    periods = {
        "2023H2": ("2023-07-21", "2023-12-31"),
        "2024H1": ("2024-01-01", "2024-06-30"),
        "2024H2": ("2024-07-01", "2024-12-31"),
    }
    output: list[dict[str, Any]] = []
    for rule in rulebook:
        row: dict[str, Any] = {"rule_id": rule["rule_id"]}
        for name, (start, end) in periods.items():
            selected = [record for record in records if start <= record["publish_time"][:10] <= end]
            target = rule["target_label"]
            positives = [record for record in selected if record["direction_label"] == target]
            negatives = [record for record in selected if record["direction_label"] != target]
            matches = lambda record: all(
                record["predicate_status"].get(condition) == "agreed_true"
                for condition in rule["conditions"]
            )
            positive_coverage = sum(map(matches, positives)) / len(positives) if positives else 0.0
            negative_coverage = sum(map(matches, negatives)) / len(negatives) if negatives else 0.0
            row[f"{name}_score"] = positive_coverage - negative_coverage
            row[f"{name}_support"] = sum(map(matches, selected))
        output.append(row)
    return output


def stable_discovery_rulebook(
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the predeclared 2024 half-year stability gate and deduplicate coverage."""
    diagnostics = temporal_rule_diagnostics(records, candidates)
    by_id = {row["rule_id"]: row for row in diagnostics}
    stable = [
        rule for rule in candidates
        if by_id[rule["rule_id"]]["2024H1_score"] > 0
        and by_id[rule["rule_id"]]["2024H2_score"] > 0
        and by_id[rule["rule_id"]]["2024H1_support"] >= 3
        and by_id[rule["rule_id"]]["2024H2_support"] >= 3
    ]
    discovery = [record for record in records if record["publish_time"][:10] <= DISCOVERY_END.isoformat()]
    deduplicated: list[dict[str, Any]] = []
    seen_coverage: set[tuple[str, tuple[str, ...]]] = set()
    for rule in sorted(stable, key=lambda row: (len(row["conditions"]), -float(row["score"]))):
        covered_docs = tuple(sorted(
            record["doc_id"] for record in discovery
            if all(record["predicate_status"].get(name) == "agreed_true" for name in rule["conditions"])
        ))
        coverage_key = (rule["target_label"], covered_docs)
        if coverage_key in seen_coverage:
            continue
        seen_coverage.add(coverage_key)
        deduplicated.append(rule)
    prefixes = {"bullish": "BULL", "bearish": "BEAR"}
    counters = {"bullish": 0, "bearish": 0}
    output: list[dict[str, Any]] = []
    for rule in sorted(deduplicated, key=lambda row: (row["target_label"], -float(row["score"]))):
        target = rule["target_label"]
        counters[target] += 1
        output.append({
            **rule,
            "rule_id": f"LC-{prefixes[target]}-{counters[target]:02d}",
            "status": "qualified_stable_discovery",
        })
    return output, diagnostics


def build_signals(
    records: list[dict[str, Any]],
    rulebook: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    digest = rulebook_hash(rulebook)
    rows: list[dict[str, Any]] = []
    for record in records:
        active = activated_rules(rulebook, record["predicate_consensus"])
        score = rule_score(active)
        rows.append({
            "doc_id": record["doc_id"],
            "publish_time": record["publish_time"],
            "direction_label": "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral",
            "direction_score": score,
            "zero_shot_score": 0.0,
            "confidence": statistics.mean(
                [float(row.get("confidence", 0) or 0) for row in record["predicate_consensus"] if row["status"] == "agreed_true"]
            ) if any(row["status"] == "agreed_true" for row in record["predicate_consensus"]) else 0.0,
            "horizon_days": 5,
            "activated_rules": json.dumps(active, ensure_ascii=False, separators=(",", ":")),
            "predicate_consensus": json.dumps(record["predicate_consensus"], ensure_ascii=False, separators=(",", ":")),
            "inference_mode": "v3_rulebook_consistent" if active else "rulebook_inactive",
            "rulebook_sha256": digest,
        })
    write_path(SIGNAL_FILE, SIGNAL_FIELDS, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--limit-new", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    texts = combine_texts()
    predicates = annotate_missing(texts, args.model, args.limit_new, args.workers)
    contracts = _read_csv("lithium_contract_daily.csv")
    continuous = build_main_continuous(contracts)
    records = build_records(texts, predicates, continuous, contracts)
    induced_rulebook = induce_rulebook(records, anchor_predicates=ECONOMIC_PREDICATES)
    rulebook, candidate_diagnostics = stable_discovery_rulebook(records, induced_rulebook)
    write_path(
        RULEBOOK_FILE,
        RULE_FIELDS,
        [{**rule, "conditions": " AND ".join(rule["conditions"])} for rule in rulebook],
    )
    signals = build_signals(records, rulebook)
    rows = _strategy_rows(continuous, signals, 5.0, contracts)
    report = {
        "version": "lithium-v3-research-candidate",
        "selection_boundary": "文本与规则选择仅使用2025-12-31前信息；2026仅作一次性压力检验",
        "counts": {
            "texts": len(texts),
            "predicate_annotations": len(predicates),
            "labeled_records": len(records),
            "rules": len(rulebook),
            "candidate_rules_before_stability_gate": len(induced_rulebook),
            "signals": len(signals),
        },
        "rulebook_sha256": rulebook_hash(rulebook),
        "rule_temporal_diagnostics": temporal_rule_diagnostics(records, rulebook),
        "candidate_rule_temporal_diagnostics": candidate_diagnostics,
        "validation_metrics": _metrics(rows, "validation"),
        "validation_bootstrap": block_bootstrap_increment(rows, split="validation"),
        "validation_confirmed_trend_metrics": _metrics(
            rows, "validation", strategies=("pure_trend", PROSPECTIVE_STRATEGY)
        ),
        "validation_confirmed_trend_bootstrap": block_bootstrap_increment(
            rows, split="validation", enhanced_strategy=PROSPECTIVE_STRATEGY
        ),
        "old_oos_stress_metrics": _metrics(rows, "oos"),
        "old_oos_stress_bootstrap": block_bootstrap_increment(rows, split="oos"),
        "old_oos_confirmed_trend_metrics": _metrics(
            rows, "oos", strategies=("pure_trend", PROSPECTIVE_STRATEGY)
        ),
        "old_oos_confirmed_trend_bootstrap": block_bootstrap_increment(
            rows, split="oos", enhanced_strategy=PROSPECTIVE_STRATEGY
        ),
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
