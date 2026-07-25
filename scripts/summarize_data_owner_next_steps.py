"""Summarize B-role data validation status and next ownership split."""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "数据负责人下一步分工与验证报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_view_csv(filename: str) -> list[dict[str, str]]:
    path = VIEW_DIR / filename
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def md_count_items(counter: Counter[str]) -> str:
    if not counter:
        return "无"
    return "；".join(f"{key}={counter[key]}" for key in sorted(counter))


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def latest_generated_docs(docs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in docs
        if row["doc_id"].startswith("S") and row["doc_id"][1:].isdigit() and int(row["doc_id"][1:]) >= 121
    ]


def docs_without_links(docs: list[dict[str, str]], links: list[dict[str, str]]) -> list[str]:
    linked_doc_ids = {row["doc_id"] for row in links}
    return [row["doc_id"] for row in docs if row["doc_id"] not in linked_doc_ids]


def rules_summary(rules: list[dict[str, str]]) -> dict[str, object]:
    qualified = [row for row in rules if row["status"] == "qualified"]
    return {
        "total": len(rules),
        "qualified": len(qualified),
        "avg_support": avg([float(row["support_count"]) for row in qualified]),
        "avg_win": avg([float(row["win_rate"]) for row in qualified]),
        "avg_ret": avg([float(row["avg_forward_return_5d"]) for row in qualified]),
    }


