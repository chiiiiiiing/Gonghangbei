"""V6 quality-gated text alpha: stricter signal gate and stronger incremental overlay.

This candidate is built on the same causal multi-horizon market baseline as the
frozen V5 module, but it only accepts a bullish zero-shot signal when all of the
following are true:

* ``authoritative_source`` is agreed_true;
* the source is not the GFEX warehouse-report feed;
* the zero-shot score is at least ``V6_MIN_ZERO_SHOT_SCORE``;
* ``uncertainty_high`` is not agreed_true;
* confidence is positive.

The accepted signal is applied with a 0.35 multiplier instead of the V5 0.25
multiplier.  This is still a long-only overlay: when the baseline is not positive
or no signal survives the quality gate, the V6 enhanced position equals the
baseline exactly.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from src.lithium.engine import _float, _parse_day, _split_for_day, build_main_continuous
from src.lithium.walkforward import (
    BASELINE_STRATEGY,
    _clip,
    _market_inputs,
    mature_baseline_positions,
    relabel_split,
    strategy_metrics,
)


V6_ENHANCED_STRATEGY = "v6_quality_text_alpha"
V6_TEXT_WEIGHT = 0.35
V6_TEXT_WINDOW_DAYS = 5
V6_MIN_ZERO_SHOT_SCORE = 0.2
V6_COST_BPS = 5.0
MIN_BOOTSTRAP_DAYS = 63


def _agreed_predicates(signal: dict[str, Any]) -> set[str]:
    raw = signal.get("predicate_consensus", "[]")
    rows = json.loads(raw) if isinstance(raw, str) else raw
    return {
        str(row.get("name", ""))
        for row in rows
        if row.get("status") == "agreed_true"
    }


def quality_text_events_v6(
    days: list[str],
    signals: list[dict[str, Any]],
    texts: list[dict[str, str]],
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, int]]:
    """Select high-conviction bullish, attributable, non-exchange signals."""
    text_by_id = {row["doc_id"]: row for row in texts}
    events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    audit = {
        "input_signals": len(signals),
        "accepted_signals": 0,
        "rejected_below_threshold": 0,
        "rejected_exchange_derived": 0,
        "rejected_quality_gate": 0,
        "rejected_uncertainty": 0,
        "rejected_zero_confidence": 0,
    }
    for signal in signals:
        text = text_by_id.get(str(signal.get("doc_id", "")))
        if text is None:
            audit["rejected_quality_gate"] += 1
            continue
        score = _clip(float(signal.get("zero_shot_score", 0) or 0))
        confidence = max(
            0.0,
            min(1.0, float(signal.get("zero_shot_confidence", 0) or 0)),
        )
        agreed = _agreed_predicates(signal)
        if score < V6_MIN_ZERO_SHOT_SCORE:
            audit["rejected_below_threshold"] += 1
            continue
        if text.get("source_name") == "广州期货交易所":
            audit["rejected_exchange_derived"] += 1
            continue
        if "authoritative_source" not in agreed:
            audit["rejected_quality_gate"] += 1
            continue
        if "uncertainty_high" in agreed:
            audit["rejected_uncertainty"] += 1
            continue
        if confidence <= 0:
            audit["rejected_zero_confidence"] += 1
            continue
        publish_day = str(signal.get("publish_time", ""))[:10]
        signal_index = next(
            (index for index, day in enumerate(days) if day >= publish_day), None
        )
        if signal_index is None:
            continue
        events[signal_index].append({
            "doc_id": signal.get("doc_id", ""),
            "publish_time": publish_day,
            "score": score,
            "confidence": confidence,
            "source_name": text.get("source_name", ""),
            "quality_rule": (
                "authoritative_source AND non_GFEX AND "
                f"zero_shot_score >= {V6_MIN_ZERO_SHOT_SCORE} "
                "AND NOT uncertainty_high"
            ),
        })
        audit["accepted_signals"] += 1
    return dict(events), audit


def active_quality_text_score_v6(
    index: int,
    events: dict[int, list[dict[str, Any]]],
) -> float:
    weighted_score = 0.0
    total_weight = 0.0
    for event_index in range(
        max(0, index - V6_TEXT_WINDOW_DAYS + 1), index + 1
    ):
        age = index - event_index
        decay = (V6_TEXT_WINDOW_DAYS - age) / V6_TEXT_WINDOW_DAYS
        for event in events.get(event_index, []):
            weight = float(event["confidence"]) * decay
            weighted_score += float(event["score"]) * weight
            total_weight += weight
    return weighted_score / total_weight if total_weight else 0.0


def strategy_rows_v6(
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]],
    signals: list[dict[str, Any]],
    texts: list[dict[str, str]],
    *,
    cost_bps: float = V6_COST_BPS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    days, _, market_returns, _ = _market_inputs(continuous, contracts)
    baseline = mature_baseline_positions(continuous, contracts)
    events, event_audit = quality_text_events_v6(days, signals, texts)
    rows: list[dict[str, Any]] = []
    previous = {BASELINE_STRATEGY: 0.0, V6_ENHANCED_STRATEGY: 0.0}
    nav = {BASELINE_STRATEGY: 1.0, V6_ENHANCED_STRATEGY: 1.0}
    cost_rate = cost_bps / 10000.0
    for index, context in baseline.items():
        if index not in market_returns:
            continue
        baseline_position = float(context["position"])
        text_score = active_quality_text_score_v6(index, events)
        text_confirmed = baseline_position > 0 and text_score > 0
        enhanced_position = (
            _clip(baseline_position + V6_TEXT_WEIGHT * text_score)
            if text_confirmed
            else baseline_position
        )
        positions = {
            BASELINE_STRATEGY: baseline_position,
            V6_ENHANCED_STRATEGY: enhanced_position,
        }
        entry_day = days[index + 1]
        market_return = market_returns[index]
        split = _split_for_day(_parse_day(entry_day, "trade_date"))
        for strategy, position in positions.items():
            turnover = abs(position - previous[strategy])
            net_return = position * market_return - turnover * cost_rate
            nav[strategy] *= 1.0 + net_return
            rows.append({
                "trade_date": entry_day,
                "signal_date": days[index],
                "split": split,
                "strategy": strategy,
                "position": position,
                "market_open_return": market_return,
                "turnover": turnover,
                "cost_bps": cost_bps,
                "net_return": net_return,
                "nav": nav[strategy],
                "baseline_position": baseline_position,
                "active_text_score": (
                    text_score if strategy == V6_ENHANCED_STRATEGY else 0.0
                ),
                "position_delta": (
                    enhanced_position - baseline_position
                    if strategy == V6_ENHANCED_STRATEGY else 0.0
                ),
                "text_confirmed": (
                    text_confirmed if strategy == V6_ENHANCED_STRATEGY else False
                ),
                "realized_volatility": context["realized_volatility"],
            })
            previous[strategy] = position
    return rows, event_audit


def map_live_prediction_v6(
    document: dict[str, str],
    prediction: dict[str, Any],
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]],
) -> dict[str, Any]:
    """Map one DeepSeek forecast to the V6 quality-gated overlay."""
    days = [str(row["trade_date"]) for row in continuous]
    publish_day = _parse_day(document["publish_time"], "publish_time").isoformat()
    signal_index = next(
        (index for index, day in enumerate(days) if day >= publish_day), None
    )
    if signal_index is None:
        return {
            "status": "awaiting_signal_day_market_data",
            "publish_time": publish_day,
            "latest_market_date": days[-1] if days else "",
            "baseline_strategy": BASELINE_STRATEGY,
            "enhanced_strategy": V6_ENHANCED_STRATEGY,
        }
    baseline = mature_baseline_positions(continuous, contracts)
    if signal_index not in baseline:
        return {
            "status": "insufficient_mature_baseline_context",
            "publish_time": publish_day,
            "signal_market_date": days[signal_index],
            "baseline_strategy": BASELINE_STRATEGY,
            "enhanced_strategy": V6_ENHANCED_STRATEGY,
        }
    predicates = {
        str(row.get("name", "")): str(row.get("status", ""))
        for row in prediction.get("predicate_consensus", [])
    }
    zero_shot_score = _clip(float(prediction.get("zero_shot_score", 0) or 0))
    zero_shot_confidence = max(
        0.0,
        min(1.0, float(prediction.get("zero_shot_confidence", 0) or 0)),
    )
    quality_rule_active = (
        predicates.get("authoritative_source") == "agreed_true"
        and document.get("source_name") != "广州期货交易所"
        and zero_shot_score >= V6_MIN_ZERO_SHOT_SCORE
        and predicates.get("uncertainty_high") != "agreed_true"
        and zero_shot_confidence > 0
    )
    text_score = zero_shot_score if quality_rule_active else 0.0
    baseline_position = float(baseline[signal_index]["position"])
    text_confirmed = baseline_position > 0 and text_score > 0
    enhanced_position = (
        _clip(baseline_position + V6_TEXT_WEIGHT * text_score)
        if text_confirmed else baseline_position
    )
    execution_day = days[signal_index + 1] if signal_index + 1 < len(days) else ""
    return {
        "status": "mapped" if execution_day else "awaiting_next_trading_day",
        "publish_time": publish_day,
        "signal_market_date": days[signal_index],
        "execution_trade_date": execution_day,
        "execution_timing": "信号日收盘后形成，下一交易日开盘执行",
        "baseline_strategy": BASELINE_STRATEGY,
        "enhanced_strategy": V6_ENHANCED_STRATEGY,
        "baseline_position": baseline_position,
        "zero_shot_score": zero_shot_score,
        "zero_shot_confidence": zero_shot_confidence,
        "quality_rule": (
            "authoritative_source AND non_GFEX AND "
            f"zero_shot_score >= {V6_MIN_ZERO_SHOT_SCORE} "
            "AND NOT uncertainty_high"
        ),
        "quality_rule_active": quality_rule_active,
        "active_text_score": text_score,
        "text_confirmed": text_confirmed,
        "enhanced_position": enhanced_position,
        "position_delta": enhanced_position - baseline_position,
        "position_range": [-1.0, 1.0],
        "formula": (
            "仅当质量规则激活且基准偏多时，"
            f"enhanced=clip(baseline+{V6_TEXT_WEIGHT}*text_score,-1,1)"
        ),
    }


def paired_block_bootstrap_v6(
    rows: list[dict[str, Any]],
    split: str,
    *,
    enhanced_strategy: str = V6_ENHANCED_STRATEGY,
    samples: int = 5000,
    block_size: int = MIN_BOOTSTRAP_DAYS,
) -> dict[str, Any]:
    baseline = {
        row["trade_date"]: float(row["net_return"])
        for row in rows
        if row["strategy"] == BASELINE_STRATEGY and row["split"] == split
    }
    enhanced = {
        row["trade_date"]: float(row["net_return"])
        for row in rows
        if row["strategy"] == enhanced_strategy and row["split"] == split
    }
    days = sorted(set(baseline) & set(enhanced))
    differences = [enhanced[day] - baseline[day] for day in days]
    observed = statistics.mean(differences) * 252 if differences else 0.0
    result = {
        "method": "moving_block_bootstrap_3_months",
        "block_size_trading_days": block_size,
        "observations": len(differences),
        "annualized_net_return_difference": observed,
    }
    if len(differences) < block_size:
        return {
            **result,
            "samples": 0,
            "ci_lower_95": 0.0,
            "ci_upper_95": 0.0,
            "conclusion": "insufficient_history",
        }
    blocks = [
        differences[index:index + block_size]
        for index in range(len(differences) - block_size + 1)
    ]
    rng = random.Random(20260815)
    estimates: list[float] = []
    for _ in range(samples):
        sample: list[float] = []
        while len(sample) < len(differences):
            sample.extend(rng.choice(blocks))
        estimates.append(statistics.mean(sample[:len(differences)]) * 252)
    estimates.sort()
    lower = estimates[int(samples * 0.025)]
    upper = estimates[min(samples - 1, int(samples * 0.975))]
    return {
        **result,
        "samples": samples,
        "ci_lower_95": lower,
        "ci_upper_95": upper,
        "conclusion": (
            "positive_increment_established"
            if observed > 0 and lower > 0
            else "trading_increment_not_established"
        ),
    }


def evaluate_prospective_decisions_v6(
    decisions: list[dict[str, str]],
    contracts: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Settle V6 positions only when they were recorded before entry open."""
    continuous = build_main_continuous(contracts)
    days = [str(row["trade_date"]) for row in continuous]
    day_positions = {day: index for index, day in enumerate(days)}
    contract_lookup = {
        (row["trade_date"], row["contract"].strip().upper()): row
        for row in contracts
    }
    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    pending = 0
    previous = {BASELINE_STRATEGY: 0.0, V6_ENHANCED_STRATEGY: 0.0}
    nav = {BASELINE_STRATEGY: 1.0, V6_ENHANCED_STRATEGY: 1.0}
    seen: set[str] = set()
    for decision in sorted(decisions, key=lambda row: row.get("signal_date", "")):
        signal_day = decision.get("signal_date", "")
        if signal_day in seen:
            invalid.append({"signal_date": signal_day, "reason": "duplicate_signal_date"})
            continue
        seen.add(signal_day)
        signal_index = day_positions.get(signal_day)
        if signal_index is None:
            invalid.append({"signal_date": signal_day, "reason": "missing_signal_market_day"})
            continue
        if signal_index + 2 >= len(days):
            pending += 1
            continue
        entry_day = days[signal_index + 1]
        exit_day = days[signal_index + 2]
        try:
            recorded_at = datetime.fromisoformat(decision.get("recorded_at", ""))
        except ValueError:
            invalid.append({"signal_date": signal_day, "reason": "invalid_recorded_at"})
            continue
        if recorded_at.date() >= _parse_day(entry_day, "entry_trade_date"):
            invalid.append({"signal_date": signal_day, "reason": "recorded_after_entry_open"})
            continue
        contract = decision.get("selected_contract", "").strip().upper()
        entry = contract_lookup.get((entry_day, contract))
        exit_row = contract_lookup.get((exit_day, contract))
        if entry is None or exit_row is None:
            invalid.append({
                "signal_date": signal_day,
                "reason": "contract_not_tradeable_through_exit",
            })
            continue
        market_return = (
            _float(exit_row["open"], "open", positive=True)
            / _float(entry["open"], "open", positive=True)
            - 1.0
        )
        positions = {
            BASELINE_STRATEGY: float(decision["baseline_position"]),
            V6_ENHANCED_STRATEGY: float(decision["enhanced_position"]),
        }
        cost_bps = float(decision.get("cost_bps", V6_COST_BPS))
        for strategy, position in positions.items():
            turnover = abs(position - previous[strategy])
            net_return = position * market_return - turnover * cost_bps / 10000.0
            nav[strategy] *= 1.0 + net_return
            rows.append({
                "trade_date": entry_day,
                "signal_date": signal_day,
                "split": "v6_prospective_oos",
                "strategy": strategy,
                "position": position,
                "market_open_return": market_return,
                "turnover": turnover,
                "cost_bps": cost_bps,
                "net_return": net_return,
                "nav": nav[strategy],
                "baseline_position": float(decision["baseline_position"]),
                "active_text_score": (
                    float(decision.get("active_text_score", 0) or 0)
                    if strategy == V6_ENHANCED_STRATEGY else 0.0
                ),
                "position_delta": (
                    float(decision.get("position_delta", 0) or 0)
                    if strategy == V6_ENHANCED_STRATEGY else 0.0
                ),
            })
            previous[strategy] = position
    return rows, {
        "recorded_decisions": len(decisions),
        "settled_decisions": len(rows) // 2,
        "pending_decisions": pending,
        "invalid_decisions": invalid,
        "latest_signal_date": max(
            (row.get("signal_date", "") for row in decisions), default=""
        ),
        "evidence_mode": "append_only_v6_pre_trade_decision_ledger",
    }


