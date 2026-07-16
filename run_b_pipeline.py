"""Run the offline B-side data pipeline for AlphaLens samples."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from src.pipeline.extract_events_rule_based import main as extract_events
from src.pipeline.generate_sample_inputs import generate_sample_inputs
from src.pipeline.ground_predicates_rule_based import main as ground_predicates
from src.pipeline.link_entities import main as link_entities


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AlphaLens B-side pipeline.")
    parser.add_argument(
        "--preserve-inputs",
        action="store_true",
        help=(
            "Preserve existing stock_pool.csv, raw_documents.csv and market_data.csv. "
            "This is the default behavior."
        ),
    )
    parser.add_argument(
        "--skip-sample-generation",
        action="store_true",
        help="Skip demo input generation entirely and only rebuild entity links, events and predicates.",
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


def main(
    argv: Sequence[str] | None = None,
    *,
    skip_sample_generation: bool = False,
    force_sample_generation: bool = False,
) -> None:
    if argv is not None:
        args = parse_args(argv)
        skip_sample_generation = args.skip_sample_generation
        force_sample_generation = args.force_sample_generation

    if skip_sample_generation:
        print("[AlphaLens] Skip sample input generation; preserving existing data/sample input CSVs.")
    else:
        generate_sample_inputs(force_sample_generation=force_sample_generation)
    link_entities()
    extract_events()
    ground_predicates()
    if force_sample_generation:
        input_status = "demo inputs regenerated"
    elif skip_sample_generation:
        input_status = "existing inputs preserved"
    else:
        input_status = "existing inputs preserved unless missing"
    print(
        "B pipeline complete: "
        f"{input_status}; entity_links, events and predicates regenerated from current raw_documents.csv."
    )


if __name__ == "__main__":
    main(sys.argv[1:])
