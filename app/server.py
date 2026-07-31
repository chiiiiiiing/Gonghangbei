"""Flask service for the AlphaLens demonstrable research workflow."""

from __future__ import annotations

import csv
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
APP_DIR = ROOT / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.live_analysis import SOURCE_TYPES, analyze_new_document  # noqa: E402


DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
app = Flask(__name__, static_folder=None)


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def repository_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() or "unknown"


def numeric_value(value: str) -> int | float | str:
    if value in {"pass", "pending", "fail"}:
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def data_status() -> dict[str, Any]:
    stock_pool = read_csv("stock_pool.csv")
    documents = read_csv("raw_documents.csv")
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    rules = read_csv("rules.csv")
    factors = read_csv("factors.csv")
    market = read_csv("market_data.csv")
    source_counts = Counter(row["source_type"] for row in documents)
    adj_placeholder = bool(market) and all(float(row["adj_factor"]) == 1.0 for row in market)
    return {
        "repository_commit": repository_commit(),
        "pipeline_mode": "official-shared-functions",
        "counts": {
            "stocks": len(stock_pool),
            "documents": len(documents),
            "events": len(events),
            "predicates": len(predicates),
            "qualified_rules": sum(row["status"] == "qualified" for row in rules),
            "factor_samples": len(factors),
            "market_rows": len(market),
        },
        "source_type_counts": dict(sorted(source_counts.items())),
        "market_period": {
            "start": min((row["trade_date"] for row in market), default=""),
            "end": max((row["trade_date"] for row in market), default=""),
        },
        "adj_factor_placeholder": adj_placeholder,
        "disclaimer": DISCLAIMER,
    }


