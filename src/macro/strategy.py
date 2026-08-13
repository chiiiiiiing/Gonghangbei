"""Trend, volatility scaling and macro-confirmation strategy research."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean, pstdev
from typing import Any


RISK_ASSET = "516160"
DEFENSIVE_ASSET = "511010"
RESEARCH_PROXY = "399808"
TARGET_VOLATILITY = 0.10
TRADING_DAYS = 252
MOMENTUM_MONTHS = 12
VOLATILITY_DAYS = 60
COST_BPS = (5, 10, 20)


def _returns(closes: list[float]) -> list[float]:
    return [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]


def _month_end_indices(rows: list[dict[str, Any]]) -> list[int]:
    indices = []
    for index, row in enumerate(rows):
        next_month = rows[index + 1]["trade_date"][:7] if index + 1 < len(rows) else ""
        if row["trade_date"][:7] != next_month:
            indices.append(index)
    return indices


def _annualized_volatility(closes: list[float]) -> float:
    returns = _returns(closes)
    return pstdev(returns) * math.sqrt(TRADING_DAYS) if len(returns) >= 2 else 0.0


def _risk_weight(momentum_positive: bool, macro_positive: bool | None, volatility: float) -> float:
    if not momentum_positive or volatility <= 0:
        return 0.0
    scaled = min(TARGET_VOLATILITY / volatility, 1.0)
    if macro_positive is False:
        return 0.5 * scaled
    return scaled


def build_monthly_signals(
    market_rows: list[dict[str, str]],
    forecasts: list[dict[str, str]],
    actual_targets: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        code = row["asset_code"]
        if code == RESEARCH_PROXY and str(row["is_tradable"]).lower() == "true":
            raise ValueError("399808 是不可交易研究代理，is_tradable 必须为 false")
        by_asset[code].append(
            {
                **row,
                "open": float(row["open"]),
                "close": float(row["close"]),
            }
        )
    if RISK_ASSET not in by_asset or DEFENSIVE_ASSET not in by_asset:
        return []
    for rows in by_asset.values():
        rows.sort(key=lambda row: row["trade_date"])
    risk = by_asset[RISK_ASSET]
    defensive_by_date = {row["trade_date"]: row for row in by_asset[DEFENSIVE_ASSET]}
    forecast_by_period = {row["period_end"]: row for row in forecasts}
    actual_by_period = {row["period_end"]: row for row in actual_targets}
    signals: list[dict[str, Any]] = []
    for index in _month_end_indices(risk):
        if index + 1 >= len(risk) or index < VOLATILITY_DAYS:
            continue
        signal_row = risk[index]
        next_row = risk[index + 1]
        if next_row["trade_date"] not in defensive_by_date:
            continue
        prior_year = [
            row for row in risk[: index + 1]
            if row["trade_date"] >= f"{int(signal_row['trade_date'][:4]) - 1:04d}-{signal_row['trade_date'][5:]}"
        ]
        if len(prior_year) < 200:
            continue
        momentum = signal_row["close"] / prior_year[0]["close"] - 1.0
        volatility = _annualized_volatility([row["close"] for row in risk[index - VOLATILITY_DAYS : index + 1]])
        period_end = signal_row["trade_date"]
        forecast = forecast_by_period.get(period_end)
        actual = actual_by_period.get(period_end)
        latest_actual_positive = None
        if actual:
            latest_actual_positive = float(actual["target_value"]) - float(forecast["latest_released_value"]) > 0 if forecast else None
        alpha_positive = float(forecast["predicted_acceleration"]) > 0 if forecast else None
        known_positive = None
        if forecast:
            known_positive = float(forecast["latest_released_value"]) - float(
                forecast.get("previous_released_value", forecast["latest_released_value"])
            ) > 0
        trend_positive = momentum > 0
        signals.append(
            {
                "signal_date": period_end,
                "trade_date": next_row["trade_date"],
                "momentum_12m": momentum,
                "volatility_60d": volatility,
                "weights": {
                    "buy_hold": 1.0,
                    "trend": _risk_weight(trend_positive, None, volatility),
                    "trend_latest_macro": _risk_weight(trend_positive, known_positive, volatility),
                    "trend_alphalens": _risk_weight(trend_positive, alpha_positive, volatility),
                    "trend_oracle": _risk_weight(trend_positive, latest_actual_positive, volatility),
                },
                "oracle_tradable": False,
            }
        )
    return signals


def _strategy_metrics(returns: list[float], turnovers: list[float]) -> dict[str, float | int]:
    if not returns:
        return {
            "month_count": 0,
            "annualized_return": 0.0,
            "annualized_volatility": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "max_drawdown": 0.0,
            "monthly_win_rate": 0.0,
            "annualized_turnover": 0.0,
            "worst_month": 0.0,
        }
    wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, wealth / peak - 1.0)
    annualized_return = wealth ** (12.0 / len(returns)) - 1.0
    volatility = pstdev(returns) * math.sqrt(12) if len(returns) >= 2 else 0.0
    return {
        "month_count": len(returns),
        "annualized_return": annualized_return,
        "annualized_volatility": volatility,
        "sharpe": annualized_return / volatility if volatility else 0.0,
        "calmar": annualized_return / abs(max_drawdown) if max_drawdown else 0.0,
        "max_drawdown": max_drawdown,
        "monthly_win_rate": sum(value > 0 for value in returns) / len(returns),
        "annualized_turnover": mean(turnovers) * 12 if turnovers else 0.0,
        "worst_month": min(returns),
    }


def time_block_bootstrap(
    differences: list[float],
    block_size: int = 6,
    iterations: int = 2000,
    seed: int = 20260813,
) -> dict[str, float | int]:
    if len(differences) < block_size * 2:
        return {"iterations": 0, "mean_difference": mean(differences) if differences else 0.0, "lower_95": 0.0, "upper_95": 0.0}
    blocks = [differences[index : index + block_size] for index in range(0, len(differences), block_size)]
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        sample = [value for _index in range(len(blocks)) for value in rng.choice(blocks)]
        samples.append(mean(sample[: len(differences)]))
    samples.sort()
    return {
        "iterations": iterations,
        "mean_difference": mean(differences),
        "lower_95": samples[int(iterations * 0.025)],
        "upper_95": samples[min(int(iterations * 0.975), iterations - 1)],
    }


def evaluate_macro_confirmation_strategy(
    market_rows: list[dict[str, str]],
    forecasts: list[dict[str, str]],
    actual_targets: list[dict[str, str]],
) -> dict[str, Any]:
    signals = build_monthly_signals(market_rows, forecasts, actual_targets)
    if len(signals) < 24:
        return {
            "status": "insufficient_data",
            "conclusion": "尚不能证明交易增量",
            "signal_count": len(signals),
            "results_by_cost_bps": {},
            "bootstrap": {"iterations": 0, "lower_95": 0.0, "upper_95": 0.0},
            "oracle_label": "Oracle：不可交易，仅作为理论参照上限",
        }
    by_asset_date = {(row["asset_code"], row["trade_date"]): row for row in market_rows}
    strategy_names = list(signals[0]["weights"])
    results_by_cost: dict[str, Any] = {}
    return_cache: dict[tuple[int, str], list[float]] = {}
    for cost_bps in COST_BPS:
        returns_by_strategy: dict[str, list[float]] = defaultdict(list)
        turnover_by_strategy: dict[str, list[float]] = defaultdict(list)
        previous_weights = {name: 0.0 for name in strategy_names}
        for current, following in zip(signals, signals[1:]):
            risk_entry = float(by_asset_date[(RISK_ASSET, current["trade_date"])]["open"])
            risk_exit = float(by_asset_date[(RISK_ASSET, following["trade_date"])]["open"])
            defensive_entry = float(by_asset_date[(DEFENSIVE_ASSET, current["trade_date"])]["open"])
            defensive_exit = float(by_asset_date[(DEFENSIVE_ASSET, following["trade_date"])]["open"])
            risk_return = risk_exit / risk_entry - 1.0
            defensive_return = defensive_exit / defensive_entry - 1.0
            for name, weight in current["weights"].items():
                turnover = abs(weight - previous_weights[name])
                cost = turnover * cost_bps / 10000.0
                returns_by_strategy[name].append(weight * risk_return + (1.0 - weight) * defensive_return - cost)
                turnover_by_strategy[name].append(turnover)
                previous_weights[name] = weight
        results_by_cost[str(cost_bps)] = {
            name: _strategy_metrics(returns_by_strategy[name], turnover_by_strategy[name])
            for name in strategy_names
        }
        return_cache[(cost_bps, "trend")] = returns_by_strategy["trend"]
        return_cache[(cost_bps, "trend_alphalens")] = returns_by_strategy["trend_alphalens"]
    differences = [
        enhanced - trend
        for enhanced, trend in zip(return_cache[(10, "trend_alphalens")], return_cache[(10, "trend")])
    ]
    bootstrap = time_block_bootstrap(differences)
    trading_increment = bootstrap["lower_95"] > 0
    return {
        "status": "trading_increment_supported" if trading_increment else "insufficient_trading_increment",
        "conclusion": "交易增量通过时间块检验" if trading_increment else "尚不能证明交易增量",
        "signal_count": len(signals),
        "results_by_cost_bps": results_by_cost,
        "bootstrap": bootstrap,
        "oracle_label": "Oracle：不可交易，仅作为理论参照上限",
    }
