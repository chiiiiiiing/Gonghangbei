"""Advance official data, V4 inference, and the frozen V5 decision ledger."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RESEARCH_DIR = ROOT / "data" / "research"
SAMPLE_DIR = ROOT / "data" / "sample"
V5_LEDGER = RESEARCH_DIR / "lithium_v5_prospective_decisions.csv"


def run(*parts: str) -> None:
    subprocess.run([sys.executable, *parts], cwd=ROOT, check=True)


def latest_csv_day(path: Path, field: str) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return max((row[field] for row in csv.DictReader(handle)), default="")


def default_end_date() -> str:
    now = datetime.now().astimezone()
    target = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    return target.isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=default_end_date())
    parser.add_argument("--delay", default="0.8")
    args = parser.parse_args()
    try:
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("end 必须使用 YYYY-MM-DD") from exc
    run("scripts/lithium_v5_candidate_integrity.py", "--verify")
    run(
        "scripts/update_lithium_v4_prospective.py",
        "--end", args.end,
        "--delay", args.delay,
    )
    market_day = latest_csv_day(
        SAMPLE_DIR / "lithium_contract_daily.csv", "trade_date"
    )
    decision_day = latest_csv_day(V5_LEDGER, "signal_date")
    status = "already_recorded"
    if decision_day != market_day:
        if market_day < (date.today() - timedelta(days=3)).isoformat():
            raise SystemExit(
                f"最新行情 {market_day} 缺少 V5 决策且已过时，拒绝事后补写"
            )
        run("scripts/record_lithium_v5_prospective_decision.py")
        status = "decision_recorded"
    run("scripts/lithium_v5_candidate_integrity.py", "--verify")
    from app.server import load_lithium_v5_research

    research = load_lithium_v5_research()
    print(json.dumps({
        "status": status,
        "latest_market_day": market_day,
        "latest_decision_day": latest_csv_day(V5_LEDGER, "signal_date"),
        "research": research,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
