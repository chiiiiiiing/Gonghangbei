"""Generate a Markdown research report from AlphaLens outputs."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
REPORT_PATH = ROOT / "查看材料" / "因子研究报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"


def today() -> str:
    return date.today().isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pct(value: str) -> str:
    return f"{float(value) * 100:.2f}%"


def main() -> None:
    rules = read_csv(SAMPLE_DIR / "rules.csv")
    metrics = {row["metric"]: row for row in read_csv(SAMPLE_DIR / "backtest_metrics.csv")}
    groups = read_csv(SAMPLE_DIR / "group_returns.csv")
    snapshot = read_csv(SAMPLE_DIR / "factor_snapshot.csv")
    documents = read_csv(SAMPLE_DIR / "raw_documents.csv")
    top_snapshot = snapshot[:5]
    candidate_doc_count = sum(1 for row in documents if "待人工核验" in row["content"])

    qualified_rules = [rule for rule in rules if rule["status"] == "qualified"]
    factor_names = sorted({row["factor_name"] for row in snapshot if row["factor_name"]})
    lines = [
        "# AlphaLens 可解释事件因子研究报告",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 因子假设",
        "",
        "当政策、权威来源、核心产品、关注扩散、产能建设或风险披露等文本线索形成稳定谓词组合时，相关事件可被转化为可解释的另类因子研究信号。",
        "",
        "本报告展示的是 AlphaLens 从文本到事件、谓词、自动规则、规则族因子和回测审计的自动闭环，不用于预测股票价格。",
        "",
        "## 数据范围",
        "",
        "- 行业：新能源",
        "- 股票池：30 只样例股票",
        f"- 文本样例：政策、公告、新闻、互动问答共 {len(documents)} 条摘要化样本，其中 {candidate_doc_count} 条仍带待人工核验标记",
        "- 行情：当前使用 `data/sample/market_data.csv`，已联网获取前复权价格候选版，复权因子口径仍需人工复核",
        "",
        "## 规则摘要",
        "",
        f"当前因子族：{', '.join(factor_names) if factor_names else '暂无触发'}",
        "",
    ]

    for rule in qualified_rules:
        lines.extend(
            [
                f"### {rule['rule_id']} {rule['rule_name']}",
                "",
                f"- 条件：`{rule['condition']}`",
                f"- 标签：`{rule['target_label']}`",
                f"- 支持样本：{rule['support_count']}",
                f"- 5 日胜率：{pct(rule['win_rate'])}",
                f"- 5 日平均收益：{pct(rule['avg_forward_return_5d'])}",
                f"- 规则评分：{rule['score']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 因子快照",
            "",
            "| 排名 | 代码 | 名称 | 细分 | 因子值 | 触发规则 |",
            "|------|------|------|------|--------|----------|",
        ]
    )
    for rank, row in enumerate(top_snapshot, start=1):
        lines.append(
            f"| {rank} | {row['stock_code']} | {row['stock_name']} | {row['industry_sector']} | {float(row['factor_value']):.3f} | {row['trigger_rule_ids'] or '无'} |"
        )

    lines.extend(
        [
            "",
            "## 回测摘要",
            "",
            f"- 事件因子样本数：{metrics['event_factor_sample_count']['value']}",
            f"- 平均 Rank IC：{float(metrics['avg_rank_ic_5d']['value']):.4f}",
            f"- G5-G1 5 日收益差：{pct(metrics['top_bottom_group_spread_5d']['value'])}",
            f"- 5 日收益为正样本比例：{pct(metrics['positive_forward_return_rate_5d']['value'])}",
            f"- 未来函数审计：{metrics['future_info_audit']['value']}",
            "",
            "## 分组收益",
            "",
            "| 组别 | 样本数 | 5 日平均收益 |",
            "|------|--------|-------------------|",
        ]
    )
    for row in groups:
        lines.append(f"| {row['group']} | {row['sample_count']} | {pct(row['avg_forward_return_5d'])} |")

    lines.extend(
        [
            "",
            "## 风险与局限",
            "",
            "1. 当前文本为联网替换后的摘要化研究样本，正式研究需由 B 人工抽查来源、URL、日期和原文摘要。",
            "2. 当前行情来自联网前复权价格候选版，`adj_factor` 口径仍需人工复核，不能直接作为最终实证结论。",
            "3. 规则评分用于展示工程闭环，正式版本需在真实样本内/样本外切分中重估。",
            "4. 所有 forward return 均使用事件发生后的交易日计算，避免把事件后的收益信息提前写入因子。",
            "",
            DISCLAIMER,
            "",
        ]
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Research report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
