"""CSV loader helpers for AlphaLens demo data.

This module intentionally uses the Python standard library so C-side checks can
run even before pandas and Streamlit are installed.
"""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"


def load_table(filename: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / filename
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_core_tables() -> dict[str, list[dict[str, str]]]:
    filenames = [
        "stock_pool.csv",
        "raw_documents.csv",
        "entity_links.csv",
        "events.csv",
        "predicates.csv",
        "market_data.csv",
    ]
    return {filename: load_table(filename) for filename in filenames}


def load_research_tables() -> dict[str, list[dict[str, str]]]:
    filenames = [
        "predicate_matrix.csv",
        "event_forward_returns.csv",
        "rules.csv",
        "factors.csv",
        "factor_snapshot.csv",
        "group_returns.csv",
        "rank_ic_timeseries.csv",
        "backtest_metrics.csv",
    ]
    return {filename: load_table(filename) for filename in filenames}
