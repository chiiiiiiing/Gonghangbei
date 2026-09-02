"""Auditable rates research service and artifact builder."""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.ai.gateway import AIServiceError
from src.rates.factors import (
    document_fingerprint,
    events_from_predicates,
    factor_scores,
    ground_predicates,
    merge_llm_predicates,
)
from src.rates.llm import extract_with_llm
from src.rates.modeling import ROUTES, evaluate_route, live_probabilities, paired_block_bootstrap
from src.rates.rules import RULES, RULE_VERSION, activate_rules, rule_pressure
from src.rates.schema import (
    DISCLAIMER,
    FACTOR_LABELS,
    FACTOR_NAMES,
    FLAT_THRESHOLD_BP,
    HORIZON_TRADING_DAYS,
    RESEARCH_BOUNDARY,
    TARGET_NAME,
    TEXT_DECAY_DAYS,
    TEXT_HALF_LIFE_DAYS,
    effective_trade_date,
    factor_dictionary,
    parse_datetime,
    validate_market_row,
    validate_text_row,
)


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "sample"
MARKET_PATH = DATA_DIR / "rates_market.csv"
TEXT_PATH = DATA_DIR / "rates_policy_texts.csv"
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
REVIEW_PATH = ROOT / "data" / "research" / "rates_reviews.jsonl"


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


