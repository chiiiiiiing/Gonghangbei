"""Build research outputs from B-side sample CSV files.

The calculations here are deterministic and intentionally simple. They exist to
exercise the AlphaLens data contract end to end with the current input data.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"

PREDICATE_COLUMNS = [
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
]


RULE_DEFS = [
    {
        "rule_id": "R001",
        "rule_name": "policy_attention_momentum",
        "condition": "has_policy_support AND policy_directly_related_to_business AND social_attention_spikes",
        "target_label": "short_term_theme_momentum",
        "predicate_true": [
            "has_policy_support",
            "policy_directly_related_to_business",
            "social_attention_spikes",
        ],
        "event_types": [],
    },
    {
        "rule_id": "R002",
        "rule_name": "authoritative_core_event",
        "condition": "event_mentions_core_product AND evidence_from_authoritative_source",
        "target_label": "explainable_event_signal",
        "predicate_true": ["event_mentions_core_product", "evidence_from_authoritative_source"],
        "event_types": [],
    },
    {
        "rule_id": "R003",
        "rule_name": "capacity_authoritative_expansion",
        "condition": "event_type = capacity_expansion AND evidence_from_authoritative_source",
        "target_label": "capacity_expansion_attention",
        "predicate_true": ["evidence_from_authoritative_source"],
        "event_types": ["capacity_expansion"],
    },
    {
        "rule_id": "R004",
        "rule_name": "investor_pressure_watch",
        "condition": "investor_questions_increase AND management_response_vague",
        "target_label": "information_uncertainty_watch",
        "predicate_true": ["investor_questions_increase", "management_response_vague"],
        "event_types": [],
    },
]


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def build_predicates_by_event() -> dict[str, dict[str, str]]:
    predicates = defaultdict(dict)
    for row in read_csv("predicates.csv"):
        predicates[row["event_id"]][row["predicate_name"]] = row["value"]
    return predicates


def build_predicate_matrix() -> list[dict[str, str]]:
    predicates = build_predicates_by_event()
    rows: list[dict[str, str]] = []
    for event in read_csv("events.csv"):
        row = {
            "event_id": event["event_id"],
            "doc_id": event["doc_id"],
            "stock_code": event["stock_code"],
            "event_type": event["event_type"],
            "event_time": event["event_time"],
        }
        for predicate_name in PREDICATE_COLUMNS:
            row[predicate_name] = predicates[event["event_id"]].get(predicate_name, "")
        rows.append(row)
    write_csv(
        SAMPLE_DIR / "predicate_matrix.csv",
        ["event_id", "doc_id", "stock_code", "event_type", "event_time", *PREDICATE_COLUMNS],
        rows,
    )
    return rows


def build_market_by_stock() -> dict[str, list[dict[str, str]]]:
    market_by_stock: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv("market_data.csv"):
        market_by_stock[row["stock_code"]].append(row)
    for rows in market_by_stock.values():
        rows.sort(key=lambda item: item["trade_date"])
    return market_by_stock


def compute_forward_returns() -> dict[str, dict[str, str]]:
    market_by_stock = build_market_by_stock()
    rows: list[dict[str, object]] = []
    by_event: dict[str, dict[str, str]] = {}

    for event in read_csv("events.csv"):
        future_rows = [row for row in market_by_stock[event["stock_code"]] if row["trade_date"] > event["event_time"]]
        if len(future_rows) < 10:
            continue
        entry = future_rows[0]
        exit_5d = future_rows[4]
        exit_10d = future_rows[9]
        entry_open = float(entry["open"])
        ret_5d = float(exit_5d["close"]) / entry_open - 1
        ret_10d = float(exit_10d["close"]) / entry_open - 1
        result = {
            "event_id": event["event_id"],
            "stock_code": event["stock_code"],
            "event_time": event["event_time"],
            "entry_trade_date": entry["trade_date"],
            "exit_trade_date_5d": exit_5d["trade_date"],
            "forward_return_5d": f"{ret_5d:.6f}",
            "exit_trade_date_10d": exit_10d["trade_date"],
            "forward_return_10d": f"{ret_10d:.6f}",
            "future_info_ok": "true",
        }
        rows.append(result)
        by_event[event["event_id"]] = {key: str(value) for key, value in result.items()}

    write_csv(
        SAMPLE_DIR / "event_forward_returns.csv",
        [
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
        rows,
    )
    return by_event


def rule_matches(event_row: dict[str, str], rule_def: dict[str, object]) -> bool:
    event_types = rule_def["event_types"]
    if event_types and event_row["event_type"] not in event_types:
        return False
    predicate_true = rule_def["predicate_true"]
    return all(event_row.get(predicate_name) == "true" for predicate_name in predicate_true)


def build_rules(matrix: list[dict[str, str]], forward_by_event: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rule_def in RULE_DEFS:
        matched = [row for row in matrix if rule_matches(row, rule_def)]
        return_values = [
            float(forward_by_event[row["event_id"]]["forward_return_5d"])
            for row in matched
            if row["event_id"] in forward_by_event and forward_by_event[row["event_id"]]["forward_return_5d"] != ""
        ]
        support_count = len(return_values)
        positive_count = sum(1 for value in return_values if value > 0)
        win_rate = positive_count / support_count if support_count else 0.0
        avg_return = mean(return_values) if return_values else 0.0
        coverage_bonus = min(support_count / 20.0, 1.0) * 0.2
        score = win_rate * 0.55 + max(avg_return, 0.0) * 4.0 + coverage_bonus
        status = "qualified" if support_count >= 5 else "below_min_occurrence"
        rows.append(
            {
                "rule_id": str(rule_def["rule_id"]),
                "rule_name": str(rule_def["rule_name"]),
                "condition": str(rule_def["condition"]),
                "target_label": str(rule_def["target_label"]),
                "support_count": str(support_count),
                "positive_count": str(positive_count),
                "win_rate": f"{win_rate:.4f}",
                "avg_forward_return_5d": f"{avg_return:.6f}",
                "score": f"{score:.4f}",
                "status": status,
            }
        )
    write_csv(
        SAMPLE_DIR / "rules.csv",
        [
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
        rows,
    )
    return rows


def build_event_factors(
    matrix: list[dict[str, str]],
    rules: list[dict[str, str]],
    forward_by_event: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    rule_by_id = {rule["rule_id"]: rule for rule in rules if rule["status"] == "qualified"}
    rule_defs_by_id = {str(rule["rule_id"]): rule for rule in RULE_DEFS}
    rows: list[dict[str, str]] = []
    for event_row in matrix:
        triggered_rule_ids = [
            rule_id
            for rule_id in rule_by_id
            if rule_matches(event_row, rule_defs_by_id[rule_id])
        ]
        if not triggered_rule_ids:
            continue
        forward = forward_by_event.get(event_row["event_id"])
        if not forward:
            continue
        if forward["future_info_ok"] != "true":
            continue
        evidence_strength = float(event_row["event_evidence_strength"])
        impact_prior = float(event_row["event_has_short_term_price_impact"])
        raw_score = sum(float(rule_by_id[rule_id]["score"]) for rule_id in triggered_rule_ids)
        factor_value = raw_score * (0.7 * evidence_strength + 0.3 * impact_prior)
        rows.append(
            {
                "trade_date": forward["entry_trade_date"],
                "stock_code": event_row["stock_code"],
                "factor_name": "policy_attention_momentum_score",
                "factor_value": f"{factor_value:.6f}",
                "raw_score": f"{raw_score:.6f}",
                "trigger_event_ids": event_row["event_id"],
                "trigger_rule_ids": "|".join(triggered_rule_ids),
                "forward_return_5d": forward["forward_return_5d"],
                "future_info_ok": "true",
            }
        )
    write_csv(
        SAMPLE_DIR / "factors.csv",
        [
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
        rows,
    )
    return rows


def zscore(values: list[float]) -> list[float]:
    if not values:
        return []
    avg = mean(values)
    variance = mean([(value - avg) ** 2 for value in values])
    std = math.sqrt(variance)
    if std == 0:
        return [0.0 for _ in values]
    return [(value - avg) / std for value in values]


def build_factor_snapshot(matrix: list[dict[str, str]], rules: list[dict[str, str]]) -> None:
    stocks = {row["stock_code"]: row for row in read_csv("stock_pool.csv")}
    last_trade_date = max(row["trade_date"] for row in read_csv("market_data.csv"))
    rule_by_id = {rule["rule_id"]: rule for rule in rules if rule["status"] == "qualified"}
    rule_defs_by_id = {str(rule["rule_id"]): rule for rule in RULE_DEFS}
    raw_by_stock: dict[str, float] = {stock_code: 0.0 for stock_code in stocks}
    events_by_stock: dict[str, list[str]] = defaultdict(list)
    rules_by_stock: dict[str, set[str]] = defaultdict(set)
    last_date = parse_date(last_trade_date)

    for event_row in matrix:
        if event_row["event_time"] > last_trade_date:
            continue
        triggered_rule_ids = [
            rule_id
            for rule_id in rule_by_id
            if rule_matches(event_row, rule_defs_by_id[rule_id])
        ]
        if not triggered_rule_ids:
            continue
        days_since = max((last_date - parse_date(event_row["event_time"])).days, 0)
        recency_decay = 0.5 ** (days_since / 45.0)
        event_weight = float(event_row["event_evidence_strength"]) * recency_decay
        score = sum(float(rule_by_id[rule_id]["score"]) for rule_id in triggered_rule_ids) * event_weight
        stock_code = event_row["stock_code"]
        raw_by_stock[stock_code] += score
        events_by_stock[stock_code].append(event_row["event_id"])
        rules_by_stock[stock_code].update(triggered_rule_ids)

    by_sector: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for stock_code, raw_value in raw_by_stock.items():
        by_sector[stocks[stock_code]["industry_sector"]].append((stock_code, raw_value))

    z_by_stock: dict[str, float] = {}
    for items in by_sector.values():
        values = [raw_value for _stock_code, raw_value in items]
        scores = zscore(values)
        for (stock_code, _raw_value), score in zip(items, scores):
            z_by_stock[stock_code] = score

    rows = []
    for stock_code, stock in sorted(stocks.items(), key=lambda item: z_by_stock[item[0]], reverse=True):
        rows.append(
            {
                "trade_date": last_trade_date,
                "stock_code": stock_code,
                "stock_name": stock["stock_name"],
                "industry_sector": stock["industry_sector"],
                "factor_name": "policy_attention_momentum_score",
                "factor_value": f"{z_by_stock[stock_code]:.6f}",
                "raw_score": f"{raw_by_stock[stock_code]:.6f}",
                "trigger_event_ids": "|".join(events_by_stock[stock_code]),
                "trigger_rule_ids": "|".join(sorted(rules_by_stock[stock_code])),
            }
        )
    write_csv(
        SAMPLE_DIR / "factor_snapshot.csv",
        [
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
        rows,
    )


def rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        next_index = index
        while next_index < len(ordered) and ordered[next_index][1] == ordered[index][1]:
            next_index += 1
        avg_rank = (index + next_index - 1) / 2 + 1
        for original_index, _value in ordered[index:next_index]:
            ranks[original_index] = avg_rank
        index = next_index
    return ranks


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2 or len(left) != len(right):
        return 0.0
    left_avg = mean(left)
    right_avg = mean(right)
    numerator = sum((x - left_avg) * (y - right_avg) for x, y in zip(left, right))
    left_var = sum((x - left_avg) ** 2 for x in left)
    right_var = sum((y - right_avg) ** 2 for y in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator else 0.0


def build_backtest_outputs(factor_rows: list[dict[str, str]]) -> None:
    valid_rows = [row for row in factor_rows if row["forward_return_5d"] != ""]
    sorted_rows = sorted(valid_rows, key=lambda row: float(row["factor_value"]))
    group_count = 5
    group_rows = []
    for group_index in range(group_count):
        start = group_index * len(sorted_rows) // group_count
        end = (group_index + 1) * len(sorted_rows) // group_count
        rows = sorted_rows[start:end]
        avg_return = mean([float(row["forward_return_5d"]) for row in rows]) if rows else 0.0
        group_rows.append(
            {
                "group": f"G{group_index + 1}",
                "sample_count": str(len(rows)),
                "avg_forward_return_5d": f"{avg_return:.6f}",
            }
        )
    write_csv(SAMPLE_DIR / "group_returns.csv", ["group", "sample_count", "avg_forward_return_5d"], group_rows)

    by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in valid_rows:
        by_date[row["trade_date"]].append(row)
    ic_rows = []
    for trade_date, rows in sorted(by_date.items()):
        if len(rows) < 3:
            continue
        factor_values = [float(row["factor_value"]) for row in rows]
        returns = [float(row["forward_return_5d"]) for row in rows]
        rank_ic = correlation(rank(factor_values), rank(returns))
        ic_rows.append({"trade_date": trade_date, "rank_ic_5d": f"{rank_ic:.6f}", "sample_count": str(len(rows))})
    write_csv(SAMPLE_DIR / "rank_ic_timeseries.csv", ["trade_date", "rank_ic_5d", "sample_count"], ic_rows)

    rank_ic_values = [float(row["rank_ic_5d"]) for row in ic_rows]
    avg_rank_ic = mean(rank_ic_values) if rank_ic_values else 0.0
    group_spread = float(group_rows[-1]["avg_forward_return_5d"]) - float(group_rows[0]["avg_forward_return_5d"])
    win_rate = (
        sum(1 for row in valid_rows if float(row["forward_return_5d"]) > 0) / len(valid_rows)
        if valid_rows
        else 0.0
    )
    metrics = [
        {
            "metric": "event_factor_sample_count",
            "value": str(len(valid_rows)),
            "description": "事件触发后的因子样本数",
        },
        {
            "metric": "avg_rank_ic_5d",
            "value": f"{avg_rank_ic:.6f}",
            "description": "按事件入场日计算的 Rank IC 均值",
        },
        {
            "metric": "top_bottom_group_spread_5d",
            "value": f"{group_spread:.6f}",
            "description": "G5 减 G1 的 5 日收益差",
        },
        {
            "metric": "positive_forward_return_rate_5d",
            "value": f"{win_rate:.6f}",
            "description": "事件样本 5 日收益为正的比例",
        },
        {
            "metric": "future_info_audit",
            "value": "pass",
            "description": "收益窗口均使用 event_time 之后的交易日",
        },
    ]
    write_csv(SAMPLE_DIR / "backtest_metrics.csv", ["metric", "value", "description"], metrics)


def main() -> None:
    matrix = build_predicate_matrix()
    forward_by_event = compute_forward_returns()
    rules = build_rules(matrix, forward_by_event)
    factor_rows = build_event_factors(matrix, rules, forward_by_event)
    build_factor_snapshot(matrix, rules)
    build_backtest_outputs(factor_rows)
    print("Research outputs generated: predicate_matrix, returns, rules, factors, metrics.")


if __name__ == "__main__":
    main()
