"""Append official GFEX data and advance the frozen V4 prospective study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SAMPLE_DIR = ROOT / "data" / "sample"
RESEARCH_DIR = ROOT / "data" / "research"
RUN_LOG = RESEARCH_DIR / "lithium_v4_prospective_runs.csv"
MANIFEST = RESEARCH_DIR / "lithium_v4_prospective_freeze.json"
RUN_FIELDS = [
    "run_at", "requested_end", "previous_market_day", "latest_market_day",
    "status", "recorded_decisions", "settled_decisions", "pending_decisions",
    "bootstrap_observations", "annualized_net_return_difference", "ci_lower_95",
    "conclusion", "prefix_manifest_sha256",
]


def default_end_date() -> str:
    now = datetime.now().astimezone()
    target = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    return target.isoformat()


def latest_market_day() -> str:
    path = SAMPLE_DIR / "lithium_contract_daily.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return max(row["trade_date"] for row in csv.DictReader(handle))


def latest_decision_day() -> str:
    path = RESEARCH_DIR / "lithium_v4_prospective_decisions.csv"
    if not path.exists():
        return ""
    with path.open(encoding="utf-8", newline="") as handle:
        return max((row["signal_date"] for row in csv.DictReader(handle)), default="")


def run(*parts: str) -> None:
    subprocess.run([sys.executable, *parts], cwd=ROOT, check=True)


def current_research() -> dict[str, Any]:
    from app.server import load_lithium_v4_research

    return load_lithium_v4_research()


def append_run(
    requested_end: str,
    previous_day: str,
    status: str,
    research: dict[str, Any],
) -> None:
    ledger = research.get("decision_ledger", {})
    bootstrap = research.get("prospective_bootstrap", {})
    row = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "requested_end": requested_end,
        "previous_market_day": previous_day,
        "latest_market_day": latest_market_day(),
        "status": status,
        "recorded_decisions": ledger.get("recorded_decisions", 0),
        "settled_decisions": ledger.get("settled_decisions", 0),
        "pending_decisions": ledger.get("pending_decisions", 0),
        "bootstrap_observations": bootstrap.get("observations", 0),
        "annualized_net_return_difference": bootstrap.get(
            "annualized_net_return_difference", 0
        ),
        "ci_lower_95": bootstrap.get("ci_lower_95", 0),
        "conclusion": research.get("conclusion", "前瞻交易增量待检验"),
        "prefix_manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }
    write_header = not RUN_LOG.exists() or RUN_LOG.stat().st_size == 0
    if RUN_LOG.exists():
        with RUN_LOG.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != RUN_FIELDS:
                raise ValueError("V4 前瞻运行日志字段发生变化，拒绝写入")
    with RUN_LOG.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def new_fetch_errors(previous_day: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / "lithium_gfex_fetch_audit.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            row for row in csv.DictReader(handle)
            if row["trade_date"] > previous_day
            and str(row["status"]).startswith("error:")
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=default_end_date())
    parser.add_argument("--delay", default="0.8")
    args = parser.parse_args()
    try:
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("end 必须使用 YYYY-MM-DD") from exc
    previous = latest_market_day()
    if args.end < previous:
        raise SystemExit(f"end={args.end} 早于当前最新行情 {previous}，拒绝截短")

    run("scripts/lithium_v4_prospective_integrity.py", "--verify")
    if args.end == previous:
        if latest_decision_day() == previous:
            research = current_research()
            append_run(args.end, previous, "no_new_market_data", research)
            print(json.dumps({
                "status": "no_new_market_data",
                "latest_market_day": previous,
                "research": research,
            }, ensure_ascii=False, indent=2))
            return
        if previous < (date.today() - timedelta(days=3)).isoformat():
            raise SystemExit(f"最新行情 {previous} 缺少前瞻决策且已过时，拒绝事后补写")
        run("scripts/record_lithium_v4_prospective_decision.py")
        run("scripts/lithium_v4_prospective_integrity.py", "--verify")
        research = current_research()
        append_run(args.end, previous, "decision_recovered", research)
        print(json.dumps({
            "status": "decision_recovered",
            "latest_market_day": previous,
            "research": research,
        }, ensure_ascii=False, indent=2))
        return

    run(
        "scripts/fetch_gfex_lithium_data.py",
        "--start", "2023-07-21", "--end", args.end,
        "--delay", args.delay, "--workers", "1",
    )
    run("scripts/lithium_v4_prospective_integrity.py", "--verify")
    errors = new_fetch_errors(previous)
    if errors:
        raise SystemExit(f"新增区间存在 {len(errors)} 个广期所请求错误，拒绝生成决策")
    latest = latest_market_day()
    if latest <= previous:
        research = current_research()
        append_run(args.end, previous, "no_new_trading_day", research)
        print(json.dumps({
            "status": "no_new_trading_day",
            "requested_end": args.end,
            "latest_market_day": latest,
        }, ensure_ascii=False, indent=2))
        return
    if latest < (date.today() - timedelta(days=3)).isoformat():
        raise SystemExit(f"最新行情 {latest} 已过时，拒绝事后补写前瞻决策")

    run("scripts/record_lithium_v4_prospective_decision.py")
    run("scripts/lithium_v4_prospective_integrity.py", "--verify")
    research = current_research()
    append_run(args.end, previous, "decision_recorded", research)
    print(json.dumps({
        "status": "decision_recorded",
        "latest_market_day": latest,
        "research": research,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
