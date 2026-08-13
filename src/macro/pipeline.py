"""End-to-end macro feature, rule-route and evaluation pipeline."""

from __future__ import annotations

import csv
import copy
import json
from pathlib import Path
from typing import Any

from src.ingestion.common import atomic_write_csv
from src.macro.ai_rules import fuse_macro_predicates
from src.macro.ai_rules import MACRO_PROMPT_VERSION
from src.macro.features import aggregate_base_monthly_features, deduplicate_documents
from src.macro.modeling import evaluate_routes
from src.macro.predicates import ground_all_macro_predicates
from src.macro.rules import (
    ai_dynamic_rule_features,
    combine_route_features,
    historical_rule_features,
    learn_historical_rules,
)
from src.macro.schema import (
    MACRO_PREDICATE_FIELDS,
    MACRO_RULE_FIELDS,
    MONTHLY_FEATURE_FIELDS,
    TARGET_FIELDS,
    validate_target_row,
)
from src.research.scoring import source_reliability


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_macro_ai_analyses(
    path: Path,
    documents: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Load successful, already validated macro AI cache records."""
    document_by_id = {row["doc_id"]: row for row in documents or []}
    rows: list[dict[str, Any]] = []
    for record in read_jsonl(path):
        if (
            record.get("status") != "success"
            or record.get("prompt_version") != MACRO_PROMPT_VERSION
            or not isinstance(record.get("analysis"), dict)
        ):
            continue
        analysis = copy.deepcopy(record["analysis"])
        document = document_by_id.get(str(record.get("doc_id", "")))
        if document:
            cap = source_reliability(document.get("source_type", ""), document.get("source_name", ""))
            for predicate in analysis.get("predicates", []):
                predicate["confidence"] = f"{min(float(predicate.get('confidence', 0.0)), cap):.4f}"
            for rule in analysis.get("candidate_rules", []):
                rule["confidence"] = min(float(rule.get("confidence", 0.0)), cap)
        rows.append(
            {
                "doc_id": str(record.get("doc_id", "")),
                "period_end": str(record.get("period_end", "")),
                "used": True,
                "result": analysis,
            }
        )
    return rows


def _ai_predicates_by_doc(ai_analyses: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        str(row["doc_id"]): list(row["result"].get("predicates", []))
        for row in ai_analyses
        if isinstance(row.get("result"), dict)
    }


def _successful_legacy_ai_doc_ids(path: Path) -> set[str]:
    return {
        str(row.get("doc_id", ""))
        for row in read_jsonl(path)
        if row.get("status") == "success" and row.get("analysis")
    }


def build_macro_research(sample_dir: Path = SAMPLE_DIR) -> dict[str, Any]:
    documents = read_csv(sample_dir / "raw_documents.csv")
    events = read_csv(sample_dir / "events.csv")
    legacy_predicates = read_csv(sample_dir / "predicates.csv")
    entity_links = read_csv(sample_dir / "entity_links.csv")
    factors = read_csv(sample_dir / "factors.csv")
    targets = read_csv(sample_dir / "macro_targets.csv")
    if targets:
        if list(targets[0]) != TARGET_FIELDS:
            raise ValueError("macro_targets.csv 字段合同不匹配")
        for row in targets:
            validate_target_row(row)

    canonical_documents, dedup_dropped = deduplicate_documents(documents)
    canonical_ids = {row["doc_id"] for row in canonical_documents}
    deterministic_predicates = ground_all_macro_predicates(canonical_documents)
    ai_analyses = load_macro_ai_analyses(
        sample_dir / "macro_ai_annotations.jsonl",
        canonical_documents,
    )
    ai_by_doc = _ai_predicates_by_doc(ai_analyses)
    research_predicates: list[dict[str, Any]] = []
    deterministic_by_doc: dict[str, list[dict[str, Any]]] = {}
    for row in deterministic_predicates:
        deterministic_by_doc.setdefault(str(row["doc_id"]), []).append(row)
    for document in canonical_documents:
        doc_id = document["doc_id"]
        deterministic = deterministic_by_doc[doc_id]
        research_predicates.extend(
            fuse_macro_predicates(deterministic, ai_by_doc[doc_id])
            if doc_id in ai_by_doc
            else deterministic
        )

    base_features, feature_audit = aggregate_base_monthly_features(
        canonical_documents,
        research_predicates,
        events=[row for row in events if row["doc_id"] in canonical_ids],
        legacy_predicates=legacy_predicates,
        entity_links=entity_links,
        factor_rows=factors,
        ai_annotated_doc_ids=_successful_legacy_ai_doc_ids(sample_dir / "ai_annotations.jsonl"),
    )
    historical_rules = learn_historical_rules(research_predicates, targets)
    historical_features = historical_rule_features(historical_rules, research_predicates)
    ai_features = ai_dynamic_rule_features(ai_analyses)
    all_features = combine_route_features(base_features, historical_features, ai_features)
    evaluation = evaluate_routes(targets, all_features)
    evaluation["data_audit"] = {
        **feature_audit,
        "input_document_count": len(documents),
        "canonical_document_count": len(canonical_documents),
        "dropped_document_count": len(dedup_dropped),
        "dropped_documents": dedup_dropped,
        "dedup_dropped_document_count": len(dedup_dropped),
        "macro_ai_success_count": len(ai_analyses),
        "macro_target_count": len(targets),
        "historical_macro_rule_count": len(historical_rules),
    }

    atomic_write_csv(sample_dir / "macro_predicates.csv", MACRO_PREDICATE_FIELDS, research_predicates)
    atomic_write_csv(sample_dir / "macro_rules.csv", MACRO_RULE_FIELDS, historical_rules)
    atomic_write_csv(sample_dir / "macro_monthly_features.csv", MONTHLY_FEATURE_FIELDS, all_features)
    (sample_dir / "macro_route_evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evaluation
