"""重建 AlphaLens 宏观 Nowcast 特征、规则路线和评估结果。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.macro.pipeline import build_macro_research


ROOT = Path(__file__).resolve().parent
PROTECTED_INPUTS = [
    ROOT / "data" / "sample" / "raw_documents.csv",
    ROOT / "data" / "sample" / "macro_targets.csv",
]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED_INPUTS}
    result = build_macro_research()
    after = {path: file_hash(path) for path in PROTECTED_INPUTS}
    if before != after:
        raise RuntimeError("宏观流水线改写了受保护输入，已终止")
    print(
        json.dumps(
            {
                "status": result["status"],
                "conclusion": result["conclusion"],
                "selected_route": result["selected_route"],
                "target_counts": result["target_counts"],
                "data_audit": result["data_audit"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
