"""One-command daily pipeline for the V6 strict prospective increment."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def default_end_date() -> str:
    now = datetime.now().astimezone()
    target = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    return target.isoformat()


def run(*parts: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *parts],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=default_end_date())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--delay", default="0.8")
    parser.add_argument("--skip-live-collector", action="store_true")
    args = parser.parse_args()
    try:
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("end 必须使用 YYYY-MM-DD") from exc

    collector_status = "skipped"
    if not args.skip_live_collector:
        result = run(
            "scripts/collect_live_cninfo_lithium_signals.py",
            "--end", args.end,
            "--days", str(args.days),
            "--delay", args.delay,
            check=False,
        )
        collector_status = "ok" if result.returncode == 0 else "failed"
        if result.returncode:
            print(
                "[daily-pipeline] live collector failed; continuing with V6 baseline/decision. "
                f"stderr={result.stderr.strip()[:800]}"
            )

    update = run(
        "scripts/update_lithium_v6_prospective.py",
        "--end", args.end,
        "--delay", args.delay,
    )
    print(update.stdout.strip())

    monitor = run(
        "scripts/monitor_lithium_v6.py",
        check=False,
    )
    print("[daily-pipeline] monitor:")
    print(monitor.stdout.strip())
    if monitor.returncode != 0:
        print(
            "[daily-pipeline] strict increment is not established yet; "
            "monitor returned non-zero as expected until 63 settled days pass."
        )

    print(json.dumps({
        "end_date": args.end,
        "live_collector": collector_status,
        "update_status": "ok",
        "monitor_exit_code": monitor.returncode,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
