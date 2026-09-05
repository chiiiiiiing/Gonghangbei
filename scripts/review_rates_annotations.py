"""Prepare a blind human-review sample and score completed predicate labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.factors import ground_predicates, merge_llm_predicates  # noqa: E402
from src.rates.schema import PREDICATES  # noqa: E402


DATA_DIR = ROOT / "data" / "sample"
REVIEW_DIR = ROOT / "data" / "review"
TEXT_PATH = DATA_DIR / "rates_policy_texts.csv"
ANNOTATION_PATH = DATA_DIR / "rates_llm_annotations.jsonl"
DOC_SAMPLE_PATH = REVIEW_DIR / "rates_annotation_blind_sample.csv"
GOLD_PATH = REVIEW_DIR / "rates_annotation_gold_template.csv"
REPORT_JSON_PATH = REVIEW_DIR / "rates_annotation_validation.json"
REPORT_MD_PATH = REVIEW_DIR / "rates_annotation_validation.md"
DEFAULT_QUOTAS = {"中国人民银行": 30, "国家统计局": 20, "财政部": 10}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _annotation_cache(path: Path = ANNOTATION_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[(str(row.get("doc_id", "")), str(row.get("source_sha256", "")))] = row
    return rows


def _pipeline_predictions(
    document: dict[str, str], cache: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, dict[str, dict[str, Any]]]:
    deterministic = ground_predicates(document)
    annotation = cache.get((document["doc_id"], document["source_sha256"]))
    if annotation and annotation.get("used"):
        rows = merge_llm_predicates(
            deterministic,
            annotation.get("predicates", []),
            f"{document['title']}。{document['content']}",
        )
        mode = "llm_evidence_gated"
    else:
        rows = [{**row, "consensus": "deterministic_only"} for row in deterministic]
        mode = "deterministic_fallback" if annotation else "unannotated_deterministic"
    return mode, {str(row["predicate_name"]): row for row in rows}


def _rank(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def select_documents(
    texts: list[dict[str, str]],
    annotations: dict[tuple[str, str], dict[str, Any]],
    quotas: dict[str, int] | None = None,
    seed: str = "rates-human-review-v1",
) -> list[dict[str, str]]:
    """Select source/year-balanced documents while prioritizing positive predicates."""
    quotas = quotas or DEFAULT_QUOTAS
    selected: list[dict[str, str]] = []
    for source, quota in quotas.items():
        candidates = [row for row in texts if row.get("source_name") == source]
        enriched: list[tuple[dict[str, str], int, str, str]] = []
        for row in candidates:
            mode, predictions = _pipeline_predictions(row, annotations)
            active = sum(bool(item.get("value")) for item in predictions.values())
            year = row.get("publish_time", "")[:4]
            enriched.append((row, active, mode, year))
        by_year: dict[str, list[tuple[dict[str, str], int, str, str]]] = defaultdict(list)
        for item in enriched:
            by_year[item[3]].append(item)
        for values in by_year.values():
            values.sort(key=lambda item: (-item[1], _rank(seed, item[0]["doc_id"])))
        source_selected: list[dict[str, str]] = []
        years = sorted(by_year)
        while len(source_selected) < min(quota, len(candidates)):
            progressed = False
            for year in years:
                if by_year[year] and len(source_selected) < quota:
                    source_selected.append(by_year[year].pop(0)[0])
                    progressed = True
            if not progressed:
                break
        selected.extend(source_selected)
    selected.sort(key=lambda row: (row["source_name"], row["publish_time"], row["doc_id"]))
    return selected


def prepare_review(
    text_path: Path = TEXT_PATH,
    annotation_path: Path = ANNOTATION_PATH,
    review_dir: Path = REVIEW_DIR,
) -> dict[str, Any]:
    texts = _read_csv(text_path)
    annotations = _annotation_cache(annotation_path)
    selected = select_documents(texts, annotations)
    review_dir.mkdir(parents=True, exist_ok=True)
    document_path = review_dir / DOC_SAMPLE_PATH.name
    gold_path = review_dir / GOLD_PATH.name
    document_fields = [
        "sample_id", "doc_id", "source_name", "publish_time", "title", "content",
        "source_url", "source_sha256",
    ]
    with document_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=document_fields, lineterminator="\n")
        writer.writeheader()
        for index, row in enumerate(selected, 1):
            writer.writerow({"sample_id": f"HR-{index:03d}", **{field: row.get(field, "") for field in document_fields[1:]}})
    gold_fields = [
        "sample_id", "predicate_name", "human_value", "human_evidence_text",
        "reviewer", "reviewed_at", "notes",
    ]
    with gold_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=gold_fields, lineterminator="\n")
        writer.writeheader()
        for index, _row in enumerate(selected, 1):
            for predicate_name in PREDICATES:
                writer.writerow({
                    "sample_id": f"HR-{index:03d}", "predicate_name": predicate_name,
                    "human_value": "", "human_evidence_text": "", "reviewer": "",
                    "reviewed_at": "", "notes": "",
                })
    modes = Counter(_pipeline_predictions(row, annotations)[0] for row in selected)
    return {
        "documents": len(selected),
        "predicate_labels_required": len(selected) * len(PREDICATES),
        "source_counts": dict(Counter(row["source_name"] for row in selected)),
        "pipeline_mode_counts": dict(modes),
        "document_file": _display_path(document_path),
        "gold_file": _display_path(gold_path),
        "blind": True,
    }


def _metric(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def evaluate_review(
    text_path: Path = TEXT_PATH,
    annotation_path: Path = ANNOTATION_PATH,
    review_dir: Path = REVIEW_DIR,
) -> dict[str, Any]:
    docs = {row["sample_id"]: row for row in _read_csv(review_dir / DOC_SAMPLE_PATH.name)}
    gold = _read_csv(review_dir / GOLD_PATH.name)
    texts = {row["doc_id"]: row for row in _read_csv(text_path)}
    annotations = _annotation_cache(annotation_path)
    completed = [row for row in gold if row.get("human_value", "").strip() in {"0", "1"}]
    invalid = [row for row in gold if row.get("human_value", "").strip() not in {"", "0", "1"}]
    if invalid:
        raise ValueError("human_value只允许空白、0或1")
    result: dict[str, Any] = {
        "version": "rates-annotation-validation-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "documents": len(docs), "labels_required": len(gold), "labels_completed": len(completed),
        "completion_rate": round(len(completed) / len(gold), 4) if gold else 0.0,
        "status": "complete" if gold and len(completed) == len(gold) else "awaiting_independent_human_labels",
        "metrics": {},
        "disclaimer": "空白模板不构成人工验收；只有独立复核员填写全部金标签后才计算正式指标。",
    }
    if completed:
        confusion: dict[str, Counter[str]] = defaultdict(Counter)
        by_mode: dict[str, Counter[str]] = defaultdict(Counter)
        evidence = Counter()
        prediction_cache: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}
        for row in completed:
            sample = docs[row["sample_id"]]
            document = texts[sample["doc_id"]]
            if sample["sample_id"] not in prediction_cache:
                prediction_cache[sample["sample_id"]] = _pipeline_predictions(document, annotations)
            mode, predictions = prediction_cache[sample["sample_id"]]
            pred_row = predictions[row["predicate_name"]]
            predicted = bool(pred_row.get("value"))
            actual = row["human_value"] == "1"
            key = "tp" if predicted and actual else "fp" if predicted else "fn" if actual else "tn"
            confusion[row["predicate_name"]][key] += 1
            by_mode[mode][key] += 1
            if predicted:
                evidence["predicted_positive"] += 1
                if str(pred_row.get("evidence_text", "")) in f"{document['title']}。{document['content']}":
                    evidence["prediction_grounded"] += 1
            if actual:
                human_evidence = row.get("human_evidence_text", "").strip()
                evidence["human_positive"] += 1
                if human_evidence and human_evidence in f"{document['title']}。{document['content']}":
                    evidence["human_evidence_grounded"] += 1
        predicate_metrics = {}
        for name in PREDICATES:
            counts = confusion[name]
            predicate_metrics[name] = {
                **{key: counts[key] for key in ("tp", "fp", "fn", "tn")},
                **_metric(counts["tp"], counts["fp"], counts["fn"]),
            }
        mode_metrics = {}
        for mode, counts in by_mode.items():
            mode_metrics[mode] = {
                **{key: counts[key] for key in ("tp", "fp", "fn", "tn")},
                **_metric(counts["tp"], counts["fp"], counts["fn"]),
            }
        result["metrics"] = {
            "by_predicate": predicate_metrics,
            "by_pipeline_mode": mode_metrics,
            "macro_f1": round(sum(row["f1"] for row in predicate_metrics.values()) / len(predicate_metrics), 4),
            "prediction_evidence_grounding_rate": round(
                evidence["prediction_grounded"] / evidence["predicted_positive"], 4
            ) if evidence["predicted_positive"] else None,
            "human_evidence_grounding_rate": round(
                evidence["human_evidence_grounded"] / evidence["human_positive"], 4
            ) if evidence["human_positive"] else None,
        }
    report_json = review_dir / REPORT_JSON_PATH.name
    report_md = review_dir / REPORT_MD_PATH.name
    report_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 利率文本谓词人工验收", "",
        f"- 状态：`{result['status']}`",
        f"- 抽样文档：{result['documents']}篇",
        f"- 已填写标签：{result['labels_completed']} / {result['labels_required']}（{result['completion_rate']:.1%}）",
        f"- 说明：{result['disclaimer']}", "",
    ]
    if result["metrics"]:
        lines.extend([
            f"- Macro-F1：{result['metrics']['macro_f1']:.4f}",
            f"- 预测证据原文命中率：{result['metrics']['prediction_evidence_grounding_rate']}", "",
            "| 谓词 | Precision | Recall | F1 |", "| --- | ---: | ---: | ---: |",
        ])
        for name, row in result["metrics"]["by_predicate"].items():
            lines.append(f"| `{name}` | {row['precision']:.4f} | {row['recall']:.4f} | {row['f1']:.4f} |")
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "evaluate"))
    args = parser.parse_args()
    result = prepare_review() if args.command == "prepare" else evaluate_review()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
