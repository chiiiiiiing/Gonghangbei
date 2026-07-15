"""Run AlphaLens local pipeline end to end."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from run_b_pipeline import main as run_b_pipeline
from src.backtest.demo_engine import main as build_research_outputs
from src.report.generate_research_report import main as generate_research_report
from scripts.prepare_b_handoff_materials import main as prepare_b_handoff_materials
from scripts.update_b_task_docs import main as update_b_task_docs
from scripts.validate_b_data import main as validate_b_data
from scripts.validate_delivery_package import main as validate_delivery_package
from scripts.validate_input_preservation import main as validate_input_preservation
from scripts.validate_manual_review_results import main as validate_manual_review_results
from scripts.validate_real_market_data import main as validate_real_market_data
from scripts.validate_research_outputs import main as validate_research_outputs


def run_step(name: str, func) -> None:
    print(f"[AlphaLens] {name} ...")
    result = func()
    if isinstance(result, int) and result != 0:
        raise SystemExit(result)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AlphaLens local pipeline end to end.")
    parser.add_argument(
        "--preserve-inputs",
        action="store_true",
        help=(
            "Preserve existing stock_pool.csv, raw_documents.csv and market_data.csv. "
            "This is the default and safe mode after manual verification starts."
        ),
    )
    parser.add_argument(
        "--skip-sample-generation",
        action="store_true",
        help="Skip demo input generation entirely and rebuild downstream outputs from current CSV inputs.",
    )
    parser.add_argument(
        "--force-sample-generation",
        action="store_true",
        help="Overwrite demo input CSVs. Do not use after real texts or real market data have been entered.",
    )
    args = parser.parse_args(argv)
    if args.skip_sample_generation and args.force_sample_generation:
        parser.error("--skip-sample-generation and --force-sample-generation cannot be used together")
    if args.preserve_inputs and args.force_sample_generation:
        parser.error("--preserve-inputs and --force-sample-generation cannot be used together")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv or [])
    run_step(
        "Run B-side pipeline from current inputs",
        lambda: run_b_pipeline(
            skip_sample_generation=args.skip_sample_generation,
            force_sample_generation=args.force_sample_generation,
        ),
    )
    if not args.force_sample_generation:
        run_step("Validate raw document input preservation", validate_input_preservation)
    run_step("Build rule/factor/backtest outputs", build_research_outputs)
    run_step("Prepare B handoff materials", prepare_b_handoff_materials)
    run_step("Validate manual review result values", validate_manual_review_results)
    run_step("Generate research report", generate_research_report)
    run_step("Validate B-side data", validate_b_data)
    run_step("Validate market data import format", validate_real_market_data)
    run_step("Validate research outputs", validate_research_outputs)
    run_step("Update B-role local task docs", update_b_task_docs)
    run_step("Validate delivery package", validate_delivery_package)
    print("[AlphaLens] Pipeline complete.")


if __name__ == "__main__":
    main(sys.argv[1:])
