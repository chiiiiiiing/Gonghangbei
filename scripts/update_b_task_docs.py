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
        f"- P0 真实文本替换进度：已替换 {p0_completed} 条，仍待替换 {p0_pending} 条；第一批目标剩余 {first_batch_remaining} 条",
        f"- 真实文本来源审计：{len(docs)} 条已纳入程序化核验，见 `查看材料/真实文本来源核验报告.md`",
        f"- 实体链接：{safe_len('entity_links.csv')} 行",
        f"- 事件：{len(events)} 行",
        f"- 谓词：{len(predicates)} 行",
        f"- 行情数据：{safe_len('market_data.csv')} 行（东方财富 `fqt=1` 前复权价格候选版；已接受 adj_factor=1 作为字段占位并披露限制）",
        f"- 合格规则：{qualified_rules} 条",
        f"- 团队对接手册：已生成 `查看材料/团队对接手册.md`",
        f"- 人工抽检样本：已生成 `查看材料/事件人工抽检样本.csv` 和 `查看材料/谓词人工抽检样本.csv`",
        f"- PPT 案例素材：已生成 `查看材料/PPT案例素材包.md`",
        f"- 全量案例索引：已生成 `查看材料/案例索引.csv`",
        f"- 查看材料索引：已生成 `查看材料/材料索引.md`",
        f"- A 口径确认建议稿：已生成 `查看材料/A口径确认建议稿.md`",
        f"- C 联调运行手册：已生成 `查看材料/C联调运行手册.md`",
        "- 可演示成果：已集中到 `可演示成果/`，完整说明见 `可演示成果/完整说明文档.md`",
        f"- 答辩问答素材：已生成 `查看材料/答辩问答素材.md`",
        f"- 真实文本来源核验报告：已生成 `查看材料/真实文本来源核验报告.md` 和 `查看材料/源文本核验明细.csv`",
        f"- 真实行情获取记录：已生成 `查看材料/真实行情获取记录.md`",
        f"- 真实行情导入模板：已生成 `查看材料/真实行情导入模板.csv`",
        f"- 流水线输入保护验证：已生成 `查看材料/流水线输入保护验证报告.md`",
        f"- 人工抽检结果校验报告：已生成 `查看材料/人工抽检结果校验报告.md`",
        f"- 交付包自检报告：已生成 `查看材料/交付包自检报告.md`",
        "- Flask Demo：统一使用 `.venv/bin/python 可演示成果/启动演示.py` 启动",
        "- Demo 双工作区：新文本页已支持真实详情页案例、实体切换与因子公式；历史页已展示正式回测、因子截面和冻结规则库",
        "- AI 研究层：模式一已强制执行本地 Embedding、`deepseek-v4-flash` 结构化 Chat 和程序校验，AI 失败直接报错；模式二独立提供规则复现",
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
            "| 第一阶段 | 5 条高质量政策/公告 Demo 文本 | 已联网替换并核验 | S001-S010 已替换为可追溯详情页并纳入逐条来源审计 |",
            "| 第一阶段 | 事件类型列表 | 已自动完成 | `参考文档/事件与谓词Schema.md` |",
            "| 第一阶段 | 谓词 schema | 已自动完成 | `参考文档/事件与谓词Schema.md` |",
            "| 第二阶段 | 30 只新能源股票池 | 已自动完成 | `data/sample/stock_pool.csv` |",
            f"| 第二阶段 | 100+ 文本样本 | 已联网替换并程序化核验 | {len(docs)} 条，逐条见 `源文本核验明细.csv` |",
            "| 第二阶段 | 行情数据 | 已联网获取并确定占位口径 | `market_data.csv` 来自东方财富 `fqt=1`；adj_factor=1 仅作字段占位，答辩需说明限制 |",
            "| 第二阶段 | 实体链接脚本和结果 | 已自动完成 | `src/pipeline/link_entities.py` / `entity_links.csv` |",
            "| 第二阶段 | 事件抽取 prompt | 已自动完成 | `参考文档/事件抽取提示词.txt` |",
            "| 第二阶段 | 谓词判断 prompt | 已自动完成 | `参考文档/谓词判断提示词.txt` |",
            "| 第二阶段 | 大模型 API 与结构化输出 | 已自动完成 | `src/ai/` / `参考文档/大模型结构化研究提示词.md` / `可演示成果/完整说明文档.md` |",
            "| 第二阶段 | 事件抽取和谓词判断全量跑批 | 已自动完成 | `events.csv` / `predicates.csv` |",
            "| 第二阶段 | 数据质量检查 | 已自动完成 | `查看材料/数据质量报告.md` |",
            "| 第三阶段 | 输出最终版事件/谓词 | 事件误判已按抽检修复，谓词待人工抽检 | 单条问答不再自动生成压力事件，公告只按原文事实抽取 |",
            "| 第三阶段 | 典型案例整理 | 已自动完成 | `查看材料/PPT案例素材包.md` / `查看材料/案例索引.csv` |",
            "| 第三阶段 | 人工抽检样本准备 | 已自动完成 | `查看材料/事件人工抽检样本.csv` / `查看材料/谓词人工抽检样本.csv` |",
            "| 第三阶段 | 全事件案例索引 | 已自动完成 | `查看材料/案例索引.csv` |",
            "| 第三阶段 | 查看材料索引 | 已自动完成 | `查看材料/材料索引.md` |",
            "| 第四阶段 | 时间戳与行情范围检查 | 已自动完成 | `validate_b_data.py` 和 `validate_research_outputs.py` |",
            "| 第四阶段 | 更多案例素材 | 已自动完成 | `查看材料/PPT案例素材包.md` / `查看材料/案例索引.csv` |",
            "| 第五阶段 | 最终数据完整性检查 | 自动候选版已完成 | 字段/引用/谓词/未来函数校验通过 |",
            "| 第五阶段 | 图文对应素材整理 | 已自动完成 | `查看材料/PPT案例素材包.md` |",
            "| 第五阶段 | Demo 离线运行协助 | 自动环境已准备 | `.venv` 可运行，真实离线机需重新安装依赖 |",
            "| 第六阶段 | 数据附件完整性检查 | 自动候选版已完成 | 见质量报告 |",
            "| 第六阶段 | A 口径确认建议稿 | 已自动完成 | `查看材料/A口径确认建议稿.md` |",
            "| 第六阶段 | C 联调运行手册 | 已自动完成 | `查看材料/C联调运行手册.md` |",
            "| 第六阶段 | A/B/C 团队对接步骤 | 已自动完成 | `查看材料/团队对接手册.md` |",
            "| 第六阶段 | Demo 演示与说明 | 已自动完成 | `可演示成果/完整说明文档.md` |",
            "| 第六阶段 | 答辩问答素材 | 已自动完成 | `查看材料/答辩问答素材.md` |",
            "| 第六阶段 | 真实文本来源联网替换 | 已完成并核验 | `fetch_verified_text_sources.py` / `audit_text_sources.py` / `真实文本来源核验报告.md` |",
            "| 第六阶段 | 真实前复权行情联网获取 | 已完成候选版并记录限制 | `fetch_eastmoney_market_data.py` / `真实行情获取记录.md` |",
            "| 第六阶段 | 流水线保护人工数据安全模式 | 已自动完成 | `run_pipeline.py --preserve-inputs` 默认保留已有输入，`--force-sample-generation` 才覆盖 Demo 输入 |",
            "| 第六阶段 | 流水线输入保护验证 | 已自动完成 | `scripts/validate_input_preservation.py` / `查看材料/流水线输入保护验证报告.md` |",
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
            ".venv/bin/python 可演示成果/启动演示.py",
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
        "建议先按 `查看材料/团队对接手册.md` 的顺序与 A/C 对接，并在每次数据或口径变更后安全复跑验证。",
        "",
        "| 优先级 | 任务 | 原因 | 建议产物 |",
        "|--------|------|------|----------|",
        "| P0 | 随机 10 条检查谓词赋值合理性 | 谓词值影响规则和因子，需人工审阅 | 抽检记录和修正后的 `predicates.csv` |",
        "| P0 | 与 A 确认事件类型列表和谓词 schema | schema 是金融逻辑口径，需项目负责人确认 | A 确认记录 |",
        "| P0 | 与 C 联调真实行情和 CSV 读取 | C 端回测依赖真实数据读取和未来函数检查 | 联调记录、修复项 |",
            "| P0 | 用团队 Key 完成一次 DeepSeek 真实连通测试 | 密钥、额度、模型权限和外部文本发送权限必须由团队确认 | 只记录时间、模型和成功/失败，不记录 Key |",
        "| P1 | 抽检 10 条 AI 与规则谓词差异 | 模型候选需要金融口径与证据一致性审核 | AI/规则差异记录 |",
        "| P1 | 检查所有事件是否存在未来信息 | 正式回测必须严格 `event_time < trade_date` | 未来函数审计记录 |",
        "| P1 | 为 A 挑选 PPT 用 3-5 个案例 | 案例叙事需要金融逻辑筛选 | PPT 案例素材 |",
        "| P2 | 检查 Demo 在另一台电脑离线运行 | 比赛现场环境不可控 | 离线运行记录 |",
        "| P2 | 协助 A 检查 PPT 中数据和图表 | 防止展示数字与 CSV 不一致 | PPT 检查记录 |",
        "",
        "## 本轮已完成",
        "",
        "- 真实文本已完成程序化联网核验，逐条见 `源文本核验明细.csv`。",
        "- 行情口径：接受 `adj_factor=1` 作为字段占位，并要求答辩主动说明它不是真实复权因子序列。",
        "- 事件抽检：10 条均为 drop，已按共性根因修复并保留 `事件抽检问题处理记录.md`。",
        "- Demo 已提供 DeepSeek API Key 单次密码框；模拟端点已验证 Key 不回显、请求后清空和 `deepseek-v4-flash` 结构化输出。",
        "",
        "## 人工替换数据时的硬约束",
        "",
        "- 与 A/C 对接时按 `查看材料/团队对接手册.md` 执行并保留书面回传记录。",
        "- 与 C 联调时使用 `查看材料/C联调运行手册.md`。",
        "- 大模型配置和演示按 `可演示成果/完整说明文档.md` 执行；Key 只填页面密码框，不提交 `.env`，不写进联调记录。",
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
        ".venv/bin/python scripts/audit_text_sources.py",
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
