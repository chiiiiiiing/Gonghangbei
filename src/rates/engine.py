"""Auditable rates research service and artifact builder."""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import math
import os
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.ai.gateway import AIServiceError
from src.rates.factors import (
    document_fingerprint,
    events_from_predicates,
    factor_scores,
    ground_predicates,
    independent_event_key,
    merge_llm_predicates,
)
from src.rates.llm import extract_with_llm
from src.rates.modeling import ROUTES, evaluate_route, live_probabilities, paired_block_bootstrap
from src.rates.rules import RULES, RULE_VERSION, activate_rules, rule_pressure
from src.rates.schema import (
    DISCLAIMER,
    ENHANCEMENT_VERSION,
    FACTOR_LABELS,
    FACTOR_NAMES,
    FLAT_THRESHOLD_BP,
    HORIZON_TRADING_DAYS,
    MINIMUM_INDEPENDENT_EVENTS,
    PREDICATES,
    RESEARCH_BOUNDARY,
    STRUCTURED_INDICATORS,
    TARGET_NAME,
    TEXT_DECAY_DAYS,
    TEXT_HALF_LIFE_DAYS,
    TEXT_OVERLAY_WEIGHT,
    RULE_LOGIT_WEIGHT,
    effective_trade_date,
    factor_dictionary,
    parse_datetime,
    validate_market_row,
    validate_structured_row,
    validate_text_row,
)


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "sample"
MARKET_PATH = DATA_DIR / "rates_market.csv"
TEXT_PATH = DATA_DIR / "rates_policy_texts.csv"
STRUCTURED_PATH = DATA_DIR / "rates_structured_data.csv"
LLM_ANNOTATION_PATH = DATA_DIR / "rates_llm_annotations.jsonl"
AUDIT_PATH = DATA_DIR / "rates_source_audit.json"
POLICY_AUDIT_PATH = DATA_DIR / "rates_policy_source_audit.json"
DAILY_FACTOR_PATH = DATA_DIR / "rates_daily_factors.csv"
EVIDENCE_PATH = DATA_DIR / "rates_evidence_audit.json"
FORECAST_PATH = DATA_DIR / "rates_forecast.json"
BACKTEST_PATH = DATA_DIR / "rates_backtest.json"
MODEL_MANIFEST_PATH = DATA_DIR / "rates_model_manifest.json"
DEMO_CASES_PATH = DATA_DIR / "rates_demo_cases.json"
REPORT_PATH = DATA_DIR / "rates_research_report.md"
REVIEW_PATH = ROOT / "data" / "runtime" / "rates_reviews.jsonl"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_signature() -> dict[str, str]:
    return {
        "rates_market.csv": _file_hash(MARKET_PATH),
        "rates_policy_texts.csv": _file_hash(TEXT_PATH),
        "rates_llm_annotations.jsonl": _file_hash(LLM_ANNOTATION_PATH),
        "rates_structured_data.csv": _file_hash(STRUCTURED_PATH),
        "rates_rule_version": RULE_VERSION,
        "rates_engine.py": _file_hash(Path(__file__)),
        "rates_factors.py": _file_hash(ROOT / "src" / "rates" / "factors.py"),
        "rates_modeling.py": _file_hash(ROOT / "src" / "rates" / "modeling.py"),
        "rates_rules.py": _file_hash(ROOT / "src" / "rates" / "rules.py"),
        "rates_schema.py": _file_hash(ROOT / "src" / "rates" / "schema.py"),
    }