def historical_backtest() -> dict[str, Any]:
    metric_rows = read_csv("backtest_metrics.csv")
    metrics = {row["metric"]: numeric_value(row["value"]) for row in metric_rows}
    descriptions = {row["metric"]: row["description"] for row in metric_rows}
    groups = [
        {
            "group": row["group"],
            "sample_count": int(row["sample_count"]),
            "avg_forward_return_5d": float(row["avg_forward_return_5d"]),
        }
        for row in read_csv("group_returns.csv")
    ]
    rank_ic = [
        {
            "trade_date": row["trade_date"],
            "rank_ic_5d": float(row["rank_ic_5d"]),
            "sample_count": int(row["sample_count"]),
        }
        for row in read_csv("rank_ic_timeseries.csv")
    ]
    rules = [
        {
            "rule_id": row["rule_id"],
            "rule_name": row["rule_name"],
            "condition": row["condition"],
            "target_label": row["target_label"],
            "support_count": int(row["support_count"]),
            "win_rate": float(row["win_rate"]),
            "avg_forward_return_5d": float(row["avg_forward_return_5d"]),
            "score": float(row["score"]),
        }
        for row in read_csv("rules.csv")
        if row["status"] == "qualified"
    ]
    snapshot = [
        {
            "trade_date": row["trade_date"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "industry_sector": row["industry_sector"],
            "factor_name": row["factor_name"],
            "factor_value": float(row["factor_value"]),
            "raw_score": float(row["raw_score"]),
            "trigger_rule_ids": row["trigger_rule_ids"],
        }
        for row in read_csv("factor_snapshot.csv")
    ]
    return {
        "scope": "historical_reference_only",
        "scope_note": "以下指标来自固定历史样本，不是本次新输入文本的事后收益或单次回测结果。",
        "metrics": metrics,
        "metric_descriptions": descriptions,
        "group_returns": groups,
        "rank_ic_timeseries": rank_ic,
        "qualified_rules": rules,
        "factor_snapshot": snapshot,
        "limitations": [
            "当前行情为前复权候选价，adj_factor=1 仅作占位字段，不是真实复权因子序列。",
            "事件样本与规则数量仍有限，历史统计不代表未来表现。",
            "当前演示回测未完整计入交易成本、流动性和做空约束。",
        ],
        "disclaimer": DISCLAIMER,
    }


def generate_report(analysis: dict[str, Any], history: dict[str, Any]) -> str:
    metrics = history["metrics"]
    stocks = analysis["stock_results"]
    rules = analysis["triggered_rules"]
    lines = [
        "# AlphaLens 新文本因子研究记录",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        "## 一、输入与事件",
        "",
        f"- 来源类型：`{analysis['source_type']}`",
        f"- 来源名称：{analysis['source_name']}",
        f"- 首次公开日期：{analysis['event_time']}",
        f"- 事件类型：`{analysis['event_type']}`",
        f"- 事件证据强度：{analysis['evidence_strength']:.2f}",
        "",
        "## 二、因子形成路径",
        "",
        "文本先经过实体链接与事件抽取，再按锁定 Schema 生成可解释谓词；冻结规则只匹配值为 true 的谓词组合，规则历史评分经证据强度和事件类型先验加权后形成候选因子值。",
        "",
        f"本次关联 {len(stocks)} 只样例股票，触发 {len(rules)} 条冻结规则。候选值用于研究排序与追溯，不是收益预测或买卖信号。",
        "",
        "| 股票 | 行业 | 候选因子 | 原始规则分 | 触发规则 |",
        "|---|---|---:|---:|---|",
    ]
    for stock in stocks[:15]:
        rule_ids = "、".join(rule["id"] for rule in stock["triggered_rules"]) or "无"
        lines.append(
            f"| {stock['name']}（{stock['code']}） | {stock['sector']} | "
            f"{stock['candidate_factor']:.4f} | {stock['raw_score']:.4f} | {rule_ids} |"
        )
    lines.extend(
        [
            "",
            "## 三、规则追溯",
            "",
        ]
    )
    if rules:
        for rule in rules:
            lines.append(
                f"- `{rule['id']}`：{rule['condition']}；历史支持数 {rule['support']}，"
                f"历史 5 日平均收益 {rule['avg_return']:.4f}，规则评分 {rule['score']:.4f}。"
            )
    else:
        lines.append("- 本次事件未触发达到最低样本门槛的冻结规则，因此候选因子值为 0。")
    lines.extend(
        [
            "",
            "## 四、历史回测参考",
            "",
            "> 这一部分来自固定历史样本，并非对本次新文本单独回测。",
            "",
            f"- 事件因子样本数：{metrics.get('event_factor_sample_count', 0)}",
            f"- 平均 Rank IC（5 日）：{metrics.get('avg_rank_ic_5d', 0):.6f}",
            f"- G5-G1 五日收益差：{metrics.get('top_bottom_group_spread_5d', 0):.6f}",
            f"- 正收益样本比例：{metrics.get('positive_forward_return_rate_5d', 0):.6f}",
            f"- 未来函数审计：{metrics.get('future_info_audit', 'pending')}",
            "",
            "## 五、限制",
            "",
            "- 当前行情为前复权候选价，`adj_factor=1` 仅作占位字段，不是真实复权因子序列。",
            "- 新文本只生成候选因子；只有积累到后续真实收益并遵守时间边界后，才能纳入下一轮历史检验。",
            "- 当前样本、规则与股票池规模有限，结果用于验证研究链路，不代表未来表现。",
            "",
            "## 六、免责声明",
            "",
            f"**{DISCLAIMER}**。AlphaLens 是量化研究助手，不提供买卖建议。",
        ]
    )
    return "\n".join(lines)


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    normalized = {key: str(value or "").strip() for key, value in payload.items()}
    if not normalized.get("content"):
        return None, "请提供正文内容"
    if not normalized.get("source_name"):
        return None, "请填写可核验的来源名称"
    source_type = normalized.get("source_type", "auto")
    if source_type not in SOURCE_TYPES | {"auto"}:
        return None, "来源类型不合法"
    event_date = normalized.get("event_date", "")
    try:
        parsed_date = datetime.strptime(event_date, "%Y-%m-%d").date()
    except ValueError:
        return None, "首次公开日期必须使用 YYYY-MM-DD"
    if parsed_date > date.today():
        return None, "首次公开日期不能晚于今天"
    source_url = normalized.get("source_url", "")
    if source_url and not source_url.startswith(("https://", "http://")):
        return None, "来源链接必须以 http:// 或 https:// 开头"
    return normalized, None


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/vendor/<path:filename>")
def vendor(filename: str):
    return send_from_directory(APP_DIR / "vendor", filename)


@app.get("/api/status")
def status():
    return jsonify(data_status())


@app.get("/api/backtest")
def backtest():
    return jsonify(historical_backtest())


@app.post("/api/analyze")
def analyze():
    payload, error = validate_payload(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    assert payload is not None
    try:
        result = analyze_new_document(payload, read_csv("stock_pool.csv"), read_csv("rules.csv"))
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    if "error" in result:
        return jsonify(result), 422
    history = historical_backtest()
    result["historical_backtest"] = history
    result["report"] = generate_report(result, history)
    result["disclaimer"] = DISCLAIMER
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8701, debug=False)