def build_report_v6(
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]],
    signals: list[dict[str, Any]],
    texts: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows, audit = strategy_rows_v6(continuous, contracts, signals, texts)
    historical = relabel_split(rows, "2025-01-01", "9999-12-31", "historical_walkforward")
    validation = relabel_split(rows, "2025-01-01", "2025-12-31", "validation")
    oos_stress = relabel_split(rows, "2026-01-01", "9999-12-31", "oos_stress")
    historical_bootstrap = paired_block_bootstrap_v6(
        historical, "historical_walkforward"
    )
    validation_bootstrap = paired_block_bootstrap_v6(validation, "validation")
    oos_bootstrap = paired_block_bootstrap_v6(oos_stress, "oos_stress")
    cost_sensitivity: list[dict[str, Any]] = []
    for cost in (2.0, V6_COST_BPS, 10.0):
        cost_rows = rows
        if cost != V6_COST_BPS:
            cost_rows, _ = strategy_rows_v6(
                continuous, contracts, signals, texts, cost_bps=cost
            )
        cost_oos = relabel_split(
            cost_rows, "2026-01-01", "9999-12-31", "oos_stress"
        )
        cost_historical = relabel_split(
            cost_rows, "2025-01-01", "9999-12-31", "historical_walkforward"
        )
        cost_sensitivity.append({
            "cost_bps": cost,
            "oos_stress_bootstrap": paired_block_bootstrap_v6(
                cost_oos, "oos_stress"
            ),
            "historical_walkforward_bootstrap": paired_block_bootstrap_v6(
                cost_historical, "historical_walkforward"
            ),
        })
    strategies = (BASELINE_STRATEGY, V6_ENHANCED_STRATEGY)
    report = {
        "version": "lithium-v6-quality-text-walkforward-v1",
        "status": "retrospective_walkforward_evaluated",
        "baseline": {
            "strategy": BASELINE_STRATEGY,
            "momentum_horizons": [20, 60, 120],
            "feature_lookback": 126,
            "target_annual_volatility": 0.25,
            "position_range": [-1.0, 1.0],
        },
        "text_alpha": {
            "strategy": V6_ENHANCED_STRATEGY,
            "rule": (
                "authoritative_source AND non_GFEX AND "
                f"zero_shot_score >= {V6_MIN_ZERO_SHOT_SCORE} "
                "AND NOT uncertainty_high"
            ),
            "window_days": V6_TEXT_WINDOW_DAYS,
            "weight": V6_TEXT_WEIGHT,
            "formula": (
                "baseline + 0.35 * text_score only when baseline > 0"
            ),
            "attribution": (
                "when the quality rule is inactive, enhanced position equals baseline exactly"
            ),
        },
        "signal_audit": audit,
        "validation_metrics": strategy_metrics(
            validation, "validation", strategies=strategies
        ),
        "validation_bootstrap": validation_bootstrap,
        "oos_stress_metrics": strategy_metrics(
            oos_stress, "oos_stress", strategies=strategies
        ),
        "oos_stress_bootstrap": oos_bootstrap,
        "historical_walkforward_metrics": strategy_metrics(
            historical, "historical_walkforward", strategies=strategies
        ),
        "historical_walkforward_bootstrap": historical_bootstrap,
        "cost_sensitivity": cost_sensitivity,
        "retrospective_increment_evidence": (
            historical_bootstrap["conclusion"] == "positive_increment_established"
        ),
        "strict_increment_established": False,
        "conclusion": (
            "历史滚动增量成立，严格前瞻增量待检验"
            if historical_bootstrap["conclusion"] == "positive_increment_established"
            else "交易增量未建立"
        ),
        "research_boundary": (
            "V6 候选是在历史结果已可见后形成，只能作为回顾性 walk-forward 证据；"
            "2026 单段仅作压力检验，不冒充新的未见 OOS。严格结论继续读取 V6 追加式前瞻账本。"
        ),
    }
    return report, rows
