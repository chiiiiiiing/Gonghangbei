"""Validate generated research outputs beyond the locked B-side CSV contract."""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
REPORT_PATH = ROOT / "查看材料" / "因子研究报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

SCHEMAS = {
    "predicate_matrix.csv": [
        "event_id",
        "doc_id",
        "stock_code",
        "event_type",
        "event_time",
        "has_policy_support",
        "policy_directly_related_to_business",
        "event_mentions_core_product",
        "evidence_from_authoritative_source",
        "social_attention_spikes",
        "institutional_attention_increases",
        "investor_questions_increase",
        "management_response_vague",
        "announcement_contains_uncertainty",
        "event_evidence_strength",
        "event_has_short_term_price_impact",
    ],
    "event_forward_returns.csv": [
        "event_id",
        "stock_code",
        "event_time",
        "entry_trade_date",
        "exit_trade_date_5d",
        "forward_return_5d",
        "exit_trade_date_10d",
        "forward_return_10d",
        "future_info_ok",
    ],
    "rules.csv": [
        "rule_id",
        "rule_name",
        "condition",
        "target_label",
        "support_count",
        "positive_count",
        "win_rate",
        "avg_forward_return_5d",
        "score",
        "status",
    ],
    "factors.csv": [
        "trade_date",
        "stock_code",
        "factor_name",
        "factor_value",
        "raw_score",
        "trigger_event_ids",
        "trigger_rule_ids",
        "forward_return_5d",
        "future_info_ok",
    ],
    "factor_snapshot.csv": [
        "trade_date",
        "stock_code",
        "stock_name",
        "industry_sector",
        "factor_name",
        "factor_value",
        "raw_score",
        "trigger_event_ids",
        "trigger_rule_ids",
    ],
    "group_returns.csv": ["group", "sample_count", "avg_forward_return_5d"],
    "rank_ic_timeseries.csv": ["trade_date", "rank_ic_5d", "sample_count"],
    "backtest_metrics.csv": ["metric", "value", "description"],
}


def read_csv(filename: str) -> tuple[list[str], list[dict[str, str]]]:
    path = SAMPLE_DIR / filename
    if not path.exists():
        return [], []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def main() -> int:
    errors: list[str] = []
    tables: dict[str, list[dict[str, str]]] = {}

    for filename, schema in SCHEMAS.items():
        header, rows = read_csv(filename)
        tables[filename] = rows
        if not header:
            errors.append(f"{filename}: missing or empty")
        elif header != schema:
            errors.append(f"{filename}: header mismatch")

    event_rows = tables.get("event_forward_returns.csv", [])
    for row in event_rows:
        if row["future_info_ok"] != "true":
            errors.append(f"event_forward_returns.csv: future_info_ok false for {row['event_id']}")
            continue
        if parse_date(row["entry_trade_date"]) <= parse_date(row["event_time"]):
            errors.append(f"event_forward_returns.csv: entry not after event_time for {row['event_id']}")
        for field in ["forward_return_5d", "forward_return_10d"]:
            if not is_float(row[field]):
                errors.append(f"event_forward_returns.csv: invalid {field} for {row['event_id']}")

    for row in tables.get("rules.csv", []):
        support_count = int(row["support_count"])
        if row["status"] == "qualified" and support_count < 5:
            errors.append(f"rules.csv: qualified rule {row['rule_id']} has support_count < 5")
        if row["status"] == "below_min_occurrence" and support_count >= 5:
            errors.append(f"rules.csv: rule {row['rule_id']} incorrectly below threshold")
        for field in ["win_rate", "avg_forward_return_5d", "score"]:
            if not is_float(row[field]):
                errors.append(f"rules.csv: invalid {field} for {row['rule_id']}")

    for row in tables.get("factors.csv", []):
        if row["future_info_ok"] != "true":
            errors.append(f"factors.csv: future_info_ok false for {row['stock_code']} {row['trade_date']}")
        if not row["trigger_event_ids"] or not row["trigger_rule_ids"]:
            errors.append(f"factors.csv: missing trigger lineage for {row['stock_code']} {row['trade_date']}")
        if not is_float(row["factor_value"]):
            errors.append(f"factors.csv: invalid factor_value for {row['stock_code']} {row['trade_date']}")

    snapshot = tables.get("factor_snapshot.csv", [])
    snapshot_codes = {row["stock_code"] for row in snapshot}
    if len(snapshot) != 30 or len(snapshot_codes) != 30:
        errors.append("factor_snapshot.csv: expected exactly 30 unique stocks")

    metric_map = {row["metric"]: row["value"] for row in tables.get("backtest_metrics.csv", [])}
    if metric_map.get("future_info_audit") != "pass":
        errors.append("backtest_metrics.csv: future_info_audit is not pass")

    if not REPORT_PATH.exists():
        errors.append("因子研究报告.md: missing")
    else:
        report_text = REPORT_PATH.read_text(encoding="utf-8")
        if DISCLAIMER not in report_text:
            errors.append("因子研究报告.md: disclaimer missing")

    print(f"research_output_errors={len(errors)}")
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