def _daily_context(
    market: list[dict[str, str]], texts: list[dict[str, str]]
) -> tuple[dict[str, dict[str, float]], dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    dates = [row["trade_date"] for row in market]
    date_index = {day: index for index, day in enumerate(dates)}
    factor_sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    factor_weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    rule_sums: dict[str, float] = defaultdict(float)
    rule_weights: dict[str, float] = defaultdict(float)
    document_counts: Counter[str] = Counter()
    evidence_audit: list[dict[str, Any]] = []
    llm_cache = _llm_annotation_cache()
    for document in texts:
        effective = effective_trade_date(document["publish_time"], dates)
        deterministic = ground_predicates(document)
        annotation = llm_cache.get((document["doc_id"], document["source_sha256"]))
        if annotation and annotation.get("used"):
            source_text = f"{document['title']}。{document['content']}"
            predicates = merge_llm_predicates(deterministic, annotation.get("predicates", []), source_text)
            events = annotation.get("events", []) or events_from_predicates(document, predicates)
            extraction_mode = "llm_evidence_gated"
        else:
            predicates = [{**row, "consensus": "deterministic_only"} for row in deterministic]
            events = events_from_predicates(document, predicates)
            extraction_mode = "deterministic_fallback"
        scores = factor_scores(predicates)
        rules = activate_rules(predicates)
        if effective:
            start = date_index[effective]
            for age in range(TEXT_DECAY_DAYS):
                if start + age >= len(dates):
                    break
                trade_date = dates[start + age]
                decay = 0.5 ** (age / TEXT_HALF_LIFE_DAYS)
                document_counts[trade_date] += 1
                for name, score in scores.items():
                    if score:
                        factor_sums[trade_date][name] += score * decay
                        factor_weights[trade_date][name] += decay
                if rules:
                    rule_sums[trade_date] += rule_pressure(rules) * decay
                    rule_weights[trade_date] += decay
        evidence_audit.append({
            "doc_id": document["doc_id"], "title": document["title"],
            "publish_time": document["publish_time"], "effective_trade_date": effective,
            "source_name": document["source_name"], "source_url": document["source_url"],
            "source_sha256": document["source_sha256"], "document_fingerprint": document_fingerprint(document),
            "extraction_mode": extraction_mode,
            "llm_request_id": (annotation or {}).get("metadata", {}).get("request_id", ""),
            "events": events,
            "active_predicates": [row for row in predicates if row["value"]],
            "factor_scores": scores, "triggered_rules": rules,
        })
    factors_by_date: dict[str, dict[str, float]] = {}
    daily_rows: list[dict[str, Any]] = []
    for trade_date in dates:
        scores = {
            name: round(factor_sums[trade_date][name] / factor_weights[trade_date][name], 8)
            if factor_weights[trade_date][name] else 0.0
            for name in FACTOR_NAMES
        }
        factors_by_date[trade_date] = scores
        pressure = rule_sums[trade_date] / rule_weights[trade_date] if rule_weights[trade_date] else 0.0
        daily_rows.append({
            "trade_date": trade_date, **scores, "rule_pressure": round(pressure, 8),
            "supporting_document_count": int(document_counts[trade_date]),
        })
    pressure_by_date = {row["trade_date"]: float(row["rule_pressure"]) for row in daily_rows}
    return factors_by_date, pressure_by_date, evidence_audit, daily_rows


def load_status() -> dict[str, Any]:
    market, texts, errors, duplicates = _load()
    market_audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8")) if AUDIT_PATH.exists() else {}
    policy_audit = json.loads(POLICY_AUDIT_PATH.read_text(encoding="utf-8")) if POLICY_AUDIT_PATH.exists() else {}
    sources = Counter(row["source_name"] for row in texts)
    llm_cache = _llm_annotation_cache()
    llm_usable = sum(bool(row.get("used")) for row in llm_cache.values())
    return {
        "version": "rates-text-factor-v2",
        "target": TARGET_NAME, "horizon_trading_days": HORIZON_TRADING_DAYS,
        "flat_threshold_bp": FLAT_THRESHOLD_BP,
        "data_ready": len(market) >= 257 and not errors,
        "market_rows": len(market), "text_rows": len(texts), "deduplicated_text_rows": duplicates,
        "first_trade_date": market[0]["trade_date"] if market else None,
        "latest_trade_date": market[-1]["trade_date"] if market else None,
        "liquidity_series": market[-1]["dr007_proxy_name"] if market else None,
        "source_counts": dict(sources), "factor_dictionary": factor_dictionary(),
        "rule_count": len(RULES), "rule_version": RULE_VERSION,
        "llm_annotations": len(llm_cache), "llm_usable_annotations": llm_usable,
        "llm_coverage": round(llm_usable / len(texts), 4) if texts else 0.0,
        "data_errors": errors, "source_audit": {"market": market_audit, "policy": policy_audit},
        "source_signature": _source_signature(),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }


def _compute_forecast(as_of: str | None = None, horizon: int = HORIZON_TRADING_DAYS) -> dict[str, Any]:
    market, texts, errors, _duplicates = _load()
    if horizon != HORIZON_TRADING_DAYS:
        raise ValueError("当前冻结模型仅支持5个交易日预测窗口")
    if as_of:
        market = [row for row in market if row["trade_date"] <= as_of]
        texts = [row for row in texts if parse_datetime(row["publish_time"]).date().isoformat() <= as_of]
    if errors or not market:
        return {
            "status": "research_evidence_insufficient", "reason": "；".join(errors) or "尚未导入官方市场数据",
            "probabilities": {"down": 1 / 3, "flat": 1 / 3, "up": 1 / 3},
            "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
        }
    factors, pressure, audit, _daily = _daily_context(market, texts)
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
    factors, pressure, _audit, _daily = _daily_context(market, texts)
    routes = [evaluate_route(market, factors, pressure, route) for route in ROUTES]
    baseline, enhanced = routes[0], routes[-1]
    oos_baseline = {"timeline": [row for row in baseline["timeline"] if row["period"] == "oos_2025_latest"]}
    oos_enhanced = {"timeline": [row for row in enhanced["timeline"] if row["period"] == "oos_2025_latest"]}
    bootstrap = paired_block_bootstrap(oos_baseline, oos_enhanced)
    baseline_oos = next(row for row in baseline["period_metrics"] if row["period"] == "oos_2025_latest")
    enhanced_oos = next(row for row in enhanced["period_metrics"] if row["period"] == "oos_2025_latest")
    incremental = bool(
        enhanced_oos.get("macro_f1") is not None
        and baseline_oos.get("macro_f1") is not None
        and enhanced_oos["macro_f1"] > baseline_oos["macro_f1"]
        and bootstrap.get("stable")
    )
    return {
        "status": "evaluated", "target": TARGET_NAME,
        "split_policy": "purged_756_day_rolling_window_no_shuffle",
        "periods": {
            "discovery": "2018-01-01至2022-12-31", "validation": "2023-01-01至2024-12-31",
            "oos": "2025-01-01至最新",
        },
        "routes": routes, "oos_increment_bootstrap": bootstrap,
        "increment_established": incremental,
        "increment_conclusion": "冻结OOS文本预测增量成立" if incremental else "冻结OOS文本预测增量尚未建立",
        "research_warning": "回测只衡量方向分类，不代表可交易收益；结论必须同时查看分期指标与区块Bootstrap。",
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
    try:
        llm = extract_with_llm(document, normalized.get("api_key", ""))
    except AIServiceError as exc:
        llm = {"used": False, "reason": str(exc), "events": [], "predicates": [], "metadata": {}}
    source_text = f"{document['title']}。{document['content']}"
    predicates = merge_llm_predicates(deterministic, llm.get("predicates", []), source_text) if llm.get("used") else [
        {**row, "consensus": "deterministic_only"} for row in deterministic
    ]
    scores = factor_scores(predicates)
    activated = activate_rules(predicates)
    deterministic_events = events_from_predicates(document, predicates)
    events = llm.get("events", []) if llm.get("used") and llm.get("events") else deterministic_events
    baseline = load_forecast()
    baseline_probs = dict(baseline.get("probabilities", {"down": 1 / 3, "flat": 1 / 3, "up": 1 / 3}))
    market, texts, errors, _duplicates = _load()
    if errors or not market:
        updated = baseline_probs
        marginal_model = {"data_sufficient": False, "reason": "市场数据不可用"}
        scenario_effective_date = None
        scenario_active = False
    else:
        historical_factors, historical_pressure, _audit, _daily = _daily_context(market, texts)
        latest_date = market[-1]["trade_date"]
        trade_dates = [row["trade_date"] for row in market]
        scenario_effective_date = effective_trade_date(normalized["publish_time"], trade_dates)
        scenario_active = bool(
            scenario_effective_date
            and 0 <= len(trade_dates) - 1 - trade_dates.index(scenario_effective_date) < TEXT_DECAY_DAYS
        )
        scenario_factors = {day: dict(values) for day, values in historical_factors.items()}
        latest_factors = scenario_factors.setdefault(latest_date, {name: 0.0 for name in FACTOR_NAMES})
        if scenario_active:
            age = len(trade_dates) - 1 - trade_dates.index(str(scenario_effective_date))
            decay = 0.5 ** (age / TEXT_HALF_LIFE_DAYS)
            for name in FACTOR_NAMES:
                current = float(latest_factors.get(name, 0.0))
                addition = float(scores.get(name, 0.0)) * decay
                latest_factors[name] = (current + addition) / 2 if current and addition else current + addition
        scenario_pressure = dict(historical_pressure)
        current_pressure = float(scenario_pressure.get(latest_date, 0.0))
        added_pressure = float(rule_pressure(activated)) * decay if scenario_active else 0.0
        scenario_pressure[latest_date] = (
            (current_pressure + added_pressure) / 2 if current_pressure and added_pressure
            else current_pressure + added_pressure
        )
        marginal_model = live_probabilities(market, scenario_factors, scenario_pressure)
        updated = dict(marginal_model.get("probabilities", baseline_probs))
    factor_rows = [{"name": name, "label": FACTOR_LABELS[name], "score": scores[name]} for name in FACTOR_NAMES]
    return {
        "analysis_type": "incremental_single_text",
        "document": {key: document[key] for key in document if key != "content"},
        "llm_analysis": llm, "events": events, "predicates": predicates,
        "factor_scores": factor_rows, "triggered_rules": activated,
        "baseline_forecast": baseline_probs, "updated_forecast": updated,
        "probability_delta": {label: round(updated[label] - baseline_probs[label], 8) for label in updated},
        "marginal_model": {
            "route": "fusion_rules", "same_frozen_training_sample": True,
            "effective_trade_date": scenario_effective_date, "active_in_latest_window": scenario_active,
            "data_sufficient": marginal_model.get("data_sufficient", False),
            "feature_contributions": marginal_model.get("feature_contributions", []),
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
        "model_version": "rates-text-factor-v2", "immutable": True,
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
        payload["documents"] = payload.get("documents", [])[-max(1, min(limit, 500)):]
        return payload
    market, texts, errors, _duplicates = _load()
    if errors:
        return {"documents": [], "errors": errors, "disclaimer": DISCLAIMER}
    _factors, _pressure, evidence, _daily = _daily_context(market, texts)
    return {"version": "rates-evidence-v2", "documents": evidence[-max(1, min(limit, 500)):], "disclaimer": DISCLAIMER}


def load_demo_cases() -> dict[str, Any]:
    if DEMO_CASES_PATH.exists():
        return json.loads(DEMO_CASES_PATH.read_text(encoding="utf-8"))
    return {"cases": [], "offline_ready": False, "disclaimer": DISCLAIMER}


def load_report() -> str:
    if REPORT_PATH.exists():
        return REPORT_PATH.read_text(encoding="utf-8")
    status = load_status()
    return _research_report(load_forecast(), load_backtest(), status["market_rows"], status["text_rows"])


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_rates_outputs() -> dict[str, Any]:
    market, texts, errors, _duplicates = _load()
    if errors:
        raise ValueError("；".join(errors))
    factors, pressure, evidence, daily = _daily_context(market, texts)
    forecast = _compute_forecast()
    backtest = _compute_backtest()
    with DAILY_FACTOR_PATH.open("w", encoding="utf-8", newline="") as handle:
        fields = ["trade_date", *FACTOR_NAMES, "rule_pressure", "supporting_document_count"]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(daily)
    _write_json(EVIDENCE_PATH, {"version": "rates-evidence-v2", "documents": evidence, "disclaimer": DISCLAIMER})
    _write_json(FORECAST_PATH, forecast)
    artifact_backtest = copy.deepcopy(backtest)
    for route in artifact_backtest.get("routes", []):
        timeline = route.get("timeline", [])
        route["timeline_total"] = len(timeline)
        route["timeline"] = timeline[-120:]
        route["timeline_retained"] = len(route["timeline"])
    _write_json(BACKTEST_PATH, artifact_backtest)
    manifest = {
        "version": "rates-model-v2", "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": TARGET_NAME, "horizon_trading_days": HORIZON_TRADING_DAYS,
        "flat_threshold_bp": FLAT_THRESHOLD_BP, "routes": list(ROUTES),
        "split_policy": backtest.get("split_policy"), "periods": backtest.get("periods"),
        "text_decay_days": TEXT_DECAY_DAYS, "text_half_life_days": TEXT_HALF_LIFE_DAYS,
        "rule_version": RULE_VERSION, "source_signature": _source_signature(),
        "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY,
    }
    _write_json(MODEL_MANIFEST_PATH, manifest)
    demo_cases = [
        {
            "case_id": "pboc-liquidity-injection", "title": "公开市场逆回购操作",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性，保持银行体系流动性合理充裕。",
            "source_name": "中国人民银行", "publish_time": "2026-08-28T09:30:00",
            "source_url": "https://www.pbc.gov.cn/", "expected_factor": "liquidity",
        },
        {
            "case_id": "growth-inflation-up", "title": "经济运行和价格水平回升",
            "content": "经济运行延续回升态势，PMI扩张，CPI同比回升，物价上涨压力有所增加。",
            "source_name": "国家统计局", "publish_time": "2026-08-28T10:00:00",
            "source_url": "https://www.stats.gov.cn/", "expected_rule": "R-GROWTH-INF-01",
        },
        {
            "case_id": "bond-supply-tight-funding", "title": "政府债发行与资金面",
            "content": "政府债券加快发行并形成集中供给，DR007上行，银行间资金面偏紧。",
            "source_name": "财政部", "publish_time": "2026-08-28T16:00:00",
            "source_url": "https://www.mof.gov.cn/", "expected_rule": "R-SUPPLY-LIQ-02",
        },
    ]
    _write_json(DEMO_CASES_PATH, {"cases": demo_cases, "offline_ready": True, "disclaimer": DISCLAIMER})
    report = _research_report(forecast, backtest, len(market), len(texts))
    REPORT_PATH.write_text(report, encoding="utf-8")
    return {
        "market_rows": len(market), "text_rows": len(texts), "factor_rows": len(daily),
        "backtest_observations": backtest.get("routes", [{}])[-1].get("observations", 0),
        "increment_conclusion": backtest.get("increment_conclusion"),
    }


def _research_report(forecast: dict[str, Any], backtest: dict[str, Any], market_rows: int, text_rows: int) -> str:
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
        f"- 样本：{market_rows}个交易日，{text_rows}篇去重政策文本", "",
        "## 市场与流动性", "",
        f"- 10年期国债收益率：{snapshot.get('cgb_10y_yield', '证据不足')}%",
        f"- {snapshot.get('dr007_proxy_name', 'FDR007_FIXING')}：{snapshot.get('dr007_proxy', '证据不足')}%",
        f"- 流动性状态：{snapshot.get('dr007_state', '证据不足')}", "",
        "## 六类文本因子", "",
        "| 因子 | 收益率压力分数 |", "| --- | ---: |",
    ]
    lines.extend(
        f"| {row.get('label', row.get('name', ''))} | {float(row.get('score', 0)):+.4f} |"
        for row in forecast.get("factor_scores", [])
    )
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
        "| 路线 | OOS观测 | Accuracy | Macro-F1 | AUC |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for route in backtest.get("routes", []):
        oos = next((row for row in route.get("period_metrics", []) if row.get("period") == "oos_2025_latest"), {})
        if not oos:
            continue
        lines.append(
            f"| {route.get('route_label', route.get('route', ''))} | {oos.get('observations', 0)} | "
            f"{float(oos.get('accuracy') or 0):.2%} | {float(oos.get('macro_f1') or 0):.4f} | "
            f"{float(oos.get('macro_auc_ovr') or 0):.4f} |"
        )
    bootstrap = backtest.get("oos_increment_bootstrap", {})
    lines.extend([
        "", f"- 结论：{backtest.get('increment_conclusion', '尚未评估')}",
        f"- 规则增强相对市场基线的OOS准确率差：{float(bootstrap.get('accuracy_difference') or 0):+.2%}",
        f"- 20日移动区块Bootstrap 95%区间：[{float(bootstrap.get('ci_lower_95') or 0):.2%}, {float(bootstrap.get('ci_upper_95') or 0):.2%}]",
        f"- 切分：{backtest.get('split_policy', '尚未评估')}",
        f"- 注意：{backtest.get('research_warning', '')}", "",
        "## 数据来源与研究边界", "",
        f"- 中债收益率曲线：{snapshot.get('cgb_source_url', '未提供')}",
        f"- 银行间流动性代理：{snapshot.get('liquidity_source_url', '未提供')}",
        "- FDR007定盘利率仅作DR007历史代理；当前样本没有完整接入CPI、PPI、PMI、社融、MLF和政府债发行结构化序列。",
        "", RESEARCH_BOUNDARY, "", DISCLAIMER, "",
    ])
    return "\n".join(lines)
