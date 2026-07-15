"""Validate AlphaLens local delivery materials and directory boundaries."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
REFERENCE_DIR = ROOT / "参考文档"
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "交付包自检报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

REFERENCE_FILES = [
    "赛题与工作说明.md",
    "工作推进与分工文档.md",
    "数据格式规范.md",
    "数据负责人工作指南.md",
    "数据来源清单.md",
    "事件与谓词Schema.md",
    "事件抽取提示词.txt",
    "谓词判断提示词.txt",
    "RIFT规则归纳论文.pdf",
]

VIEW_FILES = [
    "材料索引.md",
    "任务进度.md",
    "人工待办.md",
    "用户参与工作推进手册.md",
    "A口径确认建议稿.md",
    "C联调运行手册.md",
    "Demo演示脚本.md",
    "答辩问答素材.md",
    "真实数据替换验收清单.md",
    "真实文本来源获取记录.md",
    "真实文本核验进度.md",
    "真实行情获取记录.md",
    "真实行情导入模板.csv",
    "真实行情校验报告.md",
    "流水线输入保护验证报告.md",
    "人工抽检结果校验报告.md",
    "源文本核验队列.csv",
    "事件人工抽检样本.csv",
    "谓词人工抽检样本.csv",
    "案例索引.csv",
    "解释案例草稿.md",
    "PPT案例素材包.md",
    "C联调数据契约清单.md",
    "数据质量报告.md",
    "未来函数审计明细.md",
    "因子研究报告.md",
]

SAMPLE_FILES = [
    "stock_pool.csv",
    "raw_documents.csv",
    "entity_links.csv",
    "events.csv",
    "predicates.csv",
    "market_data.csv",
    "predicate_matrix.csv",
    "event_forward_returns.csv",
    "rules.csv",
    "factors.csv",
    "factor_snapshot.csv",
    "group_returns.csv",
    "rank_ic_timeseries.csv",
    "backtest_metrics.csv",
]

DISCLAIMER_FILES = [
    VIEW_DIR / "材料索引.md",
    VIEW_DIR / "任务进度.md",
    VIEW_DIR / "人工待办.md",
    VIEW_DIR / "用户参与工作推进手册.md",
    VIEW_DIR / "A口径确认建议稿.md",
    VIEW_DIR / "C联调运行手册.md",
    VIEW_DIR / "Demo演示脚本.md",
    VIEW_DIR / "答辩问答素材.md",
    VIEW_DIR / "真实数据替换验收清单.md",
    VIEW_DIR / "真实文本来源获取记录.md",
    VIEW_DIR / "真实文本核验进度.md",
    VIEW_DIR / "真实行情获取记录.md",
    VIEW_DIR / "真实行情校验报告.md",
    VIEW_DIR / "流水线输入保护验证报告.md",
    VIEW_DIR / "人工抽检结果校验报告.md",
    VIEW_DIR / "解释案例草稿.md",
    VIEW_DIR / "PPT案例素材包.md",
    VIEW_DIR / "C联调数据契约清单.md",
    VIEW_DIR / "数据质量报告.md",
    VIEW_DIR / "未来函数审计明细.md",
    VIEW_DIR / "因子研究报告.md",
    ROOT / "README.md",
]

DEPRECATED_VIEW_FILES = {
    "B线阶段0进展.md",
    "自动推进进展记录.md",
    "数据完整性检查报告.md",
    "B线交付检查清单.md",
    "人工核验操作手册.md",
    "真实文本核验进度.csv",
}


def today() -> str:
    return date.today().isoformat()


def is_git_ignored(path: Path, *, directory: bool = False) -> bool:
    relative_path = str(path.relative_to(ROOT))
    if directory and not relative_path.endswith("/"):
        relative_path = f"{relative_path}/"
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative_path],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def check_required_files(errors: list[str]) -> None:
    for filename in REFERENCE_FILES:
        if not (REFERENCE_DIR / filename).exists():
            errors.append(f"参考文档缺少 `{filename}`")
    for filename in VIEW_FILES:
        if not (VIEW_DIR / filename).exists():
            errors.append(f"查看材料缺少 `{filename}`")
    for filename in SAMPLE_FILES:
        if not (SAMPLE_DIR / filename).exists():
            errors.append(f"data/sample 缺少 `{filename}`")


def check_disclaimers(errors: list[str]) -> None:
    for path in DISCLAIMER_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if DISCLAIMER not in text:
            errors.append(f"{path.relative_to(ROOT)} 缺少免责声明")


def check_directory_boundaries(errors: list[str], warnings: list[str]) -> None:
    view_like_names = {
        "任务进度.md",
        "人工待办.md",
        "用户参与工作推进手册.md",
        "数据质量报告.md",
        "因子研究报告.md",
        "解释案例草稿.md",
        "PPT案例素材包.md",
        "未来函数审计明细.md",
        "事件人工抽检样本.csv",
        "谓词人工抽检样本.csv",
        "源文本核验队列.csv",
        "C联调数据契约清单.md",
        "C联调运行手册.md",
        "A口径确认建议稿.md",
        "Demo演示脚本.md",
        "答辩问答素材.md",
        "真实文本来源获取记录.md",
        "真实文本核验进度.md",
        "真实行情获取记录.md",
        "真实行情导入模板.csv",
        "真实行情校验报告.md",
        "流水线输入保护验证报告.md",
        "人工抽检结果校验报告.md",
        "案例索引.csv",
        "材料索引.md",
    }
    for path in REFERENCE_DIR.glob("*"):
        if path.name in view_like_names:
            errors.append(f"查看材料 `{path.name}` 不应留在参考文档目录")

    for path in VIEW_DIR.glob("*"):
        if path.name in DEPRECATED_VIEW_FILES:
            errors.append(f"查看材料存在已废弃的重复文档 `{path.name}`")
        elif path.suffix.lower() in {".md", ".csv"} and path.name not in set(VIEW_FILES) | {
            "交付包自检报告.md",
        }:
            warnings.append(f"查看材料存在未登记文件 `{path.name}`，如需长期保留请加入材料索引")


def check_gitignore(errors: list[str]) -> None:
    for path in [VIEW_DIR / "任务进度.md", VIEW_DIR / "人工待办.md"]:
        if path.exists() and not is_git_ignored(path):
            errors.append(f"{path.relative_to(ROOT)} 未被 gitignore")
    for path in [ROOT / "data" / "raw", ROOT / "data" / "processed", ROOT / "data" / "external"]:
        if not is_git_ignored(path, directory=True):
            errors.append(f"{path.relative_to(ROOT)} 未被 gitignore")


def write_report(errors: list[str], warnings: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AlphaLens 交付包自检报告",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 结论",
        "",
        f"- Fatal errors: {len(errors)}",
        f"- Warnings: {len(warnings)}",
        f"- 自检结论：{'通过' if not errors else '不通过'}",
        "",
        "## 检查范围",
        "",
        "- 必要参考文档是否存在。",
        "- 查看材料是否完整。",
        "- data/sample 数据文件是否齐备。",
        "- 报告类文件是否保留免责声明。",
        "- 本地维护文档和 raw/processed/external 数据目录是否被 gitignore。",
        "- `参考文档/` 与 `查看材料/` 的目录边界是否清晰。",
        "",
        "## Warnings",
        "",
    ]
    lines.extend([f"- {item}" for item in warnings] or ["- 无"])
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {item}" for item in errors] or ["- 无"])
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    check_required_files(errors)
    check_disclaimers(errors)
    check_directory_boundaries(errors, warnings)
    check_gitignore(errors)
    write_report(errors, warnings)
    print(f"Delivery package report written to {REPORT_PATH}")
    print(f"delivery_errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
