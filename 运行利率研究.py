"""Print the rates MVP status, latest forecast and compact route metrics."""

from __future__ import annotations

import json

from src.rates.engine import load_backtest, load_forecast, load_status


if __name__ == "__main__":
    backtest = load_backtest()
    payload = {
        "status": load_status(),
        "forecast": load_forecast(),
        "backtest": {
            "status": backtest.get("status"),
            "increment_conclusion": backtest.get("increment_conclusion"),
            "routes": [
                {key: row.get(key) for key in ("route", "observations", "accuracy", "macro_f1", "brier")}
                for row in backtest.get("routes", [])
            ],
            "research_warning": backtest.get("research_warning"),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
