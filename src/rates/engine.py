"""Read-only application service for the rates research MVP."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.ai.gateway import AIServiceError
from src.rates.factors import factor_scores, ground_predicates, merge_llm_predicates
from src.rates.llm import extract_with_llm
from src.rates.modeling import ROUTES, evaluate_route, live_probabilities
from src.rates.rules import activate_rules, rule_pressure
from src.rates.schema import (
    DISCLAIMER, FACTOR_LABELS, FACTOR_NAMES, FLAT_THRESHOLD_BP,
    HORIZON_TRADING_DAYS, RESEARCH_BOUNDARY, TARGET_NAME,
    effective_trade_date, validate_market_row, validate_text_row,
)


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "sample"
MARKET_PATH = DATA_DIR / "rates_market.csv"
TEXT_PATH = DATA_DIR / "rates_policy_texts.csv"
AUDIT_PATH = DATA_DIR / "rates_source_audit.json"
REVIEW_PATH = ROOT / "data" / "runtime" / "rates_reviews.jsonl"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load() -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    market = sorted(_read_csv(MARKET_PATH), key=lambda row: row.get("trade_date", ""))
    texts = sorted(_read_csv(TEXT_PATH), key=lambda row: row.get("publish_time", ""))
    errors: list[str] = []
    for index, row in enumerate(market, 2):
        try:
            validate_market_row(row)
        except (ValueError, TypeError) as exc:
            errors.append(f"rates_market.csv 第{index}行：{exc}")
    for index, row in enumerate(texts, 2):
        try:
            validate_text_row(row)
        except (ValueError, TypeError) as exc:
            errors.append(f"rates_policy_texts.csv 第{index}行：{exc}")
    return market, texts, errors


def _daily_context(
    market: list[dict[str, str]], texts: list[dict[str, str]]
) -> tuple[dict[str, dict[str, float]], dict[str, float], list[dict[str, Any]]]:
    dates = [row["trade_date"] for row in market]
    factor_parts: dict[str, list[dict[str, float]]] = defaultdict(list)
    rules_by_date: dict[str, list[float]] = defaultdict(list)
    audit: list[dict[str, Any]] = []
    for document in texts:
        effective = effective_trade_date(document["publish_time"], dates)
        predicates = ground_predicates(document)
        scores = factor_scores(predicates)
        rules = activate_rules(predicates)
        if effective:
            factor_parts[effective].append(scores)
            rules_by_date[effective].append(rule_pressure(rules))
        audit.append({
            "doc_id": document["doc_id"],
            "title": document["title"],
            "source_name": document["source_name"],
            "publish_time": document["publish_time"],
            "effective_trade_date": effective,
            "active_predicates": [row["predicate_name"] for row in predicates if row["value"]],
            "evidence": [row["evidence_text"] for row in predicates if row["value"]],
            "triggered_rules": [row["rule_id"] for row in rules],
            "source_url": document["source_url"], "source_sha256": document["source_sha256"],
        })
    factors_by_date: dict[str, dict[str, float]] = {}
    for trade_date, parts in factor_parts.items():
        factors_by_date[trade_date] = {
            name: sum(part.get(name, 0.0) for part in parts) / len(parts)
            for name in FACTOR_NAMES
        }
    pressure = {
        trade_date: sum(values) / len(values) for trade_date, values in rules_by_date.items()
    }
    return factors_by_date, pressure, audit


def load_status() -> dict[str, Any]:
    market, texts, errors = _load()
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8")) if AUDIT_PATH.exists() else {}
    return {
        "version": "rates-text-factor-submission-v1",
        "target": TARGET_NAME,
        "horizon_trading_days": HORIZON_TRADING_DAYS,
        "flat_threshold_bp": FLAT_THRESHOLD_BP,
        "data_ready": len(market) >= 25 and not errors,
        "model_status": {
            "market_model": "ready" if len(market) >= 25 and not errors else "insufficient_data",
            "text_increment_validation": "pending_more_text_coverage",
        },
        "market_rows": len(market), "text_rows": len(texts),
        "first_trade_date": market[0]["trade_date"] if market else None,
        "latest_trade_date": market[-1]["trade_date"] if market else None,
        "liquidity_series": market[-1]["dr007_proxy_name"] if market else None,
        "data_errors": errors,
        "source_audit": audit,
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def load_forecast(as_of: str | None = None, horizon: int = HORIZON_TRADING_DAYS) -> dict[str, Any]:
    market, texts, errors = _load()
    if horizon != HORIZON_TRADING_DAYS:
        raise ValueError("首版仅支持5个交易日预测窗口")
    if as_of:
        date.fromisoformat(as_of)
        market = [row for row in market if row["trade_date"] <= as_of]
        texts = [row for row in texts if row["publish_time"][:10] <= as_of]
    if errors or not market:
        return {
            "status": "research_evidence_insufficient", "reason": "；".join(errors) or "尚未导入官方市场数据",
            "probabilities": {"down": 1 / 3, "flat": 1 / 3, "up": 1 / 3},
            "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
        }
    factors, pressure, audit = _daily_context(market, texts)
    model = live_probabilities(market, factors, pressure)
    latest = market[-1]
    latest_factors = factors.get(latest["trade_date"], {name: 0.0 for name in FACTOR_NAMES})
    liquidity_values = [float(row["dr007_proxy"]) for row in market[-20:]]
    liquidity_mean = sum(liquidity_values) / len(liquidity_values)
    latest_liquidity = float(latest["dr007_proxy"])
    state = "偏紧" if latest_liquidity > liquidity_mean + 0.05 else "偏松" if latest_liquidity < liquidity_mean - 0.05 else "中性"
    return {
        "status": "model_estimate" if model["data_sufficient"] else "research_evidence_insufficient",
        "as_of": latest["trade_date"], "horizon_trading_days": horizon,
        "target": TARGET_NAME, "flat_threshold_bp": FLAT_THRESHOLD_BP,
        **model,
        "bond_price_direction": {"up": "债券价格偏弱", "flat": "债券价格震荡", "down": "债券价格偏强"}.get(model["predicted_label"], "证据不足"),
        "market_snapshot": {
            "cgb_10y_yield": float(latest["cgb_10y_yield"]),
            "dr007_proxy": latest_liquidity, "dr007_state": state,
            "dr007_proxy_name": latest["dr007_proxy_name"],
            "cgb_source_url": latest["cgb_source_url"],
            "liquidity_source_url": latest["liquidity_source_url"],
        },
        "factor_scores": [
            {"name": name, "label": FACTOR_LABELS[name], "score": round(latest_factors.get(name, 0.0), 4)}
            for name in FACTOR_NAMES
        ],
        "evidence": audit[-5:],
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def load_backtest() -> dict[str, Any]:
    market, texts, errors = _load()
    if errors or len(market) < 25:
        return {
            "status": "research_evidence_insufficient", "reason": "；".join(errors) or "市场样本不足25个交易日",
            "routes": [], "increment_conclusion": "文本预测增量尚未建立",
            "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
        }
    factors, pressure, audit = _daily_context(market, texts)
    routes = [evaluate_route(market, factors, pressure, route) for route in ROUTES]
    baseline = routes[0]
    enhanced = routes[-1]
    baseline_f1 = baseline.get("macro_f1")
    enhanced_f1 = enhanced.get("macro_f1")
    effective_text_dates = {
        row["effective_trade_date"] for row in audit if row.get("effective_trade_date")
    }
    minimum_text_dates = 30
    text_coverage_sufficient = len(effective_text_dates) >= minimum_text_dates
    incremental = (
        baseline_f1 is not None and enhanced_f1 is not None and enhanced_f1 > baseline_f1
        and enhanced.get("observations", 0) >= 30
        and text_coverage_sufficient
    )
    return {
        "status": "evaluated", "target": TARGET_NAME,
        "split_policy": "expanding_window_no_shuffle",
        "routes": routes,
        "text_coverage": {
            "documents": len(texts),
            "effective_trade_dates": len(effective_text_dates),
            "minimum_effective_trade_dates": minimum_text_dates,
            "sufficient_for_increment_claim": text_coverage_sufficient,
        },
        "increment_established": incremental,
        "increment_conclusion": "文本预测增量初步成立" if incremental else "文本预测增量尚未建立",
        "research_warning": (
            "当前公开样例只用于验证数据、建模和审计链路；文本覆盖不足，"
            "不得把四路线分数差异解释为已证明的文本增量。正式结论需要跨周期样本外检验。"
        ),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def analyze_document(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: str(value or "").strip() for key, value in payload.items()}
    for field in ("title", "content", "source_name", "publish_time", "source_url"):
        if not normalized.get(field):
            raise ValueError(f"缺少字段：{field}")
    datetime.fromisoformat(normalized["publish_time"].replace("Z", "+00:00"))
    if not normalized["source_url"].startswith(("https://", "http://")):
        raise ValueError("source_url 必须以 http:// 或 https:// 开头")
    document = {
        "doc_id": "live-" + hashlib.sha256((normalized["title"] + normalized["content"]).encode()).hexdigest()[:12],
        "title": normalized["title"], "content": normalized["content"],
        "source_name": normalized["source_name"], "source_url": normalized["source_url"],
        "publish_time": normalized["publish_time"],
    }
    deterministic = ground_predicates(document)
    try:
        llm = extract_with_llm(document, normalized.get("api_key", ""))
    except AIServiceError as exc:
        llm = {"used": False, "reason": str(exc), "events": [], "predicates": [], "metadata": {}}
    predicates = merge_llm_predicates(deterministic, llm.get("predicates", [])) if llm.get("used") else [
        {**row, "consensus": "deterministic_only"} for row in deterministic
    ]
    scores = factor_scores(predicates)
    activated = activate_rules(predicates)
    pressure = sum(scores.values()) / max(len(scores), 1) + 0.25 * rule_pressure(activated)
    baseline = load_forecast()
    baseline_probs = dict(baseline.get("probabilities", {"down": 1 / 3, "flat": 1 / 3, "up": 1 / 3}))
    shift = max(min(pressure * 0.12, 0.12), -0.12)
    updated = dict(baseline_probs)
    updated["up"] = max(updated["up"] + shift, 0.01)
    updated["down"] = max(updated["down"] - shift, 0.01)
    total = sum(updated.values())
    updated = {label: round(value / total, 6) for label, value in updated.items()}
    return {
        "analysis_type": "incremental_single_text", "document": {key: document[key] for key in document if key != "content"},
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "llm_analysis": llm, "predicates": predicates,
        "factor_scores": [{"name": name, "label": FACTOR_LABELS[name], "score": scores[name]} for name in FACTOR_NAMES],
        "triggered_rules": activated,
        "baseline_forecast": baseline_probs, "updated_forecast": updated,
        "probability_delta": {label: round(updated[label] - baseline_probs[label], 6) for label in updated},
        "interpretation": "该结果仅表示单篇文本对现有五日预测的边际影响，不是独立市场预测。",
        "credential_retained": False, "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def append_review(payload: dict[str, Any]) -> dict[str, Any]:
    document_id = str(payload.get("document_id", "")).strip()
    decision = str(payload.get("decision", "")).strip()
    if not document_id or decision not in {"approved", "rejected", "needs_revision"}:
        raise ValueError("document_id 必填，decision 仅支持 approved/rejected/needs_revision")
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "review_id": hashlib.sha256(f"{document_id}-{datetime.now().isoformat()}".encode()).hexdigest()[:16],
        "document_id": document_id, "decision": decision,
        "comment": str(payload.get("comment", "")).strip()[:1000],
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with REVIEW_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
