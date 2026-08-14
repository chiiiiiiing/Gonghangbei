"""Refresh official inputs and advance the frozen prospective lithium study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
HISTORY_START = "2023-07-21"
RUN_LOG = SAMPLE_DIR / "lithium_prospective_runs.csv"
RUN_FIELDS = [
    "run_at", "requested_end", "latest_controlled_day", "status", "observations",
    "annualized_net_return_difference", "ci_lower_95", "ci_upper_95", "conclusion",
    "freeze_manifest_sha256", "current_inputs_sha256",
]


def default_end_date() -> str:
    now = datetime.now().astimezone()
    target = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    return target.isoformat()


def latest_controlled_day() -> str:
    path = SAMPLE_DIR / "lithium_contract_daily.csv"
    if not path.exists():
        return HISTORY_START
    with path.open(encoding="utf-8", newline="") as handle:
        days = [row.get("trade_date", "") for row in csv.DictReader(handle)]
    return max((day for day in days if day), default=HISTORY_START)


def run(*parts: str) -> None:
    command = [sys.executable, *parts]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def current_inputs_sha256() -> str:
    digest = hashlib.sha256()
    for name in (
        "lithium_contract_daily.csv", "lithium_warehouse_receipts.csv",
        "lithium_texts.csv", "lithium_text_signals.csv", "lithium_rulebook.csv",
    ):
        path = SAMPLE_DIR / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def append_run_log(requested_end: str, prospective: dict) -> None:
    bootstrap = prospective.get("prospective_bootstrap", {})
    manifest = SAMPLE_DIR / "lithium_prospective_freeze.json"
    rows: list[dict[str, str]] = []
    if RUN_LOG.exists():
        with RUN_LOG.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    rows.append({
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "requested_end": requested_end,
        "latest_controlled_day": latest_controlled_day(),
        "status": str(prospective.get("status", "")),
        "observations": str(bootstrap.get("observations", "")),
        "annualized_net_return_difference": str(
            bootstrap.get("annualized_net_return_difference", "")
        ),
        "ci_lower_95": str(bootstrap.get("ci_lower_95", "")),
        "ci_upper_95": str(bootstrap.get("ci_upper_95", "")),
        "conclusion": str(prospective.get("conclusion", "")),
        "freeze_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "current_inputs_sha256": current_inputs_sha256(),
    })
    with RUN_LOG.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=default_end_date())
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--delay", default="0.8")
    args = parser.parse_args()
    latest = latest_controlled_day()
    if args.end < latest:
        raise SystemExit(f"end={args.end} 早于现有最新交易日 {latest}，拒绝截短受控历史")

    run("scripts/lithium_prospective_integrity.py", "--verify")
    run(
        "scripts/fetch_gfex_lithium_data.py",
        "--start", HISTORY_START,
        "--end", args.end,
        "--delay", args.delay,
        "--workers", "1",
    )
    run(
        "scripts/build_lithium_text_corpus.py",
        "--start", HISTORY_START,
        "--end", args.end,
    )
    run("scripts/generate_lithium_local_annotations.py", "--model", args.model)
    run("scripts/lithium_prospective_integrity.py", "--verify")
    run("scripts/record_lithium_prospective_decision.py")

    payload = json.loads((SAMPLE_DIR / "lithium_backtest.json").read_text(encoding="utf-8"))
    prospective = payload.get("prospective_candidate", {})
    append_run_log(args.end, prospective)
    print(json.dumps({
        "latest_controlled_day": latest_controlled_day(),
        "version": prospective.get("version"),
        "status": prospective.get("status"),
        "conclusion": prospective.get("conclusion"),
        "prospective_bootstrap": prospective.get("prospective_bootstrap"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
