"""Freeze a Validation-selected additive RIFT alpha before OOS evaluation."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lithium.engine import (  # noqa: E402
    RIFT_ADDITIVE_STRATEGY,
    VALIDATION_END,
    _metrics,
    _read_csv,
    _strategy_rows,
    block_bootstrap_increment,
    build_main_continuous,
)


RESEARCH_DIR = ROOT / "data" / "research"
FREEZE_FILE = RESEARCH_DIR / "lithium_v4_additive_freeze.json"
WEIGHT_GRID = (0.5, 1.0, 2.0, 4.0)
FORMULA = "clip(trend_score + weight * active_text_score, -1, 1)"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_signals() -> list[dict[str, Any]]:
    with (RESEARCH_DIR / "lithium_v3_signals.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for field in (
            "direction_score", "zero_shot_score", "confidence",
            "zero_shot_confidence",
        ):
            row[field] = float(row.get(field, 0) or 0)
    return rows


def validation_candidate(weight: float) -> dict[str, Any]:
    cutoff = VALIDATION_END.isoformat()
    contracts = [
        row for row in _read_csv("lithium_contract_daily.csv")
        if row["trade_date"] <= cutoff
    ]
    signals = [row for row in load_signals() if row["publish_time"][:10] <= cutoff]
    rows = _strategy_rows(
        build_main_continuous(contracts), signals, 5.0, contracts,
        additive_weight=weight,
    )
    if any(row["split"] == "oos" for row in rows):
        raise RuntimeError("冻结阶段不得生成 OOS 策略行")
    metrics = _metrics(
        rows, "validation", strategies=("pure_trend", RIFT_ADDITIVE_STRATEGY)
    )
    bootstrap = block_bootstrap_increment(
        rows, split="validation", enhanced_strategy=RIFT_ADDITIVE_STRATEGY
    )
    candidate = next(row for row in metrics if row["strategy"] == RIFT_ADDITIVE_STRATEGY)
    return {"weight": weight, "candidate_metrics": candidate, "bootstrap": bootstrap}


def build_freeze() -> dict[str, Any]:
    candidates = [validation_candidate(weight) for weight in WEIGHT_GRID]
    selected = max(
        candidates,
        key=lambda row: (row["candidate_metrics"]["sharpe"], -row["weight"]),
    )
    return {
        "version": "lithium-v4-additive-alpha-freeze-v1",
        "status": "validation_selected_oos_not_evaluated",
        "selection_boundary": "仅使用不晚于2025-12-31的行情、信号和收益选权重",
        "validation_end": VALIDATION_END.isoformat(),
        "oos_start": "2026-01-01",
        "strategy": RIFT_ADDITIVE_STRATEGY,
        "formula": FORMULA,
        "cost_bps": 5.0,
        "weight_grid": list(WEIGHT_GRID),
        "selection_metric": "validation_sharpe",
        "selected_weight": selected["weight"],
        "validation_candidates": candidates,
        "input_sha256": {
            name: sha256_file(RESEARCH_DIR / name)
            for name in (
                "lithium_v3_rulebook.csv", "lithium_v3_directions.csv",
                "lithium_v3_signals.csv",
            )
        },
        "oos_metrics": None,
        "oos_bootstrap": None,
    }


def main() -> None:
    if FREEZE_FILE.exists():
        raise SystemExit(f"冻结文件已存在，拒绝覆盖: {FREEZE_FILE}")
    payload = build_freeze()
    FREEZE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