def _llm_annotation_cache() -> dict[tuple[str, str], dict[str, Any]]:
    if not LLM_ANNOTATION_PATH.exists():
        return {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in LLM_ANNOTATION_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[(str(row.get("doc_id", "")), str(row.get("source_sha256", "")))] = row
    return rows


def _load() -> tuple[list[dict[str, str]], list[dict[str, str]], list[str], int]:
    market = sorted(_read_csv(MARKET_PATH), key=lambda row: row.get("trade_date", ""))
    raw_texts = sorted(_read_csv(TEXT_PATH), key=lambda row: row.get("publish_time", ""))
    errors: list[str] = []
    for index, row in enumerate(market, 2):
        try:
            validate_market_row(row)
        except (ValueError, TypeError) as exc:
            errors.append(f"rates_market.csv第{index}行：{exc}")
    seen: set[str] = set()
    texts: list[dict[str, str]] = []
    duplicates = 0
    for index, row in enumerate(raw_texts, 2):
        try:
            validate_text_row(row)
        except (ValueError, TypeError) as exc:
            errors.append(f"rates_policy_texts.csv第{index}行：{exc}")
            continue
        fingerprint = document_fingerprint(row)
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        texts.append(row)
    duplicate_dates = [day for day, count in Counter(row.get("trade_date") for row in market).items() if day and count > 1]
    if duplicate_dates:
        errors.append("rates_market.csv存在重复交易日：" + "、".join(duplicate_dates[:5]))
    return market, texts, errors, duplicates


def _load_structured() -> tuple[list[dict[str, str]], list[str]]:
    rows = _read_csv(STRUCTURED_PATH)
    errors: list[str] = []
    for index, row in enumerate(rows, 2):
        try:
            validate_structured_row(row)
        except (ValueError, TypeError) as exc:
            errors.append(f"rates_structured_data.csv第{index}行：{exc}")
    return rows, errors


def _structured_model_eligible(row: dict[str, str]) -> bool:
    """Reject retrospective reconstructions as contemporaneous model state."""
    vintage = str(row.get("vintage", "")).lower()
    return "reconstruction" not in vintage and "retrospective_snapshot" not in vintage


def _structured_status(rows: list[dict[str, str]]) -> tuple[dict[str, int], dict[str, str], str]:
    counts = Counter(row.get("indicator", "") for row in rows)
    eligible_counts = Counter(
        row.get("indicator", "") for row in rows if _structured_model_eligible(row)
    )
    indicator_counts = {name: counts[name] for name in STRUCTURED_INDICATORS}
    indicator_status = {
        name: (
            "sufficient" if eligible_counts[name]
            else "audit_only" if indicator_counts[name]
            else "missing"
        )
        for name in STRUCTURED_INDICATORS
    }
    overall = (
        "sufficient" if all(status == "sufficient" for status in indicator_status.values())
        else "partial" if rows
        else "insufficient_evidence"
    )
    return indicator_counts, indicator_status, overall


def _structured_context(
    market: list[dict[str, str]], structured: list[dict[str, str]]
) -> tuple[dict[str, dict[str, float]], dict[str, int]]:
    """Build point-in-time state and known near-term issuance features."""
    dates = [row["trade_date"] for row in market]
    by_date: dict[str, dict[str, float]] = {day: {} for day in dates}
    release_rows: list[tuple[str, str, str, str, float]] = []
    issuance_rows: list[tuple[str, str, float]] = []
    for row in structured:
        if not _structured_model_eligible(row):
            continue
        effective = effective_trade_date(row["release_time"], dates)
        if effective:
            if str(row["indicator"]) == "government_bond_issuance":
                issuance_rows.append((
                    effective, str(row.get("observation_date") or effective), float(row["value"]),
                ))
                continue
            release_rows.append((
                effective, row["release_time"], str(row["indicator"]),
                str(row.get("observation_date") or effective), float(row["value"]),
            ))
    release_rows.sort()
    latest: dict[str, tuple[str, float]] = {}
    cursor = 0
    for day in dates:
        while cursor < len(release_rows) and release_rows[cursor][0] <= day:
            _effective, _release_time, indicator, observation_date, value = release_rows[cursor]
            # Only the newest statistical period available by this date is
            # carried forward.  This prevents a 2025 retrospective table for
            # 2017--2019 from overwriting a 2025 monthly observation.
            previous = latest.get(indicator)
            if previous is None or observation_date >= previous[0]:
                latest[indicator] = (observation_date, value)
            cursor += 1
        values = {indicator: value for indicator, (_period, value) in latest.items()}
        # Issuance is a dated, known-in-advance supply event rather than a
        # persistent level. Sum plans already public for the next seven
        # calendar days (approximately five trading days), then let them expire.
        horizon_end = (date.fromisoformat(day) + timedelta(days=7)).isoformat()
        values["government_bond_issuance"] = sum(
            value for effective, issuance_date, value in issuance_rows
            if effective <= day <= issuance_date <= horizon_end
        )
        by_date[day] = values
    coverage = Counter(indicator for _effective, _release_time, indicator, _period, _value in release_rows)
    coverage["government_bond_issuance"] = len(issuance_rows)
    return by_date, dict(coverage)


def _daily_context(
    market: list[dict[str, str]], texts: list[dict[str, str]],
    structured: list[dict[str, str]] | None = None,
    text_decay_days: int = TEXT_DECAY_DAYS,
    text_half_life_days: float = TEXT_HALF_LIFE_DAYS,
) -> tuple[dict[str, dict[str, float]], dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    if text_decay_days < 1 or text_half_life_days <= 0:
        raise ValueError("文本影响窗口和半衰期必须为正数")
    if structured is None:
        structured, _structured_errors = _load_structured()
    dates = [row["trade_date"] for row in market]
    date_index = {day: index for index, day in enumerate(dates)}
    rule_entries: dict[str, list[tuple[float, set[str], float]]] = defaultdict(list)
    factor_events_by_date: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    factor_contributions: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    document_counts: Counter[str] = Counter()
    evidence_audit: list[dict[str, Any]] = []
    llm_cache = _llm_annotation_cache()
    seen_event_keys: set[str] = set()
    for document in texts:
        publication_date = parse_datetime(document["publish_time"]).date().isoformat()
        # Text signals decay over a short event window. A document published
        # before the available market sample is auditable history, not a new
        # event on the first market date in the file.
        effective = (
            effective_trade_date(document["publish_time"], dates)
            if dates and publication_date >= dates[0]
            else None
        )
        deterministic = ground_predicates(document)
        annotation = llm_cache.get((document["doc_id"], document["source_sha256"]))
        if annotation and annotation.get("used"):
            source_text = f"{document['title']}。{document['content']}"
            predicates = merge_llm_predicates(deterministic, annotation.get("predicates", []), source_text)
            llm_events = annotation.get("events", []) or []
            extraction_mode = "llm_evidence_gated"
        else:
            predicates = [{**row, "consensus": "deterministic_only"} for row in deterministic]
            llm_events = []
            extraction_mode = "deterministic_fallback"
        # Only events reconstructed from the final, evidence-gated predicates
        # may enter factor counts or model contributions. Raw LLM events are
        # retained separately for audit and can never bypass predicate gating.
        events = events_from_predicates(document, predicates)
        scores = factor_scores(predicates)
        rules = activate_rules(predicates)
        if effective:
            start = date_index[effective]
            new_event_factors: set[str] = set()
            for event in events:
                factor = str(event.get("transmission_channel", ""))
                if factor not in FACTOR_NAMES:
                    continue
                event_key = independent_event_key(event)
                if event_key not in seen_event_keys:
                    factor_events_by_date[effective][factor].add(event_key)
                    seen_event_keys.add(event_key)
                    new_event_factors.add(factor)
            rule_factors = {
                factor
                for rule in rules
                for predicate_name in rule.get("conditions", [])
                for factor in [PREDICATES.get(predicate_name).factor if PREDICATES.get(predicate_name) else ""]
                if factor
            }
            for age in range(text_decay_days):
                if start + age >= len(dates):
                    break
                trade_date = dates[start + age]
                decay = 0.5 ** (age / text_half_life_days)
                document_counts[trade_date] += 1
                for name, score in scores.items():
                    if score and name in new_event_factors:
                        factor_contributions[trade_date][name] += score * decay
                if rules and new_event_factors:
                    rule_entries[trade_date].append((rule_pressure(rules), set(rule_factors), decay))
        evidence_audit.append({
            "doc_id": document["doc_id"], "title": document["title"],
            "publish_time": document["publish_time"], "effective_trade_date": effective,
            "source_name": document["source_name"], "source_url": document["source_url"],
            "source_sha256": document["source_sha256"], "document_fingerprint": document_fingerprint(document),
            "extraction_mode": extraction_mode,
            "llm_request_id": (annotation or {}).get("metadata", {}).get("request_id", ""),
            "model_eligible": bool(effective),
            "exclusion_reason": "published_before_market_sample" if dates and publication_date < dates[0] else "",
            "llm_events": llm_events,
            "events": events,
            "independent_event_keys": sorted({independent_event_key(event) for event in events}),
            "active_predicates": [row for row in predicates if row["value"]],
            "factor_scores": scores, "triggered_rules": rules,
        })
    structured_by_date, structured_coverage = _structured_context(market, structured or [])
    factors_by_date: dict[str, dict[str, float]] = {}
    available_events: dict[str, set[str]] = defaultdict(set)
    daily_rows: list[dict[str, Any]] = []
    for trade_date in dates:
        for name in FACTOR_NAMES:
            available_events[name].update(factor_events_by_date[trade_date].get(name, set()))
        factor_status = {
            name: "sufficient" if len(available_events[name]) >= MINIMUM_INDEPENDENT_EVENTS else "insufficient_evidence"
            for name in FACTOR_NAMES
        }
        scores = {
            # tanh keeps multiple independent events additive while bounding
            # the signal.  Crucially, the event decay is no longer cancelled
            # by division through the same decay weight.
            name: round(math.tanh(factor_contributions[trade_date][name]), 8)
            if factor_status[name] == "sufficient" and factor_contributions[trade_date][name] else 0.0
            for name in FACTOR_NAMES
        }
        factors_by_date[trade_date] = scores
        eligible_rules = [entry for entry in rule_entries[trade_date] if all(
            factor_status[name] == "sufficient" for name in entry[1]
        )]
        pressure = math.tanh(sum(entry[0] * entry[2] for entry in eligible_rules)) if eligible_rules else 0.0
        daily_rows.append({
            "trade_date": trade_date, **scores, "rule_pressure": round(pressure, 8),
            "supporting_document_count": int(document_counts[trade_date]),
            "structured_observation_count": sum(1 for value in structured_by_date[trade_date].values() if value is not None),
            "independent_event_count": sum(len(available_events[name]) for name in FACTOR_NAMES),
            **{f"{name}_independent_event_count": len(available_events[name]) for name in FACTOR_NAMES},
            **{f"{name}_evidence_status": factor_status[name] for name in FACTOR_NAMES},
        })
    pressure_by_date = {row["trade_date"]: float(row["rule_pressure"]) for row in daily_rows}
    # Keep structured observations alongside the factor map for the modeling
    # layer without changing the public four-value return contract.
    for day, values in structured_by_date.items():
        factors_by_date[day]["__structured__"] = values  # type: ignore[assignment]
    return factors_by_date, pressure_by_date, evidence_audit, daily_rows


def load_status() -> dict[str, Any]:
    market, texts, errors, duplicates = _load()
    structured, structured_errors = _load_structured()
    market_audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8")) if AUDIT_PATH.exists() else {}
    policy_audit = json.loads(POLICY_AUDIT_PATH.read_text(encoding="utf-8")) if POLICY_AUDIT_PATH.exists() else {}
    sources = Counter(row["source_name"] for row in texts)
    llm_cache = _llm_annotation_cache()
    current_text_keys = {(row["doc_id"], row["source_sha256"]) for row in texts}
    current_annotations = [row for key, row in llm_cache.items() if key in current_text_keys]
    llm_usable = sum(bool(row.get("used")) for row in current_annotations)
    structured_counts, structured_indicator_status, structured_status = _structured_status(structured)
    return {
        "version": "rates-text-factor-v3",
        "target": TARGET_NAME, "horizon_trading_days": HORIZON_TRADING_DAYS,
        "flat_threshold_bp": FLAT_THRESHOLD_BP,
        "data_ready": len(market) >= 257 and not errors,
        "market_rows": len(market), "text_rows": len(texts), "deduplicated_text_rows": duplicates,
        "first_trade_date": market[0]["trade_date"] if market else None,
        "latest_trade_date": market[-1]["trade_date"] if market else None,
        "liquidity_series": market[-1]["dr007_proxy_name"] if market else None,
        "source_counts": dict(sources), "factor_dictionary": factor_dictionary(),
        "rule_count": len(RULES), "rule_version": RULE_VERSION,
        "enhancement_version": ENHANCEMENT_VERSION,
        "llm_annotations": len(current_annotations), "llm_usable_annotations": llm_usable,
        "llm_coverage": round(llm_usable / len(texts), 4) if texts else 0.0,
        "structured_rows": len(structured), "structured_indicator_counts": structured_counts,
        "structured_indicator_status": structured_indicator_status,
        "structured_data_status": structured_status if not structured_errors else "invalid",
        "structured_data_ready": bool(structured) and not structured_errors,
        "data_errors": errors + structured_errors,
        "source_audit": {"market": market_audit, "policy": policy_audit},
        "source_signature": _source_signature(),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def _compute_forecast(as_of: str | None = None, horizon: int = HORIZON_TRADING_DAYS) -> dict[str, Any]:
    market, texts, errors, _duplicates = _load()
    if horizon != HORIZON_TRADING_DAYS:
        raise ValueError("当前冻结模型仅支持5个交易日预测窗口")
    if as_of:
        try:
            requested_date = date.fromisoformat(as_of)
        except ValueError as exc:
            raise ValueError("as_of必须是YYYY-MM-DD格式") from exc
        if requested_date.isoformat() != as_of:
            raise ValueError("as_of必须是YYYY-MM-DD格式")
        if market and not (market[0]["trade_date"] <= as_of <= market[-1]["trade_date"]):
            raise ValueError(
                f"as_of必须位于市场样本区间{market[0]['trade_date']}至{market[-1]['trade_date']}"
            )
        market = [row for row in market if row["trade_date"] <= as_of]
        texts = [row for row in texts if parse_datetime(row["publish_time"]).date().isoformat() <= as_of]
    if errors or not market:
        return {
            "status": "research_evidence_insufficient", "reason": "；".join(errors) or "尚未导入官方市场数据",
            "probabilities": {"down": 1 / 3, "flat": 1 / 3, "up": 1 / 3},
            "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
        }
    structured, structured_errors = _load_structured()
    structured_counts, structured_indicator_status, structured_status = _structured_status(structured)
    if as_of:
        structured = [row for row in structured if parse_datetime(row["release_time"]).date().isoformat() <= as_of]
    factors, pressure, audit, daily = _daily_context(market, texts, structured)
    model = live_probabilities(market, factors, pressure)
    latest = market[-1]
    latest_factors = factors.get(latest["trade_date"], {name: 0.0 for name in FACTOR_NAMES})
    liquidity_values = [float(row["dr007_proxy"]) for row in market[-20:]]
    liquidity_mean = sum(liquidity_values) / len(liquidity_values)
    latest_liquidity = float(latest["dr007_proxy"])
    state = "偏紧" if latest_liquidity > liquidity_mean + 0.05 else "偏松" if latest_liquidity < liquidity_mean - 0.05 else "均衡"
    effective_audit = [row for row in audit if row["effective_trade_date"] and row["effective_trade_date"] <= latest["trade_date"]]
    recent_evidence = sorted(effective_audit, key=lambda row: row["effective_trade_date"])[-8:]
    latest_index = len(market) - 1
    active_evidence = [
        row for row in effective_audit
        if 0 <= latest_index - next(
            index for index, market_row in enumerate(market)
            if market_row["trade_date"] == row["effective_trade_date"]
        ) < TEXT_DECAY_DAYS
    ]
    current_rules = [rule for row in active_evidence for rule in row["triggered_rules"]]
    latest_daily = next((row for row in daily if row["trade_date"] == latest["trade_date"]), {})
    return {
        "status": "model_estimate" if model["data_sufficient"] else "research_evidence_insufficient",
        "as_of": latest["trade_date"], "horizon_trading_days": horizon,
        "target": TARGET_NAME, "flat_threshold_bp": FLAT_THRESHOLD_BP, **model,
        "bond_price_direction": {"up": "债券价格偏弱", "flat": "债券价格震荡", "down": "债券价格偏强"}.get(model["predicted_label"], "证据不足"),
        "market_snapshot": {
            "cgb_10y_yield": float(latest["cgb_10y_yield"]), "dr007_proxy": latest_liquidity,
            "dr007_state": state, "dr007_proxy_name": latest["dr007_proxy_name"],
            "cgb_source_url": latest["cgb_source_url"], "liquidity_source_url": latest["liquidity_source_url"],
        },
        "factor_scores": [{"name": name, "label": FACTOR_LABELS[name], "score": round(latest_factors.get(name, 0.0), 6)} for name in FACTOR_NAMES],
        "factor_evidence_status": {
            name: latest_daily.get(f"{name}_evidence_status", "insufficient_evidence") for name in FACTOR_NAMES
        },
        "structured_data_status": structured_status if not structured_errors else "invalid",
        "structured_indicator_counts": structured_counts,
        "structured_indicator_status": structured_indicator_status,
        "triggered_rules": current_rules[-8:], "evidence": recent_evidence,
        "source_signature": _source_signature(),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def load_forecast(as_of: str | None = None, horizon: int = HORIZON_TRADING_DAYS) -> dict[str, Any]:
    if not as_of and horizon == HORIZON_TRADING_DAYS and FORECAST_PATH.exists():
        cached = json.loads(FORECAST_PATH.read_text(encoding="utf-8"))
        if cached.get("source_signature") == _source_signature():
            return cached
    return _compute_forecast(as_of, horizon)


def _compute_backtest() -> dict[str, Any]:
    market, texts, errors, _duplicates = _load()
    if errors or len(market) < 257:
        return {
            "status": "research_evidence_insufficient", "reason": "；".join(errors) or "市场样本不足257个交易日",
            "routes": [], "increment_conclusion": "文本预测增量尚未建立",
            "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
        }
    structured, structured_errors = _load_structured()
    structured_counts, structured_indicator_status, structured_status = _structured_status(structured)
    factors, pressure, _audit, _daily = _daily_context(market, texts, structured)
    routes = [evaluate_route(market, factors, pressure, route) for route in ROUTES]
    baseline, text_enhanced, enhanced = routes[0], routes[2], routes[-1]
    holdout_key = "retrospective_holdout_2025_latest"
    holdout_baseline = {"timeline": [row for row in baseline["timeline"] if row["period"] == holdout_key]}
    holdout_enhanced = {"timeline": [row for row in enhanced["timeline"] if row["period"] == holdout_key]}
    bootstrap = paired_block_bootstrap(holdout_baseline, holdout_enhanced)
    baseline_holdout = next(row for row in baseline["period_metrics"] if row["period"] == holdout_key)
    enhanced_holdout = next(row for row in enhanced["period_metrics"] if row["period"] == holdout_key)
    text_holdout = next(row for row in text_enhanced["period_metrics"] if row["period"] == holdout_key)
    fusion_by_date = {row["as_of"]: row for row in text_enhanced.get("timeline", []) if row["period"] == holdout_key}
    rules_by_date = {row["as_of"]: row for row in enhanced.get("timeline", []) if row["period"] == holdout_key}
    comparable_dates = sorted(set(fusion_by_date) & set(rules_by_date))
    rule_active_dates = [
        day for day in comparable_dates if abs(float(rules_by_date[day].get("rule_pressure_applied", 0.0))) > 1e-12
    ]
    rule_probability_change = (
        sum(
            sum(abs(float(rules_by_date[day]["probabilities"][label]) - float(fusion_by_date[day]["probabilities"][label])) for label in ("down", "flat", "up")) / 2
            for day in rule_active_dates
        ) / len(rule_active_dates)
        if rule_active_dates else 0.0
    )
    text_accuracy_difference = float(text_holdout.get("accuracy") or 0.0) - float(baseline_holdout.get("accuracy") or 0.0)
    text_macro_f1_difference = float(text_holdout.get("macro_f1") or 0.0) - float(baseline_holdout.get("macro_f1") or 0.0)
    rule_changed_predictions = sum(
        fusion_by_date[day]["predicted"] != rules_by_date[day]["predicted"] for day in rule_active_dates
    )
    incremental = bool(
        enhanced_holdout.get("macro_f1") is not None
        and baseline_holdout.get("macro_f1") is not None
        and enhanced_holdout["macro_f1"] > baseline_holdout["macro_f1"]
        and bootstrap.get("stable")
    )
    positive_point_estimate = bool(text_accuracy_difference > 0 and text_macro_f1_difference > 0)
    if incremental:
        increment_conclusion = "回顾性时间留出文本预测增量成立"
    elif positive_point_estimate:
        increment_conclusion = "回顾性时间留出出现正向增量点估计，但统计证据尚不足"
    else:
        increment_conclusion = "回顾性时间留出文本预测增量尚未建立"
    return {
        "status": "evaluated", "target": TARGET_NAME,
        "structured_data_status": structured_status if not structured_errors else "invalid",
        "structured_indicator_counts": structured_counts,
        "structured_indicator_status": structured_indicator_status,
        "split_policy": "purged_non_overlapping_756_day_rolling_window_no_shuffle",
        "periods": {
            "discovery": "2018-01-01至2022-12-31", "validation": "2023-01-01至2024-12-31",
            "retrospective_holdout": "2025-01-01至最新（规则建立后回看，不是真正前瞻OOS）",
            "prospective_oos": "增强参数于2026-09-07冻结；冻结后的首个完整5交易日标签待累计",
        },
        "routes": routes, "holdout_increment_bootstrap": bootstrap,
        "enhancement_diagnostics": {
            "period": holdout_key,
            "text_overlay": {
                "accuracy_difference_vs_market": round(text_accuracy_difference, 6),
                "macro_f1_difference_vs_market": round(text_macro_f1_difference, 6),
                "effect_observed": positive_point_estimate,
            },
            "rule_prior": {
                "active_observations": len(rule_active_dates),
                "mean_total_variation_probability_change": round(rule_probability_change, 8),
                "changed_predictions": rule_changed_predictions,
                "effect_observed": bool(rule_probability_change > 0),
            },
        },
        "increment_established": incremental,
        "increment_conclusion": increment_conclusion,
        "research_warning": (
            "2025年至最新仅为回顾性时间留出，不是真正前瞻OOS；增强参数于2026-09-07冻结，"
            "真正前瞻OOS须从冻结后的新数据累计。回测只衡量方向分类，不代表可交易收益。"
        ),
        "source_signature": _source_signature(),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def load_backtest() -> dict[str, Any]:
    if BACKTEST_PATH.exists():
        cached = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
        if cached.get("source_signature") == _source_signature():
            return cached
    return _compute_backtest()


def analyze_document(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: str(value or "").strip() for key, value in payload.items()}
    for field in ("title", "content", "source_name", "publish_time", "source_url"):
        if not normalized.get(field):
            raise ValueError(f"缺少字段：{field}")
    publish_time = parse_datetime(normalized["publish_time"])
    if publish_time > datetime.now() and (publish_time - datetime.now()).total_seconds() > 300:
        raise ValueError("publish_time不能晚于当前时间")
    if not normalized["source_url"].startswith(("https://", "http://")):
        raise ValueError("source_url必须以http://或https://开头")
    document = {
        "doc_id": "live-" + hashlib.sha256((normalized["title"] + normalized["content"]).encode()).hexdigest()[:12],
        "title": normalized["title"], "content": normalized["content"],
        "source_name": normalized["source_name"], "source_url": normalized["source_url"],
        "publish_time": normalized["publish_time"],
    }
    deterministic = ground_predicates(document)
    request_api_key = normalized.get("api_key", "")
    allow_server_llm = os.getenv("ALPHALENS_ALLOW_SERVER_LLM", "").strip().lower() == "true"
    if request_api_key or allow_server_llm:
        try:
            llm = extract_with_llm(document, request_api_key)
        except AIServiceError as exc:
            llm = {"used": False, "reason": str(exc), "events": [], "predicates": [], "metadata": {}}
    else:
        llm = {
            "used": False,
            "reason": "未提供当次请求API Key，公开服务使用确定性降级",
            "events": [], "predicates": [], "metadata": {},
        }
    source_text = f"{document['title']}。{document['content']}"
    predicates = merge_llm_predicates(deterministic, llm.get("predicates", []), source_text) if llm.get("used") else [
        {**row, "consensus": "deterministic_only"} for row in deterministic
    ]
    scores = factor_scores(predicates)
    activated = activate_rules(predicates)
    # Present the same accepted evidence chain used by the model. Raw LLM
    # events remain available under ``llm_analysis`` for audit purposes.
    events = events_from_predicates(document, predicates)
    baseline = load_forecast()
    baseline_probs = dict(baseline.get("probabilities", {"down": 1 / 3, "flat": 1 / 3, "up": 1 / 3}))
    market, texts, errors, _duplicates = _load()
    if errors or not market:
        updated = baseline_probs
        marginal_model = {"data_sufficient": False, "reason": "市场数据不可用"}
        scenario_effective_date = None
        scenario_active = False
    else:
        structured, _structured_errors = _load_structured()
        historical_factors, historical_pressure, _audit, _daily = _daily_context(market, texts, structured)
        latest_date = market[-1]["trade_date"]
        trade_dates = [row["trade_date"] for row in market]
        scenario_publication_date = publish_time.date().isoformat()
        scenario_effective_date = (
            effective_trade_date(normalized["publish_time"], trade_dates)
            if trade_dates and scenario_publication_date >= trade_dates[0]
            else None
        )
        scenario_alignment = "observed_trade_date"
        if scenario_effective_date in trade_dates:
            scenario_age = len(trade_dates) - 1 - trade_dates.index(str(scenario_effective_date))
            scenario_active = 0 <= scenario_age < TEXT_DECAY_DAYS
        else:
            # A newly published document can arrive after the latest loaded
            # market close (especially on a weekend).  Treat it as an age-zero
            # scenario for the next available close, but only while the market
            # snapshot is fresh enough to support a meaningful comparison.
            latest_market_date = date.fromisoformat(latest_date)
            days_after_snapshot = (publish_time.date() - latest_market_date).days
            scenario_active = 0 < days_after_snapshot <= 7
            scenario_age = 0
            if scenario_active:
                scenario_effective_date = scenario_publication_date
                scenario_alignment = "next_available_trade_date_scenario"
            else:
                scenario_alignment = "outside_loaded_market_window"
        scenario_factors = {day: dict(values) for day, values in historical_factors.items()}
        latest_factors = scenario_factors.setdefault(latest_date, {name: 0.0 for name in FACTOR_NAMES})
        if scenario_active:
            decay = 0.5 ** (scenario_age / TEXT_HALF_LIFE_DAYS)
            for name in FACTOR_NAMES:
                current = float(latest_factors.get(name, 0.0))
                addition = float(scores.get(name, 0.0)) * decay
                raw_current = math.atanh(max(min(current, 1 - 1e-12), -1 + 1e-12))
                latest_factors[name] = math.tanh(raw_current + addition)
        scenario_pressure = dict(historical_pressure)
        current_pressure = float(scenario_pressure.get(latest_date, 0.0))
        added_pressure = float(rule_pressure(activated)) * decay if scenario_active else 0.0
        raw_current_pressure = math.atanh(max(min(current_pressure, 1 - 1e-12), -1 + 1e-12))
        scenario_pressure[latest_date] = math.tanh(raw_current_pressure + added_pressure)
        marginal_model = live_probabilities(market, scenario_factors, scenario_pressure)
        updated = dict(marginal_model.get("probabilities", baseline_probs))
    factor_rows = [{"name": name, "label": FACTOR_LABELS[name], "score": scores[name]} for name in FACTOR_NAMES]
    return {
        "analysis_type": "incremental_single_text",
        "document": {key: document[key] for key in document if key != "content"},
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "llm_analysis": llm, "events": events, "predicates": predicates,
        "factor_scores": factor_rows, "triggered_rules": activated,
        "baseline_forecast": baseline_probs, "updated_forecast": updated,
        "probability_delta": {label: round(updated[label] - baseline_probs[label], 8) for label in updated},
        "marginal_model": {
            "route": "fusion_rules", "same_frozen_training_sample": True,
            "effective_trade_date": scenario_effective_date, "active_in_latest_window": scenario_active,
            "scenario_alignment": scenario_alignment,
            "data_sufficient": marginal_model.get("data_sufficient", False),
            "feature_contributions": marginal_model.get("feature_contributions", []),
            "probability_decomposition": marginal_model.get("probability_decomposition", {}),
        },
        "evidence_chain": {
            "document_id": document["doc_id"], "event_ids": [row.get("event_id", "llm-event") for row in events],
            "active_predicates": [row["predicate_name"] for row in predicates if row["value"]],
            "nonzero_factors": [row["name"] for row in factor_rows if row["score"]],
            "triggered_rules": [row["rule_id"] for row in activated], "prediction_target": TARGET_NAME,
        },
        "interpretation": (
            "该文本发布时间位于最新预测的5交易日衰减窗口之外，因此只保留历史证据分析，不改变当前概率。"
            if not scenario_active else
            "该结果仅表示单篇文本对现有五日预测的边际影响，不是独立市场预测。"
        ),
        "credential_retained": False, "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def append_review(payload: dict[str, Any]) -> dict[str, Any]:
    document_id = str(payload.get("document_id", "")).strip()
    decision = str(payload.get("decision", "")).strip()
    if not document_id or decision not in {"approved", "rejected", "needs_revision"}:
        raise ValueError("document_id必填，decision仅支持approved/rejected/needs_revision")
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    row = {
        "review_id": uuid.uuid4().hex[:16],
        "document_id": document_id, "decision": decision,
        "reviewer": str(payload.get("reviewer", "人工审核员")).strip()[:100] or "人工审核员",
        "comment": str(payload.get("comment", "")).strip()[:1000], "reviewed_at": reviewed_at,
        "model_version": "rates-text-factor-v3", "immutable": True,
    }
    with REVIEW_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def load_reviews(limit: int = 100) -> list[dict[str, Any]]:
    if not REVIEW_PATH.exists():
        return []
    rows = [json.loads(line) for line in REVIEW_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-max(1, min(limit, 500)):]


def load_evidence(limit: int = 100) -> dict[str, Any]:
    if EVIDENCE_PATH.exists():
        payload = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        if payload.get("source_signature") == _source_signature():
            payload["documents"] = payload.get("documents", [])[-max(1, min(limit, 500)):]
            return payload
    market, texts, errors, _duplicates = _load()
    if errors:
        return {"documents": [], "errors": errors, "disclaimer": DISCLAIMER}
    structured, _structured_errors = _load_structured()
    _factors, _pressure, evidence, _daily = _daily_context(market, texts, structured)
    return {
        "version": "rates-evidence-v2", "source_signature": _source_signature(),
        "documents": evidence[-max(1, min(limit, 500)):], "disclaimer": DISCLAIMER,
    }


def load_demo_cases() -> dict[str, Any]:
    if DEMO_CASES_PATH.exists():
        return json.loads(DEMO_CASES_PATH.read_text(encoding="utf-8"))
    return {"cases": [], "offline_ready": False, "disclaimer": DISCLAIMER}


def load_report() -> str:
    if REPORT_PATH.exists() and MODEL_MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}
        if manifest.get("source_signature") == _source_signature():
            return REPORT_PATH.read_text(encoding="utf-8")
    status = load_status()
    return _research_report(
        load_forecast(), load_backtest(), status["market_rows"], status["text_rows"], status["structured_rows"]
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_rates_outputs() -> dict[str, Any]:
    market, texts, errors, _duplicates = _load()
    if errors:
        raise ValueError("；".join(errors))
    structured, structured_errors = _load_structured()
    structured_counts, structured_indicator_status, structured_status = _structured_status(structured)
    factors, pressure, evidence, daily = _daily_context(market, texts, structured)
    final_daily = daily[-1] if daily else {}
    factor_evidence = {
        name: {
            "independent_event_count": int(final_daily.get(f"{name}_independent_event_count", 0)),
            "minimum_required": MINIMUM_INDEPENDENT_EVENTS,
            "status": final_daily.get(f"{name}_evidence_status", "insufficient_evidence"),
        }
        for name in FACTOR_NAMES
    }
    forecast = _compute_forecast()
    backtest = _compute_backtest()
    with DAILY_FACTOR_PATH.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "trade_date", *FACTOR_NAMES, "rule_pressure", "supporting_document_count",
            "structured_observation_count", "independent_event_count",
            *[f"{name}_independent_event_count" for name in FACTOR_NAMES],
            *[f"{name}_evidence_status" for name in FACTOR_NAMES],
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(daily)
    _write_json(EVIDENCE_PATH, {
        "version": "rates-evidence-v2", "source_signature": _source_signature(),
        "documents": evidence, "disclaimer": DISCLAIMER,
    })
    _write_json(FORECAST_PATH, forecast)
    artifact_backtest = copy.deepcopy(backtest)
    for route in artifact_backtest.get("routes", []):
        timeline = route.get("timeline", [])
        route["timeline_total"] = len(timeline)
        route["timeline"] = timeline[-120:]
        route["timeline_retained"] = len(route["timeline"])
    _write_json(BACKTEST_PATH, artifact_backtest)
    manifest = {
        "version": "rates-model-v3", "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": TARGET_NAME, "horizon_trading_days": HORIZON_TRADING_DAYS,
        "flat_threshold_bp": FLAT_THRESHOLD_BP, "routes": list(ROUTES),
        "split_policy": backtest.get("split_policy"), "periods": backtest.get("periods"),
        "text_decay_days": TEXT_DECAY_DAYS, "text_half_life_days": TEXT_HALF_LIFE_DAYS,
        "text_overlay_weight": TEXT_OVERLAY_WEIGHT, "rule_logit_weight": RULE_LOGIT_WEIGHT,
        "enhancement_policy": "market_anchored_text_overlay_then_rule_prior",
        "minimum_independent_events_per_factor": MINIMUM_INDEPENDENT_EVENTS,
        "enhancement_version": ENHANCEMENT_VERSION,
        "factor_evidence": factor_evidence,
        "structured_rows": len(structured), "structured_data_errors": structured_errors,
        "structured_data_status": structured_status if not structured_errors else "invalid",
        "structured_indicator_counts": structured_counts,
        "structured_indicator_status": structured_indicator_status,
        "evaluation_stride_days": HORIZON_TRADING_DAYS,
        "rule_version": RULE_VERSION, "source_signature": _source_signature(),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }
    _write_json(MODEL_MANIFEST_PATH, manifest)
    demo_date = market[-1]["trade_date"] if market else date.today().isoformat()
    demo_cases = [
        {
            "case_id": "pboc-liquidity-injection", "title": "公开市场逆回购操作",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性，保持银行体系流动性合理充裕。",
            "source_name": "中国人民银行", "publish_time": f"{demo_date}T09:30:00",
            "source_url": "https://www.pbc.gov.cn/", "expected_factor": "liquidity",
        },
        {
            "case_id": "growth-inflation-up", "title": "经济运行和价格水平回升",
            "content": "经济运行延续回升态势，PMI扩张，CPI同比回升，物价上涨压力有所增加。",
            "source_name": "国家统计局", "publish_time": f"{demo_date}T10:00:00",
            "source_url": "https://www.stats.gov.cn/", "expected_rule": "R-GROWTH-INF-01",
        },
        {
            "case_id": "bond-supply-tight-funding", "title": "政府债发行与资金面",
            "content": "政府债券加快发行并形成集中供给，DR007上行，银行间资金面偏紧。",
            "source_name": "财政部", "publish_time": f"{demo_date}T16:00:00",
            "source_url": "https://www.mof.gov.cn/", "expected_rule": "R-SUPPLY-LIQ-02",
        },
    ]
    _write_json(DEMO_CASES_PATH, {"cases": demo_cases, "offline_ready": True, "disclaimer": DISCLAIMER})
    report = _research_report(forecast, backtest, len(market), len(texts), len(structured))
    REPORT_PATH.write_text(report, encoding="utf-8")
    return {
        "market_rows": len(market), "text_rows": len(texts), "structured_rows": len(structured),
        "factor_rows": len(daily),
        "backtest_observations": backtest.get("routes", [{}])[-1].get("observations", 0),
        "increment_conclusion": backtest.get("increment_conclusion"),
    }


def _research_report(
    forecast: dict[str, Any], backtest: dict[str, Any], market_rows: int, text_rows: int,
    structured_rows: int = 0,
) -> str:
    probabilities = forecast.get("probabilities", {})
    direction_labels = {"down": "收益率下行", "flat": "震荡", "up": "收益率上行"}
    snapshot = forecast.get("market_snapshot", {})
    lines = [
        "# AlphaLens利率债每日投研报告", "",
        f"- 数据截至：{forecast.get('as_of', '证据不足')}",
        f"- 研究目标：未来{HORIZON_TRADING_DAYS}个交易日10年期国债收益率方向",
        f"- 当前判断：{direction_labels.get(forecast.get('predicted_label'), '证据不足')}",
        f"- 概率：下行{probabilities.get('down', 0):.1%} / 震荡{probabilities.get('flat', 0):.1%} / 上行{probabilities.get('up', 0):.1%}",
        f"- 债券价格方向：{forecast.get('bond_price_direction', '证据不足')}",
        f"- 样本：{market_rows}个交易日，{text_rows}篇去重政策文本，{structured_rows}条结构化观测", "",
        "## 市场与流动性", "",
        f"- 10年期国债收益率：{snapshot.get('cgb_10y_yield', '证据不足')}%",
        f"- {snapshot.get('dr007_proxy_name', 'FDR007_FIXING')}：{snapshot.get('dr007_proxy', '证据不足')}%",
        f"- 流动性状态：{snapshot.get('dr007_state', '证据不足')}", "",
        "## 六类文本因子", "",
        "| 因子 | 收益率压力分数 |", "| --- | ---: |",
    ]
    for row in forecast.get("factor_scores", []):
        status = forecast.get("factor_evidence_status", {}).get(row.get("name"))
        display_score = "证据不足" if status == "insufficient_evidence" else f"{float(row.get('score', 0)):+.4f}"
        lines.append(f"| {row.get('label', row.get('name', ''))} | {display_score} |")
    lines.extend(["", "## 当前生效规则", ""])
    if forecast.get("triggered_rules"):
        lines.extend(
            f"- `{row['rule_id']}`：{row['description']}（权重 {float(row.get('weight', 0)):.2f}）"
            for row in forecast["triggered_rules"]
        )
    else:
        lines.append("- 当前5交易日衰减窗口没有命中冻结规则。")
    lines.extend([
        "", "## 模型评估", "",
        "| 路线 | 回顾性时间留出观测 | Accuracy | Macro-F1 | AUC |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for route in backtest.get("routes", []):
        holdout = next((
            row for row in route.get("period_metrics", [])
            if row.get("period") == "retrospective_holdout_2025_latest"
        ), {})
        if not holdout:
            continue
        lines.append(
            f"| {route.get('route_label', route.get('route', ''))} | {holdout.get('observations', 0)} | "
            f"{float(holdout.get('accuracy') or 0):.2%} | {float(holdout.get('macro_f1') or 0):.4f} | "
            f"{float(holdout.get('macro_auc_ovr') or 0):.4f} |"
        )
    bootstrap = backtest.get("holdout_increment_bootstrap", {})
    diagnostics = backtest.get("enhancement_diagnostics", {})
    text_effect = diagnostics.get("text_overlay", {})
    rule_effect = diagnostics.get("rule_prior", {})
    lines.extend([
        "", f"- 结论：{backtest.get('increment_conclusion', '尚未评估')}",
        f"- 文本叠加相对市场基线：准确率 {float(text_effect.get('accuracy_difference_vs_market') or 0):+.2%}，Macro-F1 {float(text_effect.get('macro_f1_difference_vs_market') or 0):+.4f}",
        f"- 规则先验：{int(rule_effect.get('active_observations') or 0)} 个时间留出观测生效，平均概率改变量 {float(rule_effect.get('mean_total_variation_probability_change') or 0):.2%}，改变 {int(rule_effect.get('changed_predictions') or 0)} 次最终分类",
        f"- 规则增强相对市场基线的回顾性时间留出准确率差：{float(bootstrap.get('accuracy_difference') or 0):+.2%}",
        f"- 20个交易日移动区块Bootstrap（{bootstrap.get('block_observations', 0)}个相邻评估观测）95%区间：[{float(bootstrap.get('ci_lower_95') or 0):.2%}, {float(bootstrap.get('ci_upper_95') or 0):.2%}]",
        f"- 切分：{backtest.get('split_policy', '尚未评估')}",
        f"- 注意：{backtest.get('research_warning', '')}", "",
        "## 结构化数据覆盖", "",
        f"- 状态：{forecast.get('structured_data_status', 'insufficient_evidence')}",
        f"- 指标条数：{json.dumps(forecast.get('structured_indicator_counts', {}), ensure_ascii=False, sort_keys=True)}",
        f"- 缺失或仅审计指标：{', '.join(name for name, status in forecast.get('structured_indicator_status', {}).items() if status != 'sufficient') or '无'}", "",
        "## 数据来源与研究边界", "",
        f"- 中债收益率曲线：{snapshot.get('cgb_source_url', '未提供')}",
        f"- 银行间流动性代理：{snapshot.get('liquidity_source_url', '未提供')}",
        "- FDR007定盘利率仅作DR007历史代理；结构化序列还需通过历史vintage门槛，回顾性快照仅审计；因子事件数少于5个时标记为证据不足并不进入模型。",
        "", RESEARCH_BOUNDARY, "", DISCLAIMER, "",
    ])
    return "\n".join(lines)
