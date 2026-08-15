"""Advance official data and the frozen V6 prospective decision ledger."""

from __future__ import annotations

import argparse
import csv
import hashlib
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
V6_LEDGER = RESEARCH_DIR / "lithium_v6_prospective_decisions.csv"
V6_RUN_LOG = RESEARCH_DIR / "lithium_v6_prospective_runs.csv"
MANIFEST = RESEARCH_DIR / "lithium_v6_candidate_freeze.json"
RUN_FIELDS = [
    "run_at", "requested_end", "previous_market_day", "latest_market_day",
    "status", "recorded_decisions", "settled_decisions", "pending_decisions",
    "bootstrap_observations", "annualized_net_return_difference", "ci_lower_95",
    "conclusion", "prefix_manifest_sha256",
]


def run(*parts: str) -> None:
    subprocess.run([sys.executable, *parts], cwd=ROOT, check=True)


def latest_csv_day(path: Path, field: str) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return max((row[field] for row in csv.DictReader(handle)), default="")


def default_end_date() -> str:
    now = datetime.now().astimezone()
    target = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    return target.isoformat()


def current_research() -> dict[str, object]:
    from app.server import load_lithium_v6_research

    return load_lithium_v6_research()


def append_run(
    requested_end: str,
    previous_day: str,
    status: str,
    research: dict[str, object],
) -> None:
    ledger = research.get("decision_ledger", {})
    bootstrap = research.get("prospective_bootstrap", {})
    row = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "requested_end": requested_end,
        "previous_market_day": previous_day,
        "latest_market_day": latest_csv_day(
            SAMPLE_DIR / "lithium_contract_daily.csv", "trade_date"
        ),
        "status": status,
        "recorded_decisions": ledger.get("recorded_decisions", 0),
        "settled_decisions": ledger.get("settled_decisions", 0),
        "pending_decisions": ledger.get("pending_decisions", 0),
        "bootstrap_observations": bootstrap.get("observations", 0),
        "annualized_net_return_difference": bootstrap.get(
            "annualized_net_return_difference", 0
        ),
        "ci_lower_95": bootstrap.get("ci_lower_95", 0),
        "conclusion": research.get("conclusion", "严格前瞻交易增量待检验"),
        "prefix_manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }
    write_header = not V6_RUN_LOG.exists() or V6_RUN_LOG.stat().st_size == 0
    if V6_RUN_LOG.exists():
        with V6_RUN_LOG.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != RUN_FIELDS:
                raise ValueError("V6 前瞻运行日志字段发生变化，拒绝写入")
    with V6_RUN_LOG.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=default_end_date())
    parser.add_argument("--delay", default="0.8")
    args = parser.parse_args()
    try:
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("end 必须使用 YYYY-MM-DD") from exc
    run("scripts/lithium_v6_candidate_integrity.py", "--verify")
    previous_market_day = latest_csv_day(
        SAMPLE_DIR / "lithium_contract_daily.csv", "trade_date"
    )
    run(
        "scripts/update_lithium_v4_prospective.py",
        "--end", args.end,
        "--delay", args.delay,
    )
    market_day = latest_csv_day(
        SAMPLE_DIR / "lithium_contract_daily.csv", "trade_date"
    )
    decision_day = latest_csv_day(V6_LEDGER, "signal_date")
    status = "already_recorded"
    if decision_day != market_day:
        if market_day < (date.today() - timedelta(days=3)).isoformat():
            raise SystemExit(
                f"最新行情 {market_day} 缺少 V6 决策且已过时，拒绝事后补写"
            )
        run("scripts/record_lithium_v6_prospective_decision.py")
        status = "decision_recorded"
    run("scripts/lithium_v6_candidate_integrity.py", "--verify")
    research = current_research()
    append_run(args.end, previous_market_day, status, research)
    print(json.dumps({
        "status": status,
        "latest_market_day": market_day,
        "latest_decision_day": latest_csv_day(V6_LEDGER, "signal_date"),
        "research": research,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
