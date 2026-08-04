"""将人工确认后的来源清单安全暂存，并可显式合并到样例输入。"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.ingestion.common import sha256
from src.ingestion.text_import import apply_staged, stage_manifest


ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT / "data" / "sample" / "raw_documents.csv"
STAGING_DIR = ROOT / "data" / "external" / "文本导入暂存"


def main() -> None:
    parser = argparse.ArgumentParser(description="校验并导入 AlphaLens 真实文本")
    parser.add_argument("manifest", type=Path, help="锁定字段格式的 CSV 采集清单")
    parser.add_argument("--apply", action="store_true", help="校验通过后显式合并到 raw_documents.csv")
    args = parser.parse_args()
    before = sha256(DESTINATION)
    staged = stage_manifest(args.manifest.resolve(), STAGING_DIR)
    if not args.apply:
        print(f"校验通过，已暂存：{staged}")
        print(f"raw_documents.csv 未修改：{before}")
        return
    summary = apply_staged(staged, DESTINATION)
    after = sha256(DESTINATION)
    print(f"导入完成：{summary}；输入哈希 {before} -> {after}")


if __name__ == "__main__":
    main()