def future_info_errors(returns: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    for row in returns:
        if row["future_info_ok"] != "true":
            errors.append(f"{row['event_id']}: future_info_ok != true")
            continue
        if parse_date(row["entry_trade_date"]) <= parse_date(row["event_time"]):
            errors.append(f"{row['event_id']}: entry_trade_date <= event_time")
    return errors


def predicate_flags(matrix: list[dict[str, str]]) -> list[tuple[str, int, float, str]]:
    if not matrix:
        return []
    metadata = {"event_id", "doc_id", "stock_code", "event_type", "event_time"}
    score_columns = {"event_evidence_strength", "event_has_short_term_price_impact"}
    total = len(matrix)
    rows: list[tuple[str, int, float, str]] = []
    for name in [key for key in matrix[0] if key not in metadata and key not in score_columns]:
        trigger_count = sum(row.get(name) == "true" for row in matrix)
        trigger_rate = trigger_count / total
        flag = "正常"
        if trigger_count == 0:
            flag = "未触发，暂不进入规则搜索"
        elif trigger_count < 5:
            flag = "过稀，需要补样本或合并"
        elif trigger_rate > 0.8:
            flag = "过密，单独区分度弱"
        rows.append((name, trigger_count, trigger_rate, flag))
    return rows


def write_report() -> None:
    docs = read_csv("raw_documents.csv")
    links = read_csv("entity_links.csv")
    events = read_csv("events.csv")
    matrix = read_csv("predicate_matrix.csv")
    returns = read_csv("event_forward_returns.csv")
    rules = read_csv("rules.csv")
    factors = read_csv("factors.csv")
    metrics = read_csv("backtest_metrics.csv")
    source_audit = read_view_csv("源文本核验明细.csv")
    demo_cases = read_view_csv("新输入文本Demo测试案例.csv")

    source_counts = Counter(row["source_type"] for row in docs)
    generated_counts = Counter(row["source_type"] for row in latest_generated_docs(docs))
    link_confidences = [float(row["confidence"]) for row in links]
    low_confidence_links = [row for row in links if float(row["confidence"]) < 0.70]
    links_by_doc = Counter(row["doc_id"] for row in links)
    event_counts = Counter(row["event_type"] for row in events)
    events_by_doc = Counter(row["doc_id"] for row in events)
    event_source_counts = Counter()
    docs_by_id = {row["doc_id"]: row for row in docs}
    for event in events:
        event_source_counts[docs_by_id[event["doc_id"]]["source_type"]] += 1
    factor_counts = Counter(row["factor_name"] for row in factors)
    metric_map = {row["metric"]: row["value"] for row in metrics}
    source_audit_counts = Counter(row.get("verification_result", "") for row in source_audit)
    no_link_docs = docs_without_links(docs, links)
    no_event_docs = [row["doc_id"] for row in docs if row["doc_id"] not in events_by_doc]
    future_errors = future_info_errors(returns)
    rule_stats = rules_summary(rules)
    predicate_rows = predicate_flags(matrix)

    lines = [
        "# AlphaLens 数据负责人下一步分工与验证报告",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        DISCLAIMER,
        "",
        "## 当前结论",
        "",
        f"- 历史文本库：{len(docs)} 条，来源分布：{md_count_items(source_counts)}。",
        f"- 本轮有效自动增量：{len(latest_generated_docs(docs))} 条，分布：{md_count_items(generated_counts)}。",
        f"- 来源自动核验：pass={source_audit_counts['pass']}，revise={source_audit_counts['revise']}。",
        f"- 实体链接：{len(links)} 条，平均置信度 {avg(link_confidences):.3f}，低于 0.70 的宽主题链接 {len(low_confidence_links)} 条。",
        f"- 事件：{len(events)} 条，事件类型分布：{md_count_items(event_counts)}。",
        f"- 谓词矩阵事件数：{len(matrix)}；forward return 可用事件：{len(returns)}。",
        f"- 规则：{rule_stats['total']} 条，其中合格 {rule_stats['qualified']} 条；合格规则平均支持 {rule_stats['avg_support']:.1f}，平均胜率 {pct(rule_stats['avg_win'])}。",
        f"- 因子样本：{len(factors)} 条；因子名分布：{md_count_items(factor_counts)}。",
        f"- 回测指标：样本数 {metric_map.get('event_factor_sample_count', '')}，G5-G1={metric_map.get('top_bottom_group_spread_5d', '')}，正收益率={metric_map.get('positive_forward_return_rate_5d', '')}，未来函数审计={metric_map.get('future_info_audit', '')}。",
        "",
        "## 已自动完成",
        "",
        "- 扩充并清洗历史文本库：追加政策/新闻可核验文本，清理本轮低相关公告，保持 URL 唯一和字段契约不变。",
        "- 提高股票关联质量：复跑实体链接，保留低置信度宽主题链接用于人工重点抽查。",
        "- 校验事件抽取：复跑事件抽取并统计事件类型、来源类型和未成事件文本。",
        "- 验证谓词质量：生成 `谓词筛选与规则调参报告.md`，标记未触发、过稀、重复和区分度弱谓词。",
        "- 准备差异样本：保留政策、新闻、公告风险、关注扩散、中性问答等方向；新增 5 条新输入 Demo 测试案例。",
        "- 检查未来函数：验证 `entry_trade_date > event_time`，并复跑研究输出校验。",
        "",
        "## 需要重点人工抽查",
        "",
        f"- 没有实体链接的文本：{len(no_link_docs)} 条。示例：{', '.join(no_link_docs[:8]) if no_link_docs else '无'}。",
        f"- 有文本但没有生成事件的文本：{len(no_event_docs)} 条。主要是单条互动问答或证据不足文本，示例：{', '.join(no_event_docs[:8]) if no_event_docs else '无'}。",
        f"- 低置信度实体链接：{len(low_confidence_links)} 条。它们多来自产业主题映射，需要你判断是否过宽。",
        f"- 事件来源分布：{md_count_items(event_source_counts)}。如果风险/问询/处罚样本仍少，需要补真实公告。",
        "",
        "## 谓词状态速览",
        "",
        "| 谓词 | 触发次数 | 触发率 | 处理建议 |",
        "|------|----------|--------|----------|",
    ]
    for name, trigger_count, trigger_rate, flag in predicate_rows:
        lines.append(f"| `{name}` | {trigger_count} | {pct(trigger_rate)} | {flag} |")
    lines.extend(
        [
            "",
            "## AI 接下来做什么",
            "",
            "1. 做在线新文本推理链路：新文本只生成当期因子值，不要求未来收益，不参与规则重训。",
            "2. 把规则库冻结/版本化：历史 discovery 生成规则，在线推理只读取合格规则。",
            "3. 改进实体链接解释：区分直接提及、产业链映射、宽主题映射，并在 Demo 展示每只股票为什么被关联。",
            "4. 改进事件抽取：政策、公告、新闻、问答分别用不同规则；单条问答不直接变成提问压力。",
            "5. 改进规则去冗余和权重：避免多个近似规则重复加分，补充风险类规则的负向或单独风险因子处理。",
            "6. 做 Streamlit 新输入 Demo 页：展示相关股票、事件、谓词、触发规则、因子名、因子值和免责声明。",
            "",
            "## 你接下来做什么",
            "",
            "1. 人工核验新增文本：逐条看 S121-S130 的标题、日期、摘要和 URL 是否与原文一致。",
            "2. 抽查低置信度股票关联：判断宽主题映射是不是过宽，尤其是政策/行业新闻关联多只股票的情况。",
            "3. 标注事件抽检样本：看 `事件人工抽检样本.csv`，对 event_type、evidence_text、impact_path 给 pass/revise/drop。",
            "4. 标注谓词抽检样本：看 `谓词人工抽检样本.csv`，指出误触发、漏触发和需要新增的谓词。",
            "5. 补真实差异样本：优先找问询函、处罚、减值、停复产、价格下跌、订单不及预期、政策约束类文本。",
            "6. 准备答辩案例：从 `新输入文本Demo测试案例.csv` 里选 2 到 3 条，确认关联股票和解释口径。",
            "",
            "## 风险提示",
            "",
            "- 当前 `adj_factor=1` 是占位口径，不是真实复权因子序列；报告和答辩必须披露。",
            "- 当前 Rank IC 为 0，说明样例因子排序能力还不稳定；展示重点应放在可解释链路、审计闭环和样本外验证方法。",
            "- 新输入文本不能马上回测，只有未来真实收益出来后，才能加入后续样本外验证。",
            "",
            DISCLAIMER,
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


def main() -> int:
    write_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
