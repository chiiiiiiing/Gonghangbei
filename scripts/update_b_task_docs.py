"""Maintain local B-role task progress and manual todo documents.

These files are intentionally gitignored because they represent local execution
state rather than project source.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
PROGRESS_PATH = VIEW_DIR / "任务进度.md"
MANUAL_TODO_PATH = VIEW_DIR / "人工待办.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
SOURCE_TYPES = ["policy", "announcement", "news", "ir_qa"]
FIRST_BATCH_TARGETS = {
    "policy": 20,
    "announcement": 20,
    "news": 20,
    "ir_qa": 10,
}
BASELINE_DEMO_DOC_COUNT = 20


def today() -> str:
    return date.today().isoformat()


def read_csv(filename: str) -> list[dict[str, str]]:
    path = SAMPLE_DIR / filename
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_len(filename: str) -> int:
    return len(read_csv(filename))


def doc_number(doc_id: str) -> int | None:
    match = re.fullmatch(r"S(\d{3})", doc_id)
    if not match:
        return None
    return int(match.group(1))


def is_candidate_summary(row: dict[str, str]) -> bool:
    return "待人工核验" in row.get("content", "")


def is_p0_verification_slot(row: dict[str, str]) -> bool:
    number = doc_number(row.get("doc_id", ""))
    return is_candidate_summary(row) or (
        number is not None and BASELINE_DEMO_DOC_COUNT < number <= 120
    )


def manual_verification_summary(docs: list[dict[str, str]]) -> tuple[int, int, int]:
    total_completed = 0
    total_pending = 0
    total_first_batch_remaining = 0
    for source_type in SOURCE_TYPES:
        p0_docs = [row for row in docs if row.get("source_type") == source_type and is_p0_verification_slot(row)]
        pending = sum(1 for row in p0_docs if is_candidate_summary(row))
        completed = len(p0_docs) - pending
        total_completed += completed
        total_pending += pending
        total_first_batch_remaining += max(FIRST_BATCH_TARGETS[source_type] - min(completed, FIRST_BATCH_TARGETS[source_type]), 0)
    return total_completed, total_pending, total_first_batch_remaining


def write_progress_doc() -> None:
    docs = read_csv("raw_documents.csv")
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    rules = read_csv("rules.csv")
    source_counts = Counter(row["source_type"] for row in docs)
    event_counts = Counter(row["event_type"] for row in events)
    candidate_count = sum(1 for row in docs if "待人工核验" in row["content"])
    p0_completed, p0_pending, first_batch_remaining = manual_verification_summary(docs)
    qualified_rules = sum(1 for row in rules if row.get("status") == "qualified")

    lines = [
        "# AlphaLens B 角色任务进度（本地自动维护）",
        "",
        f"更新时间：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 总览",
        "",
        f"- 股票池：{safe_len('stock_pool.csv')} 行",
        f"- 文本库：{len(docs)} 行，其中待人工核验候选摘要 {candidate_count} 行",
        f"- P0 真实文本核验进度：已替换 {p0_completed} 条，仍待替换 {p0_pending} 条；第一批目标剩余 {first_batch_remaining} 条",
        f"- 实体链接：{safe_len('entity_links.csv')} 行",
        f"- 事件：{len(events)} 行",
        f"- 谓词：{len(predicates)} 行",
        f"- 行情数据：{safe_len('market_data.csv')} 行（已联网获取东方财富前复权价格候选版，adj_factor 口径仍需人工复核）",
        f"- 合格规则：{qualified_rules} 条",
        f"- 解释案例：已生成 `查看材料/解释案例草稿.md`",
        f"- 源文本核验队列：已生成 `查看材料/源文本核验队列.csv`",
        f"- 用户参与工作推进手册：已生成 `查看材料/用户参与工作推进手册.md`",
        f"- 人工抽检样本：已生成 `查看材料/事件人工抽检样本.csv` 和 `查看材料/谓词人工抽检样本.csv`",
        f"- PPT 案例素材：已生成 `查看材料/PPT案例素材包.md`",
        f"- 全量案例索引：已生成 `查看材料/案例索引.csv`",
        f"- 查看材料索引：已生成 `查看材料/材料索引.md`",
        f"- C 联调契约清单：已生成 `查看材料/C联调数据契约清单.md`",
        f"- A 口径确认建议稿：已生成 `查看材料/A口径确认建议稿.md`",
        f"- C 联调运行手册：已生成 `查看材料/C联调运行手册.md`",
        f"- Demo 演示脚本：已生成 `查看材料/Demo演示脚本.md`",
        f"- 答辩问答素材：已生成 `查看材料/答辩问答素材.md`",
        f"- 真实数据替换验收清单：已生成 `查看材料/真实数据替换验收清单.md`",
        f"- 真实文本来源获取记录：已生成 `查看材料/真实文本来源获取记录.md`",
        f"- 真实文本核验进度：已生成 `查看材料/真实文本核验进度.md`",
        f"- 真实行情获取记录：已生成 `查看材料/真实行情获取记录.md`",
        f"- 真实行情导入模板：已生成 `查看材料/真实行情导入模板.csv`",
        f"- 流水线输入保护验证：已生成 `查看材料/流水线输入保护验证报告.md`",
        f"- 人工抽检结果校验报告：已生成 `查看材料/人工抽检结果校验报告.md`",
        f"- 交付包自检报告：已生成 `查看材料/交付包自检报告.md`",
        f"- Streamlit Demo：已具备 `app/main.py`，可用 `.venv/bin/streamlit run app/main.py --server.port 8501 --server.headless true` 启动",
        "",
        "## 数据来源分布",
        "",
    ]
    for source_type in ["policy", "announcement", "news", "ir_qa"]:
        lines.append(f"- {source_type}: {source_counts[source_type]}")

    lines.extend(["", "## 事件类型分布", ""])
    for event_type, count in sorted(event_counts.items()):
        lines.append(f"- {event_type}: {count}")

    lines.extend(
        [
            "",
            "## B 指南任务状态",
            "",
            "| 阶段 | 任务 | 状态 | 自动产物/说明 |",
            "|------|------|------|----------------|",
            "| 第一阶段 | 数据来源清单 | 已自动完成 | `参考文档/数据来源清单.md` |",
            "| 第一阶段 | 行业细分结构 | 已自动完成 | `data/sample/stock_pool.csv` 覆盖 5 个板块 |",
            "| 第一阶段 | 5 条高质量政策/公告 Demo 文本 | 已联网替换真实来源候选版 | S001-S010 已替换为可追溯详情页，仍需人工抽查口径 |",
            "| 第一阶段 | 事件类型列表 | 已自动完成 | `参考文档/事件与谓词Schema.md` |",
            "| 第一阶段 | 谓词 schema | 已自动完成 | `参考文档/事件与谓词Schema.md` |",
            "| 第二阶段 | 30 只新能源股票池 | 已自动完成 | `data/sample/stock_pool.csv` |",
            "| 第二阶段 | 100+ 文本样本 | 已联网替换真实来源候选版 | `data/sample/raw_documents.csv` 无待核验标记，URL 指向详情页，仍需人工抽查 |",
            "| 第二阶段 | 行情数据 | 已联网获取真实前复权候选版 | `data/sample/market_data.csv` 来自东方财富前复权 K 线，adj_factor 需人工复核 |",
            "| 第二阶段 | 实体链接脚本和结果 | 已自动完成 | `src/pipeline/link_entities.py` / `entity_links.csv` |",
            "| 第二阶段 | 事件抽取 prompt | 已自动完成 | `参考文档/事件抽取提示词.txt` |",
            "| 第二阶段 | 谓词判断 prompt | 已自动完成 | `参考文档/谓词判断提示词.txt` |",
            "| 第二阶段 | 事件抽取和谓词判断全量跑批 | 已自动完成 | `events.csv` / `predicates.csv` |",
            "| 第二阶段 | 数据质量检查 | 已自动完成 | `查看材料/数据质量报告.md` |",
            "| 第三阶段 | 输出最终版事件/谓词 | 自动候选版已完成 | 等人工核验后可作为最终版 |",
            "| 第三阶段 | 典型案例整理 | 已自动完成 | `查看材料/解释案例草稿.md` |",
            "| 第三阶段 | 人工抽检样本准备 | 已自动完成 | `查看材料/事件人工抽检样本.csv` / `查看材料/谓词人工抽检样本.csv` |",
            "| 第三阶段 | 全事件案例索引 | 已自动完成 | `查看材料/案例索引.csv` |",
            "| 第三阶段 | 查看材料索引 | 已自动完成 | `查看材料/材料索引.md` |",
            "| 第四阶段 | 时间戳与行情范围检查 | 已自动完成 | `validate_b_data.py` 和 `validate_research_outputs.py` |",
            "| 第四阶段 | 更多案例素材 | 已自动完成 | `查看材料/解释案例草稿.md` 和 `查看材料/PPT案例素材包.md` |",
            "| 第五阶段 | 最终数据完整性检查 | 自动候选版已完成 | 字段/引用/谓词/未来函数校验通过 |",
            "| 第五阶段 | 图文对应素材整理 | 已自动完成 | `查看材料/PPT案例素材包.md` |",
            "| 第五阶段 | Demo 离线运行协助 | 自动环境已准备 | `.venv` 可运行，真实离线机需重新安装依赖 |",
            "| 第六阶段 | 数据附件完整性检查 | 自动候选版已完成 | 见质量报告 |",
            "| 第六阶段 | C 联调清单 | 已自动完成 | `查看材料/C联调数据契约清单.md` |",
            "| 第六阶段 | A 口径确认建议稿 | 已自动完成 | `查看材料/A口径确认建议稿.md` |",
            "| 第六阶段 | C 联调运行手册 | 已自动完成 | `查看材料/C联调运行手册.md` |",
            "| 第六阶段 | Demo 演示脚本 | 已自动完成 | `查看材料/Demo演示脚本.md` |",
            "| 第六阶段 | 答辩问答素材 | 已自动完成 | `查看材料/答辩问答素材.md` |",
            "| 第六阶段 | 真实数据替换验收清单 | 已自动完成 | `查看材料/真实数据替换验收清单.md` |",
            "| 第六阶段 | 用户参与推进步骤 | 已自动完成 | `查看材料/用户参与工作推进手册.md` |",
            "| 第六阶段 | 真实文本来源联网替换 | 已自动完成候选版 | `scripts/fetch_verified_text_sources.py` / `查看材料/真实文本来源获取记录.md` |",
            "| 第六阶段 | 真实前复权行情联网获取 | 已自动完成候选版 | `scripts/fetch_eastmoney_market_data.py` / `查看材料/真实行情获取记录.md` |",
            "| 第六阶段 | 流水线保护人工数据安全模式 | 已自动完成 | `run_pipeline.py --preserve-inputs` 默认保留已有输入，`--force-sample-generation` 才覆盖 Demo 输入 |",
            "| 第六阶段 | 流水线输入保护验证 | 已自动完成 | `scripts/validate_input_preservation.py` / `查看材料/流水线输入保护验证报告.md` |",
            "| 第六阶段 | 真实文本核验进度统计 | 已自动完成 | `scripts/report_manual_verification_progress.py` / `查看材料/真实文本核验进度.md` |",
            "| 第六阶段 | 真实行情导入模板和校验 | 已自动完成 | `查看材料/真实行情导入模板.csv` / `scripts/validate_real_market_data.py` |",
            "| 第六阶段 | 人工抽检结果合法值校验 | 已自动完成 | `scripts/validate_manual_review_results.py` / `查看材料/人工抽检结果校验报告.md` |",
            "| 第六阶段 | 交付包自检 | 已自动完成 | `查看材料/交付包自检报告.md` |",
            "| 第六阶段 | 1 分钟电梯演讲 | 已自动起草 | 见本文档下方 |",
            "",
            "## 1 分钟电梯演讲草稿",
            "",
            "AlphaLens 是一个面向金融机构投研场景的 AI 量化研究助手。它不让大模型直接预测股价，而是把政策、公告、财经新闻和互动问答转化为结构化事件，再把事件落到可计算谓词，进一步归纳可解释规则并生成另类因子。评委可以沿着 Demo 看到从文本输入、实体链接、事件抽取、谓词矩阵、规则触发、因子排名到回测审计和研究报告的完整链路。项目的价值在于让非结构化舆情数据变得可解释、可回测、可复用，帮助研究员更快形成和验证因子假设。",
            "",
            "## 运行命令",
            "",
            "```bash",
            ".venv/bin/python run_pipeline.py --preserve-inputs",
            ".venv/bin/streamlit run app/main.py --server.port 8501 --server.headless true",
            "```",
            "",
            "说明：真实文本或真实行情开始写入后，不要使用 `--force-sample-generation`。",
            "",
        ]
    )
    PROGRESS_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_manual_todo_doc() -> None:
    lines = [
        "# AlphaLens B 角色人工待办（本地自动维护）",
        "",
        f"更新时间：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 必须人工完成或确认的任务",
        "",
        "建议先按 `查看材料/用户参与工作推进手册.md` 的顺序推进，并在每完成一批后让 Codex 安全复跑验证。",
        "",
        "| 优先级 | 任务 | 原因 | 建议产物 |",
        "|--------|------|------|----------|",
        "| P0 | 人工抽查联网替换后的真实文本来源 | 自动联网已替换为详情页 URL，但正式交付仍需人工确认事实、日期和公司关联 | 抽查记录、必要修正后的 `raw_documents.csv` |",
        "| P0 | 人工复核真实前复权行情口径 | 已联网获取东方财富前复权 K 线，但 `adj_factor` 未由接口直接返回 | 数据源口径确认记录或外部数据供应商说明 |",
        "| P0 | 随机 10 条检查事件类型准确率 | 事件类型质量需要金融语义判断 | 抽检记录和修正后的 `events.csv` |",
        "| P0 | 随机 10 条检查谓词赋值合理性 | 谓词值影响规则和因子，需人工审阅 | 抽检记录和修正后的 `predicates.csv` |",
        "| P0 | 与 A 确认事件类型列表和谓词 schema | schema 是金融逻辑口径，需项目负责人确认 | A 确认记录 |",
        "| P0 | 与 C 联调真实行情和 CSV 读取 | C 端回测依赖真实数据读取和未来函数检查 | 联调记录、修复项 |",
        "| P1 | 从财政部、工信部、发改委、国家能源局抽查 20-30 条政策文本 | 自动检索不能替代人工事实核验 | 政策文本抽查清单 |",
        "| P1 | 从巨潮资讯网抽查 20-30 条公告 | 公告事实需来源精确 | 公告文本抽查清单 |",
        "| P1 | 从权威财经媒体抽查 20-30 条新闻摘要 | 避免版权和事实风险 | 新闻摘要抽查清单 |",
        "| P1 | 从互动易/上证 e 互动抽查 10-20 条问答 | 问答需与公司和日期一一对应 | 互动问答抽查清单 |",
        "| P1 | 检查所有事件是否存在未来信息 | 正式回测必须严格 `event_time < trade_date` | 未来函数审计记录 |",
        "| P1 | 为 A 挑选 PPT 用 3-5 个案例 | 案例叙事需要金融逻辑筛选 | PPT 案例素材 |",
        "| P2 | 检查 Demo 在另一台电脑离线运行 | 比赛现场环境不可控 | 离线运行记录 |",
        "| P2 | 协助 A 检查 PPT 中数据和图表 | 防止展示数字与 CSV 不一致 | PPT 检查记录 |",
        "",
        "## 人工替换数据时的硬约束",
        "",
        "- 先按 `查看材料/用户参与工作推进手册.md` 逐项核验。",
        "- 替换真实数据后按 `查看材料/真实数据替换验收清单.md` 验收。",
        "- 与 C 联调时使用 `查看材料/C联调数据契约清单.md`。",
        "- 不改 `参考文档/数据格式规范.md` 中锁定字段名。",
        "- 股票代码必须是 6 位字符串，不带交易所后缀。",
        "- 日期必须是 `YYYY-MM-DD`。",
        "- boolean 必须是字符串 `true` / `false`。",
        "- 不提交 `data/raw/`、`data/processed/`、`data/external/`。",
        "- 不删除任何报告中的免责声明。",
        "",
        "## 完成人工任务后要运行",
        "",
        "```bash",
        ".venv/bin/python run_pipeline.py --preserve-inputs",
        ".venv/bin/python scripts/validate_input_preservation.py",
        ".venv/bin/python scripts/report_manual_verification_progress.py",
        ".venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv",
        ".venv/bin/python scripts/validate_manual_review_results.py",
        "```",
        "",
        "真实文本或真实行情开始写入后，不要使用 `--force-sample-generation`；如只重算 B 线中间表，用 `.venv/bin/python run_b_pipeline.py --skip-sample-generation`。",
        "",
    ]
    MANUAL_TODO_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    write_progress_doc()
    write_manual_todo_doc()
    print(f"Updated {PROGRESS_PATH}")
    print(f"Updated {MANUAL_TODO_PATH}")


if __name__ == "__main__":
    main()
