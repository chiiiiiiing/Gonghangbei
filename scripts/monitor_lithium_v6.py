"""Compact readiness monitor for the V6 strict prospective increment."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


MIN_SETTLED_DAYS = 63


def main() -> int:
    from app.server import load_lithium_v6_research

    research = load_lithium_v6_research()
    ledger = research.get("decision_ledger", {})
    bootstrap = research.get("prospective_bootstrap", {})
    settled = int(ledger.get("settled_decisions", 0))
    pending = int(ledger.get("pending_decisions", 0))
    recorded = int(ledger.get("recorded_decisions", 0))
    invalid = ledger.get("invalid_decisions", [])
    latest_decision = research.get("latest_decision", {})
    payload = {
        "version": research.get("version", "lithium-v6-quality-text-walkforward-v1"),
        "strict_increment_established": bool(
            research.get("strict_increment_established", False)
        ),
        "latest_signal_date": latest_decision.get("signal_date", ""),
        "recorded_decisions": recorded,
        "settled_decisions": settled,
        "pending_decisions": pending,
        "invalid_decisions": len(invalid),
        "required_settled_days": MIN_SETTLED_DAYS,
        "settled_days_remaining": max(MIN_SETTLED_DAYS - settled, 0),
        "prospective_observations": bootstrap.get("observations", 0),
        "annualized_net_return_difference": bootstrap.get(
            "annualized_net_return_difference", 0.0
        ),
        "bootstrap_ci_lower_95": bootstrap.get("ci_lower_95", 0.0),
        "conclusion": research.get("strict_conclusion", "严格前瞻交易增量待检验"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["strict_increment_established"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
