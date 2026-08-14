"""Build the V5 causal market baseline and quality-gated text-alpha report."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lithium.engine import _read_csv, build_main_continuous  # noqa: E402
from src.lithium.walkforward import build_report  # noqa: E402


RESEARCH_DIR = ROOT / "data" / "research"
REPORT_FILE = RESEARCH_DIR / "lithium_v5_walkforward_report.json"
ROWS_FILE = RESEARCH_DIR / "lithium_v5_walkforward_rows.csv"
ROW_FIELDS = [
    "trade_date", "signal_date", "split", "strategy", "position",
    "market_open_return", "turnover", "cost_bps", "net_return", "nav",
    "baseline_position", "active_text_score", "position_delta",
    "text_confirmed", "realized_volatility",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    contracts = _read_csv("lithium_contract_daily.csv")
    continuous = build_main_continuous(contracts)
    signals = read_csv(RESEARCH_DIR / "lithium_v3_signals.csv")
    texts = read_csv(RESEARCH_DIR / "lithium_v3_texts.csv")
    report, rows = build_report(continuous, contracts, signals, texts)
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with ROWS_FILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in ROW_FIELDS} for row in rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
