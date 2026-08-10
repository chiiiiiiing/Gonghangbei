"""Deterministic macro layer built after the existing candidate-factor pipeline.

The module deliberately does not replace the fixed 19-predicate AI/rule workflow.
It consumes that workflow's audited outputs, aggregates them by target period, and
then performs release-date-aware nowcasting and a separate strategy experiment.
"""

from __future__ import annotations

import csv
import json
import math
import re
import random
import statistics
import subprocess
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
TARGET_NAME = "电气机械和器材制造业增加值同比增速"
RISK_CODE = "516160"
DEFENSIVE_CODE = "511010"
PROXY_CODE = "399808"
TEXT_MIN_TRAIN_MONTHS = 24
TEXT_MIN_VALIDATION_MONTHS = 12

FEATURE_FIELDS = [
    "period_start", "period_end", "split", "document_count", "unique_source_count",
    "event_count", "stock_count", "predicate_count", "true_predicate_count",
    "positive_predicate_rate", "avg_predicate_confidence", "avg_event_evidence",
    "avg_entity_confidence", "qualified_rule_hits", "rule_supporting_document_count",
    "policy_count", "announcement_count", "news_count", "ir_qa_count",
    "battery_exposure", "solar_exposure", "storage_exposure", "grid_exposure",
    "wind_exposure", "factor_mean", "factor_p25", "factor_median", "factor_p75",
    "factor_std", "factor_positive_rate", "ai_annotation_coverage",
    "fulltext_completeness_rate", "coverage_status",
]

MODEL_NAMES = ("persistence", "seasonal", "ar1", "no_text_ridge", "ridge_text", "elastic_net_text")
PREDICTOR_FIELDS = [
    "document_count", "unique_source_count", "event_count", "stock_count",
    "true_predicate_count", "positive_predicate_rate", "avg_predicate_confidence",
    "avg_event_evidence", "avg_entity_confidence", "qualified_rule_hits",
    "policy_count", "announcement_count", "news_count", "ir_qa_count",
    "battery_exposure", "solar_exposure", "storage_exposure", "grid_exposure",
    "wind_exposure", "factor_mean", "factor_std", "factor_positive_rate",
]


