"""Reveal the frozen additive RIFT candidate on 2026 OOS exactly once."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lithium.engine import (  # noqa: E402
    RIFT_ADDITIVE_STRATEGY,
    _metrics,
    _read_csv,
    _strategy_rows,
    block_bootstrap_increment,
    build_main_continuous,
)


RESEARCH_DIR = ROOT / "data" / "research"
FREEZE_FILE = RESEARCH_DIR / "lithium_v4_additive_freeze.json"
REPORT_FILE = RESEARCH_DIR / "lithium_v4_additive_oos_report.json"


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


def verified_freeze() -> dict[str, Any]:
    freeze = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
    if freeze.get("status") != "validation_selected_oos_not_evaluated":
        raise RuntimeError("冻结状态不允许 OOS 揭盲")
    if freeze.get("oos_metrics") is not None or freeze.get("oos_bootstrap") is not None:
        raise RuntimeError("冻结文件包含 OOS 结果，拒绝作为揭盲依据")
    for name, expected in freeze["input_sha256"].items():
        actual = sha256_file(RESEARCH_DIR / name)
        if actual != expected:
            raise RuntimeError(f"冻结输入哈希不一致: {name}")
    return freeze


def candidate_result(cost_bps: float, weight: float) -> dict[str, Any]:
    contracts = _read_csv("lithium_contract_daily.csv")
    rows = _strategy_rows(
        build_main_continuous(contracts), load_signals(), cost_bps, contracts,
        additive_weight=weight,
    )
    metrics = _metrics(
        rows, "oos", strategies=("pure_trend", RIFT_ADDITIVE_STRATEGY)
    )
    bootstrap = block_bootstrap_increment(
        rows, split="oos", enhanced_strategy=RIFT_ADDITIVE_STRATEGY
    )
    return {"cost_bps": cost_bps, "metrics": metrics, "bootstrap": bootstrap}


def main() -> None:
    if REPORT_FILE.exists():
        raise SystemExit(f"OOS 报告已存在，拒绝覆盖: {REPORT_FILE}")
    freeze = verified_freeze()
    weight = float(freeze["selected_weight"])
    main_result = candidate_result(float(freeze["cost_bps"]), weight)
    sensitivity = [
        candidate_result(cost, weight)
        for cost in (2.0, 10.0)
    ]
    bootstrap = main_result["bootstrap"]
    established = bootstrap["conclusion"] == "positive_increment_established"
    freeze_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    report = {
        "version": "lithium-v4-additive-alpha-oos-v1",
        "status": "oos_revealed",
        "freeze_commit": freeze_commit,
        "freeze_sha256": sha256_file(FREEZE_FILE),
        "strategy": RIFT_ADDITIVE_STRATEGY,
        "formula": freeze["formula"],
        "selected_weight": weight,
        "main_result": main_result,
        "cost_sensitivity": sensitivity,
        "increment_established": established,
        "conclusion": "交易增量成立" if established else "交易增量未建立",
        "acceptance_gate": "成本后OOS收益差为正且3个月时间块Bootstrap 95%下界大于0",
    }
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
