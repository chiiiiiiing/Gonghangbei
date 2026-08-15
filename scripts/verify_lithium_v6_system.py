"""Run the V5/V6 integrity checks and V6 API/state smoke checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STATE_FILE = ROOT / "data" / "research" / "lithium_v6_system_state.json"


def run(*parts: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *parts],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    for script in (
        "scripts/lithium_v5_candidate_integrity.py",
        "scripts/lithium_v6_candidate_integrity.py",
    ):
        result = run(script, "--verify")
        ok = result.returncode == 0
        checks.append((script, ok, result.stdout.strip()[-400:]))
    from app.server import load_lithium_v6_research

    research = load_lithium_v6_research()
    ledger = research.get("decision_ledger", {})
    monitor = research.get("monitor", {})
    smoke = {
        "version": research.get("version", ""),
        "candidate_integrity": research.get("candidate_integrity", {}),
        "recorded_decisions": ledger.get("recorded_decisions", 0),
        "settled_decisions": ledger.get("settled_decisions", 0),
        "pending_decisions": ledger.get("pending_decisions", 0),
        "strict_increment_established": research.get(
            "strict_increment_established", False
        ),
        "monitor": monitor,
    }
    payload = {
        "integrity_checks": [
            {"script": script, "ok": ok, "tail": tail}
            for script, ok, tail in checks
        ],
        "smoke": smoke,
    }
    STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