def _read_csv(name: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(name: str, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path = SAMPLE_DIR / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for field in fields:
                value = row.get(field, "")
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif isinstance(value, float):
                    value = f"{value:.6f}"
                rendered[field] = value
            writer.writerow(rendered)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _split(period_end: str) -> str:
    if period_end <= "2021-12-31":
        return "train"
    if period_end <= "2023-12-31":
        return "validation"
    return "oos"


def _canonical_document(row: dict[str, str]) -> str:
    url = row.get("url", "").strip().lower().split("#", 1)[0]
    if url:
        return url
    text = re.sub(r"\s+", "", f"{row.get('title', '')}{row.get('content', '')}").lower()
    return text[:800]


def _target_periods() -> list[dict[str, str]]:
    rows = _read_csv("macro_target_history.csv")
    rows.sort(key=lambda item: item["period_end"])
    if not rows:
        return []
    last_end = datetime.strptime(rows[-1]["period_end"], "%Y-%m-%d").date()
    next_start = last_end + timedelta(days=1)
    today = date.today()
    if next_start <= today:
        if next_start.month == 12:
            next_end = date(next_start.year, 12, 31)
        else:
            next_end = date(next_start.year, next_start.month + 1, 1) - timedelta(days=1)
        rows.append({
            "period_start": next_start.isoformat(), "period_end": next_end.isoformat(),
            "period_kind": "monthly", "actual_yoy": "", "release_date": "",
            "source_name": "待国家统计局发布", "source_url": "", "vintage": "nowcast",
        })
    return rows


def aggregate_monthly_features() -> list[dict[str, Any]]:
    """Aggregate unique verified documents and downstream audited results by period."""
    documents = _read_csv("raw_documents.csv")
    events = _read_csv("events.csv")
    predicates = _read_csv("predicates.csv")
    links = _read_csv("entity_links.csv")
    factors = _read_csv("factors.csv")
    rules = {row["rule_id"]: row for row in _read_csv("rules.csv") if row.get("status") == "qualified"}

    unique_docs: dict[str, dict[str, str]] = {}
    for row in documents:
        key = _canonical_document(row)
        previous = unique_docs.get(key)
        if previous is None or row.get("publish_time", "") < previous.get("publish_time", ""):
            unique_docs[key] = row
    docs = list(unique_docs.values())
    event_by_id = {row["event_id"]: row for row in events}
    events_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in events:
        events_by_doc[row["doc_id"]].append(row)
    predicates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in predicates:
        predicates_by_event[row["event_id"]].append(row)
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in links:
        links_by_doc[row["doc_id"]].append(row)
    factors_by_doc: dict[str, list[float]] = defaultdict(list)
    rule_hits_by_doc: dict[str, set[str]] = defaultdict(set)
    for factor in factors:
        event_ids = [value for value in factor.get("trigger_event_ids", "").split("|") if value]
        doc_ids = {event_by_id[event_id]["doc_id"] for event_id in event_ids if event_id in event_by_id}
        for doc_id in doc_ids:
            factors_by_doc[doc_id].append(_f(factor.get("factor_value")))
            for rule_id in factor.get("trigger_rule_ids", "").split("|"):
                if rule_id in rules:
                    rule_hits_by_doc[doc_id].add(rule_id)

    annotation_success: set[str] = set()
    annotation_path = SAMPLE_DIR / "ai_annotations.jsonl"
    if annotation_path.exists():
        latest: dict[str, dict[str, Any]] = {}
        for line in annotation_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            doc_id = str(record.get("doc_id", ""))
            if doc_id and (doc_id not in latest or str(record.get("generated_at", "")) >= str(latest[doc_id].get("generated_at", ""))):
                latest[doc_id] = record
        annotation_success = {doc_id for doc_id, row in latest.items() if row.get("status") == "success"}

    topic_patterns = {
        "battery_exposure": ("电池", "锂电", "隔膜", "正极", "负极"),
        "solar_exposure": ("光伏", "太阳能", "硅片", "组件"),
        "storage_exposure": ("储能", "新型储能"),
        "grid_exposure": ("电网", "输电", "配电", "特高压"),
        "wind_exposure": ("风电", "风机", "风力"),
    }
    output: list[dict[str, Any]] = []
    for target in _target_periods():
        start, end = target["period_start"], target["period_end"]
        period_docs = [row for row in docs if start <= row.get("publish_time", "") <= end]
        doc_ids = {row["doc_id"] for row in period_docs}
        period_events = [event for doc_id in doc_ids for event in events_by_doc.get(doc_id, [])]
        event_ids = {row["event_id"] for row in period_events}
        period_predicates = [row for event_id in event_ids for row in predicates_by_event.get(event_id, [])]
        period_links = [row for doc_id in doc_ids for row in links_by_doc.get(doc_id, [])]
        period_factors = [value for doc_id in doc_ids for value in factors_by_doc.get(doc_id, [])]
        true_count = sum(row.get("value", "").lower() == "true" for row in period_predicates)
        boolean_count = sum(row.get("value", "").lower() in {"true", "false"} for row in period_predicates)
        text_by_doc = {row["doc_id"]: f"{row.get('title', '')} {row.get('content', '')}" for row in period_docs}
        source_counts = Counter(row.get("source_type", "") for row in period_docs)
        rule_hit_pairs = {(doc_id, rule_id) for doc_id in doc_ids for rule_id in rule_hits_by_doc.get(doc_id, set())}
        row: dict[str, Any] = {
            "period_start": start, "period_end": end, "split": _split(end),
            "document_count": len(period_docs),
            "unique_source_count": len({row.get("source_name", "") for row in period_docs if row.get("source_name")}),
            "event_count": len({row["event_id"] for row in period_events}),
            "stock_count": len({row["stock_code"] for row in period_events}),
            "predicate_count": len(period_predicates), "true_predicate_count": true_count,
            "positive_predicate_rate": true_count / boolean_count if boolean_count else 0.0,
            "avg_predicate_confidence": _mean([_f(row.get("confidence")) for row in period_predicates]),
            "avg_event_evidence": _mean([_f(row.get("evidence_strength")) for row in period_events]),
            "avg_entity_confidence": _mean([_f(row.get("confidence")) for row in period_links]),
            "qualified_rule_hits": len(rule_hit_pairs),
            "rule_supporting_document_count": len({pair[0] for pair in rule_hit_pairs}),
            "policy_count": source_counts["policy"], "announcement_count": source_counts["announcement"],
            "news_count": source_counts["news"], "ir_qa_count": source_counts["ir_qa"],
            "factor_mean": _mean(period_factors), "factor_p25": _quantile(period_factors, .25),
            "factor_median": _quantile(period_factors, .5), "factor_p75": _quantile(period_factors, .75),
            "factor_std": statistics.pstdev(period_factors) if len(period_factors) > 1 else 0.0,
            "factor_positive_rate": sum(value > 0 for value in period_factors) / len(period_factors) if period_factors else 0.0,
            "ai_annotation_coverage": len(doc_ids & annotation_success) / len(doc_ids) if doc_ids else 0.0,
            "fulltext_completeness_rate": 0.0,
            "coverage_status": "sufficient" if len(period_docs) >= 5 else "insufficient",
        }
        for field, keywords in topic_patterns.items():
            row[field] = sum(any(keyword in text_by_doc[doc_id] for keyword in keywords) for doc_id in doc_ids)
        output.append(row)
    _write_csv("macro_monthly_features.csv", FEATURE_FIELDS, output)
    return output


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    aug = [matrix[i][:] + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        if abs(aug[col][col]) < 1e-10:
            continue
        scale = aug[col][col]
        aug[col] = [value / scale for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            aug[row] = [aug[row][i] - factor * aug[col][i] for i in range(n + 1)]
    return [aug[i][-1] if abs(aug[i][i]) > 1e-10 else 0.0 for i in range(n)]


def _ridge_fit(x: list[list[float]], y: list[float], alpha: float) -> tuple[list[float], list[float], list[float]]:
    if not x:
        return [0.0], [], []
    p = len(x[0])
    means = [_mean([row[j] for row in x]) for j in range(p)]
    scales = [statistics.pstdev([row[j] for row in x]) or 1.0 for j in range(p)]
    z = [[1.0] + [(row[j] - means[j]) / scales[j] for j in range(p)] for row in x]
    size = p + 1
    gram = [[sum(row[i] * row[j] for row in z) for j in range(size)] for i in range(size)]
    for i in range(1, size):
        gram[i][i] += alpha
    rhs = [sum(row[i] * target for row, target in zip(z, y)) for i in range(size)]
    return _solve(gram, rhs), means, scales


def _ridge_predict(model: tuple[list[float], list[float], list[float]], row: list[float]) -> float:
    coefficients, means, scales = model
    return coefficients[0] + sum(coefficients[j + 1] * (row[j] - means[j]) / scales[j] for j in range(len(row)))


def _elastic_net_fit(
    x: list[list[float]], y: list[float], alpha: float = 0.08, l1_ratio: float = 0.5,
) -> tuple[list[float], list[float], list[float]]:
    """Small deterministic coordinate-descent Elastic Net for low-dimensional research."""
    if not x:
        return [0.0], [], []
    p = len(x[0])
    means = [_mean([row[j] for row in x]) for j in range(p)]
    scales = [statistics.pstdev([row[j] for row in x]) or 1.0 for j in range(p)]
    z = [[(row[j] - means[j]) / scales[j] for j in range(p)] for row in x]
    intercept = _mean(y)
    centered = [value - intercept for value in y]
    coefficients = [0.0] * p
    for _ in range(300):
        largest_change = 0.0
        for j in range(p):
            residual = [
                centered[i] - sum(z[i][k] * coefficients[k] for k in range(p) if k != j)
                for i in range(len(z))
            ]
            rho = _mean([z[i][j] * residual[i] for i in range(len(z))])
            threshold = alpha * l1_ratio
            numerator = math.copysign(max(abs(rho) - threshold, 0.0), rho)
            denominator = _mean([row[j] ** 2 for row in z]) + alpha * (1 - l1_ratio)
            updated = numerator / denominator if denominator else 0.0
            largest_change = max(largest_change, abs(updated - coefficients[j]))
            coefficients[j] = updated
        if largest_change < 1e-8:
            break
    return [intercept, *coefficients], means, scales


def _ar_prediction(history: list[float]) -> float:
    if len(history) < 4:
        return history[-1] if history else 0.0
    x = [[history[i - 1]] for i in range(1, len(history))]
    return _ridge_predict(_ridge_fit(x, history[1:], 1.0), [history[-1]])


def _model_predictions(history_rows: list[dict[str, Any]], current_feature: dict[str, Any]) -> dict[str, float]:
    values = [_f(row["actual_yoy"]) for row in history_rows]
    latest = values[-1] if values else 0.0
    month = int(current_feature["period_end"][5:7])
    seasonal_values = [_f(row["actual_yoy"]) for row in history_rows if int(row["period_end"][5:7]) == month]
    seasonal = _mean(seasonal_values[-5:]) if seasonal_values else latest
    ar1 = _ar_prediction(values)
    no_text_x = [[_f(row["actual_yoy"]), math.sin(int(row["period_end"][5:7]) * math.pi / 6), math.cos(int(row["period_end"][5:7]) * math.pi / 6)] for row in history_rows[:-1]]
    no_text_y = values[1:]
    no_text_row = [latest, math.sin(month * math.pi / 6), math.cos(month * math.pi / 6)]
    no_text = _ridge_predict(_ridge_fit(no_text_x, no_text_y, 2.0), no_text_row) if len(no_text_x) >= 8 else ar1
    covered = [row for row in history_rows if int(_f(row.get("document_count"))) > 0]
    sufficient = len(covered) >= TEXT_MIN_TRAIN_MONTHS
    if sufficient:
        text_x = [[_f(row[field]) for field in PREDICTOR_FIELDS] for row in history_rows]
        text_y = values
        feature_row = [_f(current_feature[field]) for field in PREDICTOR_FIELDS]
        ridge_text = _ridge_predict(_ridge_fit(text_x, text_y, 8.0), feature_row)
        elastic_text = _ridge_predict(_elastic_net_fit(text_x, text_y), feature_row)
    else:
        ridge_text = no_text
        elastic_text = no_text
    return {
        "persistence": latest, "seasonal": seasonal, "ar1": ar1, "no_text_ridge": no_text,
        "ridge_text": ridge_text, "elastic_net_text": elastic_text,
    }


def build_forecasts(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = _target_periods()
    feature_by_end = {row["period_end"]: row for row in features}
    known = [{**row, **feature_by_end.get(row["period_end"], {})} for row in targets if row.get("actual_yoy")]
    forecast_rows: list[dict[str, Any]] = []
    residuals: dict[str, list[float]] = defaultdict(list)
    validation_errors: dict[str, list[float]] = defaultdict(list)
    selected_model = "no_text_ridge"
    for target in targets:
        as_of = target["period_end"]
        feature = feature_by_end.get(as_of, {field: 0 for field in FEATURE_FIELDS})
        # This is the central anti-leakage boundary: only values released by month-end are trainable.
        available = [row for row in known if row.get("release_date") and row["release_date"] <= as_of]
        if len(available) < 12:
            continue
        predictions = _model_predictions(available, feature)
        actual = target.get("actual_yoy", "")
        if _split(as_of) == "validation" and actual:
            for name, value in predictions.items():
                validation_errors[name].append(abs(value - _f(actual)))
            eligible = ("persistence", "seasonal", "ar1", "no_text_ridge")
            selected_model = min(eligible, key=lambda name: _mean(validation_errors[name]) if validation_errors[name] else float("inf"))
        if as_of >= "2022-01-01":
            chosen = predictions[selected_model]
            prior_actual = _f(available[-1]["actual_yoy"])
            width = _quantile([abs(value) for value in residuals[selected_model]], .9) if residuals[selected_model] else 3.0
            text_train_count = sum(int(_f(row.get("document_count"))) > 0 for row in available if row["period_end"] <= "2021-12-31")
            text_validation_count = sum(int(_f(row.get("document_count"))) > 0 for row in available if "2022-01-01" <= row["period_end"] <= "2023-12-31")
            text_status = "evaluated" if text_train_count >= TEXT_MIN_TRAIN_MONTHS and text_validation_count >= TEXT_MIN_VALIDATION_MONTHS else "insufficient_history"
            forecast_rows.append({
                "as_of_date": as_of, "target_period_start": target["period_start"],
                "target_period_end": as_of, "split": _split(as_of), "selected_model": selected_model,
                "predicted_yoy": chosen, "actual_yoy": actual, "latest_published_yoy": prior_actual,
                "predicted_acceleration": chosen - prior_actual, "lower_90": chosen - width,
                "upper_90": chosen + width, "text_document_count": int(_f(feature.get("document_count"))),
                "text_train_period_count": text_train_count, "text_validation_period_count": text_validation_count,
                "text_increment_status": text_status, "evidence_status": "sufficient" if text_status == "evaluated" else "insufficient",
                "target_release_date": target.get("release_date", ""), "is_oos": _split(as_of) == "oos",
            })
        if actual:
            for name, prediction in predictions.items():
                residuals[name].append(_f(actual) - prediction)
    fields = [
        "as_of_date", "target_period_start", "target_period_end", "split", "selected_model",
        "predicted_yoy", "actual_yoy", "latest_published_yoy", "predicted_acceleration",
        "lower_90", "upper_90", "text_document_count", "text_train_period_count",
        "text_validation_period_count", "text_increment_status", "evidence_status",
        "target_release_date", "is_oos",
    ]
    _write_csv("macro_forecasts.csv", fields, forecast_rows)
    _write_forecast_metrics(forecast_rows)
    return forecast_rows


def _write_forecast_metrics(rows: list[dict[str, Any]]) -> None:
    metrics: list[dict[str, Any]] = []
    for split in ("validation", "oos"):
        samples = [row for row in rows if row["split"] == split and str(row.get("actual_yoy", "")) != ""]
        errors = [_f(row["predicted_yoy"]) - _f(row["actual_yoy"]) for row in samples]
        actuals = [_f(row["actual_yoy"]) for row in samples]
        baseline = [_f(row["latest_published_yoy"]) for row in samples]
        denominator = sum((actual - prior) ** 2 for actual, prior in zip(actuals, baseline))
        direction = [
            ((_f(row["predicted_yoy"]) - _f(row["latest_published_yoy"])) * (_f(row["actual_yoy"]) - _f(row["latest_published_yoy"]))) > 0
            for row in samples
        ]
        coverage = [
            _f(row["lower_90"]) <= _f(row["actual_yoy"]) <= _f(row["upper_90"])
            for row in samples
        ]
        metrics.append({
            "split": split, "model": samples[-1]["selected_model"] if samples else "",
            "sample_count": len(samples), "mae": _mean([abs(error) for error in errors]),
            "rmse": math.sqrt(_mean([error * error for error in errors])) if errors else 0.0,
            "oos_r2": 1 - sum(error * error for error in errors) / denominator if denominator else 0.0,
            "acceleration_direction_accuracy": _mean([float(value) for value in direction]),
            "interval_coverage_90": _mean([float(value) for value in coverage]),
            "text_increment_status": samples[-1]["text_increment_status"] if samples else "insufficient_history",
        })
    _write_csv("macro_forecast_metrics.csv", ["split", "model", "sample_count", "mae", "rmse", "oos_r2", "acceleration_direction_accuracy", "interval_coverage_90", "text_increment_status"], metrics)


def ensure_market_data() -> list[dict[str, Any]]:
    path = SAMPLE_DIR / "macro_market_data.csv"
    if path.exists():
        return _read_csv("macro_market_data.csv")
    rows: list[dict[str, Any]] = []
    for code, asset_type in ((RISK_CODE, "risk_etf"), (DEFENSIVE_CODE, "defensive_etf")):
        query = urllib.parse.urlencode({
            "secid": f"1.{code}", "klt": "101", "fqt": "1", "beg": "20130101",
            "end": "20500101", "lmt": "10000", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        })
        result = subprocess.run(
            ["curl", "-L", "--fail", "--retry", "2", "--connect-timeout", "10",
             "-A", "Mozilla/5.0", "-e", "https://quote.eastmoney.com/",
             f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{query}"],
            capture_output=True, check=True, text=True, timeout=45,
        )
        payload = json.loads(result.stdout)
        data = payload.get("data") or {}
        for item in data.get("klines") or []:
            values = item.split(",")
            rows.append({
                "trade_date": values[0], "security_code": code, "asset_type": asset_type,
                "open": values[1], "close": values[2], "high": values[3], "low": values[4],
                "volume": values[5], "source_name": "东方财富行情接口",
                "source_url": "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            })
    rows.sort(key=lambda row: (row["trade_date"], row["security_code"]))
    _write_csv("macro_market_data.csv", ["trade_date", "security_code", "asset_type", "open", "close", "high", "low", "volume", "source_name", "source_url"], rows)
    return rows


def _annualized_metrics(returns: list[float], nav: list[float], turnover: list[float]) -> dict[str, float]:
    if not returns:
        return {key: 0.0 for key in ("annual_return", "annual_volatility", "sharpe", "calmar", "max_drawdown", "monthly_win_rate", "annual_turnover")}
    years = len(returns) / 12
    annual_return = nav[-1] ** (1 / years) - 1 if years > 0 and nav[-1] > 0 else 0.0
    annual_vol = statistics.pstdev(returns) * math.sqrt(12) if len(returns) > 1 else 0.0
    peak = nav[0]
    max_drawdown = 0.0
    for value in nav:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    return {
        "annual_return": annual_return, "annual_volatility": annual_vol,
        "sharpe": annual_return / annual_vol if annual_vol else 0.0,
        "calmar": annual_return / abs(max_drawdown) if max_drawdown else 0.0,
        "max_drawdown": max_drawdown,
        "monthly_win_rate": sum(value > 0 for value in returns) / len(returns),
        "annual_turnover": sum(turnover) / years if years else 0.0,
    }


def build_strategy(forecasts: list[dict[str, Any]], market: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market:
        by_code[row["security_code"]].append(row)
    for values in by_code.values():
        values.sort(key=lambda row: row["trade_date"])
    risk = by_code.get(RISK_CODE, [])
    defensive = by_code.get(DEFENSIVE_CODE, [])
    if not risk or not defensive:
        return []
    daily_close = {row["trade_date"]: _f(row["close"]) for row in risk}
    monthly_risk: dict[str, dict[str, Any]] = {}
    monthly_def: dict[str, dict[str, Any]] = {}
    monthly_risk_first: dict[str, dict[str, Any]] = {}
    monthly_def_first: dict[str, dict[str, Any]] = {}
    for row in risk:
        monthly_risk_first.setdefault(row["trade_date"][:7], row)
        monthly_risk[row["trade_date"][:7]] = row
    for row in defensive:
        monthly_def_first.setdefault(row["trade_date"][:7], row)
        monthly_def[row["trade_date"][:7]] = row
    months = sorted(set(monthly_risk) & set(monthly_def))
    forecast_by_month = {row["target_period_end"][:7]: row for row in forecasts}
    target_by_month = {row["period_end"][:7]: row for row in _target_periods() if row.get("actual_yoy")}
    strategies = ("buy_hold", "pure_momentum", "published_macro", "alphalens_nowcast", "oracle_non_tradable")
    output: list[dict[str, Any]] = []
    for cost_bps in (5, 10, 20):
        navs = {name: 1.0 for name in strategies}
        prior_weights = {name: 0.0 for name in strategies}
        returns_by_strategy: dict[str, list[float]] = defaultdict(list)
        nav_by_strategy: dict[str, list[float]] = defaultdict(list)
        turnover_by_strategy: dict[str, list[float]] = defaultdict(list)
        for index in range(12, len(months) - 2):
            month, next_month = months[index], months[index + 1]
            following_month = months[index + 2]
            current = monthly_risk[month]
            past = monthly_risk[months[index - 12]]
            trend_positive = _f(current["close"]) / _f(past["close"]) - 1 > 0
            dates = sorted(day for day in daily_close if day <= current["trade_date"])[-61:]
            daily_returns = [daily_close[dates[i]] / daily_close[dates[i - 1]] - 1 for i in range(1, len(dates))]
            vol = statistics.pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 10 else 0.0
            scaled = min(1.0, .10 / vol) if vol > 0 else 0.0
            forecast = forecast_by_month.get(month)
            alpha_accel = _f(forecast.get("predicted_acceleration")) if forecast else 0.0
            available_actuals = [row for row in _target_periods() if row.get("actual_yoy") and row.get("release_date") and row["release_date"] <= current["trade_date"]]
            published_accel = _f(available_actuals[-1]["actual_yoy"]) - _f(available_actuals[-2]["actual_yoy"]) if len(available_actuals) > 1 else 0.0
            oracle = target_by_month.get(month)
            oracle_accel = _f(oracle.get("actual_yoy")) - _f(available_actuals[-1]["actual_yoy"]) if oracle and available_actuals else 0.0
            weights = {
                "buy_hold": 1.0,
                "pure_momentum": scaled if trend_positive else 0.0,
                "published_macro": scaled * (1.0 if published_accel > 0 else .5) if trend_positive else 0.0,
                "alphalens_nowcast": scaled * (1.0 if alpha_accel > 0 else .5) if trend_positive else 0.0,
                "oracle_non_tradable": scaled * (1.0 if oracle_accel > 0 else .5) if trend_positive else 0.0,
            }
            # Signal is formed after this month-end close; the holding interval starts
            # at the next trading day's open and ends at the following rebalance open.
            risk_return = _f(monthly_risk_first[following_month]["open"]) / _f(monthly_risk_first[next_month]["open"]) - 1
            def_return = _f(monthly_def_first[following_month]["open"]) / _f(monthly_def_first[next_month]["open"]) - 1
            for strategy in strategies:
                weight = weights[strategy]
                turnover = abs(weight - prior_weights[strategy])
                net_return = weight * risk_return + (1 - weight) * def_return - turnover * cost_bps / 10000
                navs[strategy] *= 1 + net_return
                prior_weights[strategy] = weight
                returns_by_strategy[strategy].append(net_return)
                nav_by_strategy[strategy].append(navs[strategy])
                turnover_by_strategy[strategy].append(turnover)
                output.append({
                    "trade_month": next_month, "signal_date": current["trade_date"], "strategy": strategy,
                    "cost_bps": cost_bps, "risk_weight": weight, "defensive_weight": 1 - weight,
                    "net_return": net_return, "nav": navs[strategy], "turnover": turnover,
                    "trend_positive": trend_positive, "macro_acceleration": alpha_accel if strategy == "alphalens_nowcast" else "",
                    "tradable": strategy != "oracle_non_tradable",
                })
        metric_rows: list[dict[str, Any]] = []
        for strategy in strategies:
            metric = _annualized_metrics(returns_by_strategy[strategy], nav_by_strategy[strategy], turnover_by_strategy[strategy])
            metric_rows.append({"strategy": strategy, "cost_bps": cost_bps, **metric, "sample_months": len(returns_by_strategy[strategy]), "tradable": strategy != "oracle_non_tradable"})
        existing = _read_csv("macro_strategy_metrics.csv") if cost_bps != 5 else []
        metric_fields = ["strategy", "cost_bps", "annual_return", "annual_volatility", "sharpe", "calmar", "max_drawdown", "monthly_win_rate", "annual_turnover", "sample_months", "tradable"]
        _write_csv("macro_strategy_metrics.csv", metric_fields, [*existing, *metric_rows])
    fields = ["trade_month", "signal_date", "strategy", "cost_bps", "risk_weight", "defensive_weight", "net_return", "nav", "turnover", "trend_positive", "macro_acceleration", "tradable"]
    _write_csv("macro_strategy_nav.csv", fields, output)
    _write_strategy_bootstrap(output)
    return output


def _write_strategy_bootstrap(rows: list[dict[str, Any]]) -> None:
    primary = [row for row in rows if int(row["cost_bps"]) == 10]
    alpha = {row["trade_month"]: _f(row["net_return"]) for row in primary if row["strategy"] == "alphalens_nowcast"}
    trend = {row["trade_month"]: _f(row["net_return"]) for row in primary if row["strategy"] == "pure_momentum"}
    months = sorted(set(alpha) & set(trend))
    differences = [alpha[month] - trend[month] for month in months]
    rng = random.Random(20260810)
    boot: list[float] = []
    block = 3
    if differences:
        for _ in range(1000):
            sample: list[float] = []
            while len(sample) < len(differences):
                start = rng.randrange(len(differences))
                sample.extend(differences[(start + offset) % len(differences)] for offset in range(block))
            boot.append(_mean(sample[:len(differences)]) * 12)
    observed = _mean(differences) * 12 if differences else 0.0
    rows_out = [{
        "comparison": "alphalens_nowcast_minus_pure_momentum", "cost_bps": 10,
        "sample_months": len(differences), "block_months": block, "bootstrap_iterations": len(boot),
        "annualized_net_return_difference": observed, "ci_lower_95": _quantile(boot, .025),
        "ci_upper_95": _quantile(boot, .975),
        "conclusion": "trading_increment_not_established" if not boot or _quantile(boot, .025) <= 0 else "positive_increment_observed",
    }]
    _write_csv("macro_strategy_bootstrap.csv", ["comparison", "cost_bps", "sample_months", "block_months", "bootstrap_iterations", "annualized_net_return_difference", "ci_lower_95", "ci_upper_95", "conclusion"], rows_out)


def build_macro_outputs() -> None:
    features = aggregate_monthly_features()
    forecasts = build_forecasts(features)
    market = ensure_market_data()
    strategy = build_strategy(forecasts, market)
    print(f"宏观层完成：{len(features)} 期特征，{len(forecasts)} 期预测，{len(strategy)} 条策略净值记录")


def load_macro_forecast() -> dict[str, Any]:
    forecasts = _read_csv("macro_forecasts.csv")
    metrics = _read_csv("macro_forecast_metrics.csv")
    features = _read_csv("macro_monthly_features.csv")
    latest = forecasts[-1] if forecasts else {}
    return {
        "target_name": TARGET_NAME, "latest": latest, "history": forecasts,
        "metrics": metrics, "latest_features": features[-1] if features else {},
        "text_increment_conclusion": "文本预测增量不足" if latest.get("text_increment_status") != "evaluated" else "已进入冻结样本外评估",
        "disclaimer": DISCLAIMER,
    }


def load_macro_backtest() -> dict[str, Any]:
    nav = _read_csv("macro_strategy_nav.csv")
    metrics = _read_csv("macro_strategy_metrics.csv")
    bootstrap = _read_csv("macro_strategy_bootstrap.csv")
    primary_nav = [row for row in nav if row.get("cost_bps") == "10"]
    primary_metrics = [row for row in metrics if row.get("cost_bps") == "10"]
    return {
        "risk_asset": {"code": RISK_CODE, "name": "新能源ETF"},
        "defensive_asset": {"code": DEFENSIVE_CODE, "name": "5年期国债ETF"},
        "pre_listing_proxy": {"code": PROXY_CODE, "tradable": False, "used_in_primary_backtest": False},
        "primary_cost_bps": 10, "cost_sensitivity_bps": [5, 10, 20],
        "nav": primary_nav, "metrics": primary_metrics, "bootstrap": bootstrap,
        "rebalance_timing": "月末收盘后形成信号，下一交易日开盘调仓",
        "oracle_warning": "Oracle 使用下一期真实值，不可交易，不用于收益宣传或 OOS 调参。",
        "disclaimer": DISCLAIMER,
    }


def load_macro_status() -> dict[str, Any]:
    target = _read_csv("macro_target_history.csv")
    features = _read_csv("macro_monthly_features.csv")
    forecasts = _read_csv("macro_forecasts.csv")
    covered = sum(int(_f(row.get("document_count"))) > 0 for row in features)
    latest = forecasts[-1] if forecasts else {}
    return {
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "target_name": TARGET_NAME, "target_observations": len(target),
        "target_period": {"start": target[0]["period_start"] if target else "", "end": target[-1]["period_end"] if target else ""},
        "feature_periods": len(features), "text_covered_periods": covered,
        "train_period": "2015-01-01 至 2021-12-31", "validation_period": "2022-01-01 至 2023-12-31",
        "oos_period": "2024-01-01 至最新", "latest_forecast": latest,
        "evidence_warning": "2015—2023 缺少通过现有严格校验的历史文本，当前文本模型不可宣称具有预测增量。" if latest.get("text_increment_status") != "evaluated" else "",
        "research_scope": "预测行业实体景气，不预测股票价格，不提供投资建议",
        "disclaimer": DISCLAIMER,
    }


def live_macro_impact(analysis: dict[str, Any]) -> dict[str, Any]:
    """Return a transparent marginal feature update for one fully analysed text."""
    forecast_payload = load_macro_forecast()
    latest = forecast_payload.get("latest", {})
    before = forecast_payload.get("latest_features", {})
    stocks = analysis.get("stock_results", [])
    factors = [_f(row.get("candidate_factor")) for row in stocks]
    predicate_rows = [item for stock in stocks for item in stock.get("predicate_consensus", [])]
    accepted = sum(bool(row.get("accepted_for_rule")) for row in predicate_rows)
    delta = {
        "document_count": 1, "event_count": len(stocks),
        "stock_count": len({row.get("code") for row in stocks}),
        "true_predicate_count": accepted,
        "qualified_rule_hits": sum(len(row.get("triggered_rules", [])) for row in stocks),
        "factor_mean": _mean(factors),
    }
    after = {key: _f(before.get(key)) + value for key, value in delta.items()}
    text_ready = latest.get("text_increment_status") == "evaluated"
    prediction_before = _f(latest.get("predicted_yoy"))
    prediction_after = prediction_before
    return {
        "target_name": TARGET_NAME, "period_end": latest.get("target_period_end", ""),
        "feature_delta": delta, "feature_after": after,
        "prediction_before": prediction_before, "prediction_after": prediction_after,
        "prediction_change": prediction_after - prediction_before,
        "interval_90": [_f(latest.get("lower_90")), _f(latest.get("upper_90"))],
        "evidence_status": "evaluated" if text_ready else "insufficient_history",
        "explanation": "该文本已进入本月可审计特征，但历史文本覆盖不足，系统不伪造模型系数，因此本次边际预测变化记为 0。" if not text_ready else "按冻结模型系数重算单篇文本的边际贡献。",
        "single_document_warning": "单篇文本只是月度聚合中的一个证据贡献，不代表其独立预测整个行业。",
        "trading_timing": "实时输入只更新研究 Nowcast；策略仅在预定月末产生信号，并于下一交易日调仓。",
        "disclaimer": DISCLAIMER,
    }
