"""只重建 AlphaLens 派生数据，并确保人工核验文本不被覆盖。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.backtest.demo_engine import main as build_research_outputs
from src.macro.engine import build_macro_outputs
from src.macro.history import build_historical_text_outputs
from src.pipeline.extract_events_rule_based import main as extract_events
from src.pipeline.link_entities import main as link_entities


ROOT = Path(__file__).resolve().parent
RAW_DOCUMENTS = ROOT / "data" / "sample" / "raw_documents.csv"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    before = file_hash(RAW_DOCUMENTS)
    link_entities()
    extract_events()
    build_research_outputs()
    build_historical_text_outputs()
    build_macro_outputs()
    after = file_hash(RAW_DOCUMENTS)
    if before != after:
        raise RuntimeError("raw_documents.csv 在重算过程中发生变化，已终止")
    print(f"流水线完成，原始文本未变：{after}")


if __name__ == "__main__":
    main()
