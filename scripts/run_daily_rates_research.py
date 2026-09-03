"""Refresh official rates inputs and rebuild auditable research artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.engine import build_rates_outputs  # noqa: E402


RUN_LEDGER = ROOT / "data" / "runtime" / "rates_daily_runs.jsonl"


def _run(script: str, arguments: list[str]) -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-market", action="store_true")
    parser.add_argument("--skip-text", action="store_true")
    parser.add_argument("--annotate-llm", action="store_true")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--text-start-date", default="2020-01-01")
    args = parser.parse_args()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    ledger: dict[str, Any] = {
        "started_at": started_at, "status": "running",
        "market_refreshed": not args.skip_market, "text_refreshed": not args.skip_text,
        "llm_annotation_requested": args.annotate_llm,
    }
    RUN_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not args.skip_market:
            _run("fetch_rates_market_data.py", ["--start-year", str(args.start_year)])
        if not args.skip_text:
            _run("fetch_rates_policy_texts.py", ["--start-date", args.text_start_date])
        if args.annotate_llm:
            _run("annotate_rates_policy_texts.py", [])
        ledger["outputs"] = build_rates_outputs()
        ledger["status"] = "success"
    except Exception as exc:
        ledger["status"] = "failed"
        ledger["error"] = str(exc)
        raise
    finally:
        ledger["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        with RUN_LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(ledger, ensure_ascii=False) + "\n")
    print(json.dumps(ledger, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
