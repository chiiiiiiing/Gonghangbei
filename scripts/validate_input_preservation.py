"""Verify safe pipeline mode does not rewrite raw_documents.csv."""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_b_pipeline import main as run_b_pipeline

RAW_DOCUMENTS_PATH = ROOT / "data" / "sample" / "raw_documents.csv"
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "流水线输入保护验证报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"


def today() -> str:
    return date.today().isoformat()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_report(before_hash: str, after_hash: str, errors: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AlphaLens 流水线输入保护验证报告",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 结论",
        "",
        f"- Fatal errors: {len(errors)}",
        f"- raw_documents.csv 运行前 SHA256：`{before_hash}`",
        f"- raw_documents.csv 运行后 SHA256：`{after_hash}`",
        f"- 输入保护结论：{'通过' if not errors else '不通过'}",
        "",
        "## 验证动作",
        "",
        "- 调用 `run_b_pipeline.py` 的默认安全模式。",
        "- 该模式会保留已有 `stock_pool.csv`、`raw_documents.csv`、`market_data.csv`，只重建实体链接、事件和谓词。",
        "- 真实文本开始写入后不要使用 `--force-sample-generation`。",
        "",
        "## Errors",
        "",
    ]
    lines.extend([f"- {item}" for item in errors] or ["- 无"])
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not RAW_DOCUMENTS_PATH.exists():
        errors = [f"{RAW_DOCUMENTS_PATH.relative_to(ROOT)} does not exist"]
        write_report("", "", errors)
        print(f"input_preservation_errors={len(errors)}")
        return 1

    before_hash = file_hash(RAW_DOCUMENTS_PATH)
    run_b_pipeline()
    after_hash = file_hash(RAW_DOCUMENTS_PATH)
    errors: list[str] = []
    if before_hash != after_hash:
        errors.append("raw_documents.csv changed while running the B pipeline in safe mode")
    write_report(before_hash, after_hash, errors)
    print(f"Input preservation report written to {REPORT_PATH}")
    print(f"input_preservation_errors={len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
