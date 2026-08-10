"""Flask service for the AlphaLens demonstrable research workflow."""

from __future__ import annotations

import csv
import json
import os
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
REPLAY_PATH = SAMPLE_DIR / "replay_cases.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.gateway import AIServiceError, AISettings  # noqa: E402
from src.ai.research_layer import AIResearchLayer, validate_ai_output  # noqa: E402
from src.ai.source_quality import assess_source, cached_fetch_full_text  # noqa: E402
from src.pipeline.live_analysis import SOURCE_TYPES, analyze_new_document  # noqa: E402
from src.research.scoring import SCORING_VERSION  # noqa: E402
from src.macro.engine import (  # noqa: E402
    live_text_forecast,
    load_macro_backtest,
    load_macro_forecast,
    load_macro_status,
)


DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
app = Flask(__name__, static_folder=None)
AI_LAYER = AIResearchLayer()

# 演示示例：正文为可核验摘要，选择示例后系统自动抓取链接全文填充。
DEMO_EXAMPLES = [
    {
        "title": "关于印发《新型储能规模化建设专项行动方案（2025—2027年）》的通知",
        "content": "原文摘要：为推动新型储能高质量发展，国家发展改革委、国家能源局研究制定了《新型储能规模化建设专项行动方案（2025—2027年）》。现予印发，请结合实际认真抓好贯彻落实。",
        "type": "policy", "name": "中国政府网", "date": "2025-08-27",
        "url": "https://www.gov.cn/zhengce/zhengceku/202509/content_7040296.htm",
    },
    {
        "title": "上海璞泰来新能源科技集团股份有限公司关于投资建设年产72亿平方米锂离子电池隔膜建设项目的公告",
        "content": "原文摘要：璞泰来披露年产72亿平方米锂离子电池隔膜建设项目，计划总投资56亿元人民币。重要内容提示：交易实施尚需履行审批及其他相关程序。",
        "type": "announcement", "name": "巨潮资讯网", "date": "2026-05-21",
        "url": "http://static.cninfo.com.cn/finalpage/2026-05-21/1225319446.PDF",
    },
    {
        "title": "315GW+119GW！2025年光伏、风电年新增装机再创新高",
        "content": "原文摘要：国家能源局发布2025年全国电力统计数据，光伏、风电年新增装机规模再创新高，行业装机量受到市场关注。",
        "type": "news", "name": "腾讯新闻", "date": "2026-01-28",
        "url": "https://news.qq.com/rain/a/20260128A043VK00",
    },
]


class FrozenAIResearchLayer:
    """Replay an explicitly labelled model fixture through current validators."""

    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case

    def analyze(
        self,
        document: dict[str, str],
        stock_pool: list[dict[str, str]],
        rules: list[dict[str, str]],
    ) -> dict[str, Any]:
        validated, audit = validate_ai_output(self.case["ai_output"], document, stock_pool)
        metadata = self.case["metadata"]
        offline_settings = AISettings(
            mode="off",
            base_url="",
            api_key="",
            chat_model=metadata["model"],
            embedding_model="",
            timeout_seconds=1,
            json_mode="object",
        )
        retrieval = AIResearchLayer(offline_settings)._retrieve_rules(document, rules)
        return {
            "configured": True,
            "mode": "replay",
            "provider": "frozen-demo-fixture",
            "base_url": "",
            "chat_model": metadata["model"],
            "embedding_model": retrieval.get("model", ""),
            "structured_output": True,
            "prompt_version": metadata["prompt_version"],
            "requested": True,
            "used": True,
            "fallback": False,
            "reason": "",
            "request_id": metadata["request_id"],
            "usage": metadata.get("usage", {}),
            "response_format": "json_object",
            "embedding_retrieval": retrieval,
            "validation": audit,
            "result": validated,
        }


def request_ai_layer(api_key: str) -> AIResearchLayer:
    if not api_key:
        return AI_LAYER
    settings = AISettings(
        mode="api",
        base_url=os.getenv("ALPHALENS_DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        api_key=api_key,
        chat_model="deepseek-v4-flash",
        embedding_model=os.getenv("ALPHALENS_DEEPSEEK_EMBEDDING_MODEL", "").strip(),
        timeout_seconds=float(os.getenv("ALPHALENS_AI_TIMEOUT", "45")),
        json_mode="object",
    )
    return AIResearchLayer(settings)


def load_replay_cases() -> dict[str, dict[str, Any]]:
    if not REPLAY_PATH.exists():
        return {}
    payload = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
    return {str(case["case_id"]): case for case in payload.get("cases", [])}


@app.after_request
def disable_api_caching(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def repository_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        commit = result.stdout.strip()
    except (FileNotFoundError, OSError):
        commit = ""
    if commit:
        return commit
    # 打包交付时可能不含 .git 或未安装 git，回退到 VERSION 文件，保证审计页仍有版本标识。
    version_path = ROOT / "VERSION"
    if version_path.exists():
        label = version_path.read_text(encoding="utf-8").strip()
        if label:
            return label
    return "unknown"


def numeric_value(value: str) -> int | float | str:
    if value in {"pass", "pending", "fail"}:
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def int_metric(metrics: dict[str, Any], name: str, default: int = 0) -> int:
    """Robustly coerce a metric value (int / float / numeric string) to int."""
    value = metrics.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def ai_candidate_rule_count() -> int:
    path = SAMPLE_DIR / "ai_candidate_rules.csv"
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _latest_annotation_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse append-only retry records to the latest result per document."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        doc_id = str(record.get("doc_id", "")).strip()
        if not doc_id:
            continue
        previous = latest.get(doc_id)
        if previous is None or str(record.get("generated_at", "")) >= str(previous.get("generated_at", "")):
            latest[doc_id] = record
    return list(latest.values())


def _annotation_failure_category(reason: str) -> str:
    """Group strict cache rejections without hiding their original reason."""
    if "证据文本无法回溯" in reason:
        return "事件或关系证据不是原文连续片段"
    if "事件类型" in reason and "来源类型" in reason:
        return "事件类型与来源类型不相容"
    if "19 个谓词" in reason:
        return "逐股票 19 个谓词不完整"
    if "stock_analyses" in reason or "关系证据校验" in reason:
        return "逐股票关系证据或股票池校验未通过"
    if "未返回可解析的结构化 JSON" in reason:
        return "模型未返回可解析的结构化 JSON"
    return "其他严格结构校验拒绝"


def ai_annotation_cache_summary(document_count: int) -> dict[str, Any]:
    """Expose coverage and rejection categories for replayable AI-cache audit."""
    path = SAMPLE_DIR / "ai_annotations.jsonl"
    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    latest_records = _latest_annotation_records(records)
    success_count = sum(record.get("status") == "success" for record in latest_records)
    failed_records = [record for record in latest_records if record.get("status") == "failed"]
    categories = Counter(
        _annotation_failure_category(str(record.get("reason", "")))
        for record in failed_records
    )
    return {
        "success_count": success_count,
        "failed_count": len(failed_records),
        "document_count": document_count,
        "missing_count": max(document_count - success_count - len(failed_records), 0),
        "record_count": len(records),
        "status": "complete" if document_count and success_count >= document_count else "incomplete",
        "failure_categories": [
            {"category": category, "count": count}
            for category, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


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
        "rule_version": "R1",
        "replay_case_count": len(load_replay_cases()),
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
        "ai": AI_LAYER.status(),
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
    diagnostics = {
        row["rule_id"]: row
        for row in (read_csv("rule_diagnostics.csv") if (SAMPLE_DIR / "rule_diagnostics.csv").exists() else [])
    }
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
            "independent_document_count": int(diagnostics.get(row["rule_id"], {}).get("independent_document_count", row["support_count"])),
            "independent_date_count": int(diagnostics.get(row["rule_id"], {}).get("independent_date_count", 0)),
            "oos_document_count": int(diagnostics.get(row["rule_id"], {}).get("oos_document_count", 0)),
            "oos_avg_excess_return_5d": float(diagnostics.get(row["rule_id"], {}).get("oos_avg_excess_return_5d", 0.0)),
            "score_components": {
                "posterior_win_rate": float(diagnostics.get(row["rule_id"], {}).get("posterior_win_rate", 0.0)),
                "shrunk_return": float(diagnostics.get(row["rule_id"], {}).get("shrunk_return", 0.0)),
                "return_component": float(diagnostics.get(row["rule_id"], {}).get("return_component", 0.0)),
                "half_year_stability": float(diagnostics.get(row["rule_id"], {}).get("half_year_stability", 0.0)),
                "coverage": float(diagnostics.get(row["rule_id"], {}).get("coverage_component", 0.0)),
                "evidence": float(diagnostics.get(row["rule_id"], {}).get("evidence_component", 0.0)),
                "complexity_penalty": float(diagnostics.get(row["rule_id"], {}).get("complexity_penalty", 0.0)),
            },
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
            "trigger_event_ids": row["trigger_event_ids"],
            "trigger_rule_ids": row["trigger_rule_ids"],
        }
        for row in read_csv("factor_snapshot.csv")
    ]
    split_metrics: dict[str, dict[str, Any]] = {}
    split_groups: dict[str, list[dict[str, Any]]] = {"discovery": [], "oos": []}
    split_metric_path = SAMPLE_DIR / "backtest_metrics_by_split.csv"
    if split_metric_path.exists():
        for row in read_csv("backtest_metrics_by_split.csv"):
            split_metrics.setdefault(row["split"], {})[row["metric"]] = numeric_value(row["value"])
    split_group_path = SAMPLE_DIR / "group_returns_by_split.csv"
    if split_group_path.exists():
        for row in read_csv("group_returns_by_split.csv"):
            split_groups.setdefault(row["split"], []).append(
                {
                    "group": row["group"],
                    "sample_count": int(row["sample_count"]),
                    "avg_forward_return_5d": float(row["avg_forward_return_5d"]),
                }
            )
    splits = {
        split: {
            "metrics": split_metrics.get(split, {}),
            "group_returns": split_groups.get(split, []),
            "rank_ic_timeseries": [
                row
                for row in rank_ic
                if (row["trade_date"] < "2026-01-01") == (split == "discovery")
            ],
        }
        for split in ("discovery", "oos")
    }
    oos_metrics = splits["oos"]["metrics"]
    valid_ic_dates = int_metric(oos_metrics, "rank_ic_valid_date_count")
    decay_assessment = {
        "status": "insufficient_evidence" if valid_ic_dates < 20 else "measurable",
        "label": "无法判断衰减" if valid_ic_dates < 20 else "可评估衰减",
        "warning": "OOS 有效日期不足，既不能证明稳定有效，也不能据此确认因子已失效。",
    }
    return {
        "scope": "historical_reference_only",
        "scope_note": "以下指标来自固定历史样本，不是本次新输入文本的事后收益或单次回测结果。",
        "metrics": metrics,
        "metric_descriptions": descriptions,
        "group_returns": groups,
        "rank_ic_timeseries": rank_ic,
        "qualified_rules": rules,
        "factor_snapshot": snapshot,
        "splits": splits,
        "decay_assessment": decay_assessment,
        "audit": {
            "discovery_period": "2024-01-01 至 2025-12-31",
            "oos_period": "2026-01-01 至 2026-06-30",
            "return_label": "5 日行业等权超额收益",
            "rule_support_unit": "独立文档 + 独立日期 + 股票覆盖",
        },
        "limitations": [
            "当前行情为前复权候选价，adj_factor=1 仅作占位字段，不是真实复权因子序列。",
            "事件样本与规则数量仍有限，历史统计不代表未来表现。",
            "当前演示回测未完整计入交易成本、流动性和做空约束。",
        ],
        "disclaimer": DISCLAIMER,
    }


def research_audit() -> dict[str, Any]:
    documents = read_csv("raw_documents.csv")
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    rules = read_csv("rules.csv")
    market = read_csv("market_data.csv")
    diagnostics_path = SAMPLE_DIR / "rule_diagnostics.csv"
    diagnostics = read_csv("rule_diagnostics.csv") if diagnostics_path.exists() else []
    review_path = SAMPLE_DIR / "manual_review_summary.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    source_review = review.get("source_verification", {})
    split_source_counts: dict[str, Counter[str]] = {
        "discovery": Counter(),
        "oos": Counter(),
    }
    for row in documents:
        split = "discovery" if row["publish_time"] < "2026-01-01" else "oos"
        split_source_counts[split][row["source_type"]] += 1
    target_per_type = 25
    split_coverage = {
        split: {
            source_type: {
                "count": counts[source_type],
                "target": target_per_type,
                "remaining": max(target_per_type - counts[source_type], 0),
                "status": "met" if counts[source_type] >= target_per_type else "insufficient",
            }
            for source_type in sorted(SOURCE_TYPES)
        }
        for split, counts in split_source_counts.items()
    }
    return {
        "counts": {
            "stocks": len(read_csv("stock_pool.csv")),
            "documents": len(documents),
            "events": len(events),
            "predicates": len(predicates),
            "qualified_rules": sum(row["status"] == "qualified" for row in rules),
            "market_rows": len(market),
        },
        "source_type_counts": dict(sorted(Counter(row["source_type"] for row in documents).items())),
        "split_source_coverage": split_coverage,
        "event_type_counts": dict(sorted(Counter(row["event_type"] for row in events).items())),
        "source_verification": {
            "automated_pass_count": int(source_review.get("automated_pass_count", len(documents))),
            "unique_url_count": len({row["url"] for row in documents}),
            "status": source_review.get("status", "队伍人工抽样确认待完成"),
        },
        "event_review": review.get("event_review", {"reviewed_count": 0, "status": "待人工抽检"}),
        "predicate_review": review.get("predicate_review", {"reviewed_count": 0, "status": "待人工抽检"}),
        "market": {
            "start": min((row["trade_date"] for row in market), default=""),
            "end": max((row["trade_date"] for row in market), default=""),
            "adj_factor_placeholder": bool(market) and all(float(row["adj_factor"]) == 1.0 for row in market),
        },
        "rule_diagnostics": diagnostics,
        "model": {
            "chat_model": AI_LAYER.status()["chat_model"],
            "prompt_version": AI_LAYER.status()["prompt_version"],
            "rule_version": "R1",
            "scoring_version": SCORING_VERSION,
            "repository_commit": repository_commit(),
        },
        "ai_annotation_cache": ai_annotation_cache_summary(len(documents)),
        "ai_candidate_rules_count": ai_candidate_rule_count(),
        "future_info_audit": historical_backtest()["metrics"].get("future_info_audit", "pending"),
        "disclaimer": DISCLAIMER,
    }


def generate_report(analysis: dict[str, Any], history: dict[str, Any]) -> str:
    metrics = history["metrics"]
    oos_metrics = history.get("splits", {}).get("oos", {}).get("metrics", {})
    stocks = analysis["stock_results"]
    rules = analysis["triggered_rules"]
    ai = analysis.get("ai_analysis", {})
    ai_result = ai.get("result") or {}
    lines = [
        "# AlphaLens 新文本行业景气预测与证据报告",
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
        "## 二、行业同比增速预测",
        "",
    ]
    forecast = analysis.get("text_forecast") or {}
    if forecast:
        lines.extend(
            [
                f"- 目标：{forecast.get('target_name', '')}",
                f"- 目标期：{forecast.get('target_period_end', '')}",
                f"- 单文本条件预测同比：{float(forecast.get('predicted_yoy', 0)):.2f}%",
                f"- 90% 预测区间：[{float(forecast.get('lower_90', 0)):.2f}%, {float(forecast.get('upper_90', 0)):.2f}%]",
                f"- 相对最新已公布值的预测加速度：{float(forecast.get('predicted_acceleration', 0)):.2f} 个百分点",
                f"- 冻结模型：{forecast.get('model_name', '')}；训练/验证文本 {forecast.get('training_document_count', 0)}/{forecast.get('validation_document_count', 0)} 篇",
                f"- 验证结论：{forecast.get('analysis_conclusion', '')}",
                f"- 口径：{forecast.get('forecast_basis', '')}",
                "",
                "### 主要文本特征贡献",
                "",
            ]
        )
        for item in forecast.get("top_contributions", [])[:6]:
            lines.append(f"- `{item.get('feature')}`：{float(item.get('contribution_pct_point', 0)):+.4f} 个百分点")
        lines.extend(["", "## 三、来源与完整性", ""])
    else:
        lines.extend(["- 未生成单文本同比预测。", "", "## 三、来源与完整性", ""])
    source_audit = analysis.get("source_audit")
    if source_audit:
        lines.extend(
            [
                f"- 正文链接：`{source_audit.get('url') or '无'}`",
                f"- 链接抓取：`{source_audit.get('fetch_status', 'no_url')}`（抓取 {source_audit.get('fetched_chars', 0)} 字 / 摘要 {source_audit.get('summary_chars', 0)} 字）",
                f"- 链接类型：`{source_audit.get('link_type')}` · 来源权威度：{source_audit.get('authority')}",
                f"- 完整性：`{source_audit.get('completeness')}`（{source_audit.get('completeness_score'):.2f}）",
                f"- 置信度校准上限：{source_audit.get('confidence_cap'):.2f} · 被调低项：{analysis.get('confidence_calibrated_count', 0)}",
                f"- 判定理由：{source_audit.get('reason', '')}",
                "",
            ]
        )
    else:
        lines.extend(["", "- 本次为冻结回放或规则复现，未做链接抓取与置信度校准。", ""])
    lines.extend(
        [
            "## 四、因子形成路径",
        "",
        "文本先经过 Embedding 检索（含历史 AI 结论 RAG 参考）和大模型结构化抽取，再按锁定 Schema 生成 19 个谓词。AI 谓词与确定性程序按融合值参与判定：一致采纳、冲突按 AI 置信度加权、AI 缺失回退规则值；AI 提议的候选规则以「未历史统计验证」标注参与实时候选值。",
        "",
        f"本次关联 {len(stocks)} 只样例股票，触发 {len(rules)} 条冻结规则，AI 实时候选规则 {sum(len(stock.get('ai_candidate_rules', [])) for stock in stocks)} 条。候选值用于研究排序与追溯，不是收益预测或买卖信号。",
        "",
        "### AI 研究层",
        "",
        f"- 运行状态：{'模式一已调用并通过结构校验' if ai.get('used') else '模式二仅规则复现，未调用 AI'}",
        f"- 模型：{ai.get('chat_model', '--')}",
        f"- Prompt 版本：{ai.get('prompt_version', '--')}",
        f"- 结构校验：{'模型自动修复后通过' if ai.get('repair_attempted') else '首次返回通过'}",
        f"- Embedding 相似规则：{len(ai.get('embedding_retrieval', {}).get('matches', []))} 条",
        f"- 待统计验证候选规则：{len(ai_result.get('candidate_rules', []))} 条",
        f"- 一致性门控：{'通过' if analysis.get('consensus_gate_passed') else '存在排除项'}",
        f"- 门控排除谓词：{'、'.join(analysis.get('disputed_predicates', [])) or '无'}",
        "- AI 只提出事件、谓词和规则候选；门控、冻结规则匹配、因子计算与回测由确定性程序完成。",
        "",
        "| 股票 | 行业 | 候选因子 | 原始规则分 | 触发规则 |",
        "|---|---|---:|---:|---|",
    ])
    for stock in stocks[:15]:
        rule_ids = "、".join(rule["id"] for rule in stock["triggered_rules"]) or "无"
        lines.append(
            f"| {stock['name']}（{stock['code']}） | {stock['sector']} | "
            f"{stock['candidate_factor']:.4f} | {stock['raw_score']:.4f} | {rule_ids} |"
        )
    lines.extend(
        [
            "",
            "## 五、规则追溯",
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
            "## 六、历史回测参考",
            "",
            "> 这一部分来自固定历史样本，并非对本次新文本单独回测。",
            "",
            f"- OOS 平均 Rank IC（5 日）：{float(oos_metrics.get('avg_rank_ic_5d', 0)):.6f}",
            f"- OOS ICIR：{float(oos_metrics.get('rank_ic_ir', 0)):.6f}",
            f"- OOS 有效 IC 日数：{int(oos_metrics.get('rank_ic_valid_date_count', 0))}",
            f"- OOS G5-G1 行业超额收益差：{float(oos_metrics.get('top_bottom_group_spread_5d', 0)):.6f}",
            f"- OOS 证据状态：{oos_metrics.get('evidence_status', 'insufficient')}",
            f"- 未来函数审计：{metrics.get('future_info_audit', 'pending')}",
            "",
            "## 七、限制",
            "",
            "- 当前行情为前复权候选价，`adj_factor=1` 仅作占位字段，不是真实复权因子序列。",
            "- 当前 OOS 有效日期过少，证据不足，不能宣称因子稳定有效。",
            "- 同比预测只使用本次新输入文本的结构化证据；历史文本仅用于冻结模型与规则参数。",
            "- 当前样本、规则与股票池规模有限，结果用于验证研究链路，不代表未来表现。",
            "",
            "## 八、免责声明",
            "",
            f"**{DISCLAIMER}**。AlphaLens 是量化研究助手，不提供买卖建议。",
        ]
    )
    return "\n".join(lines)


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    normalized = {key: str(value or "").strip() for key, value in payload.items()}
    source_url = normalized.get("source_url", "")
    if not normalized.get("content") and not source_url:
        return None, "请提供正文内容（或填写正文链接，系统会自动抓取全文）"
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
    analysis_mode = normalized.get("analysis_mode", "hybrid")
    if analysis_mode != "hybrid":
        return None, "实时分析必须使用 hybrid（大模型候选 + 规则校验）"
    if len(normalized.get("api_key", "")) > 512:
        return None, "API Key 长度不合法"
    normalized["analysis_mode"] = analysis_mode
    return normalized, None


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/vendor/<path:filename>")
def vendor(filename: str):
    return send_from_directory(APP_DIR / "vendor", filename)


@app.get("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(APP_DIR / "assets", filename)


@app.get("/api/status")
def status():
    payload = data_status()
    payload["macro"] = load_macro_status()
    return jsonify(payload)


@app.get("/api/macro/status")
def macro_status():
    return jsonify(load_macro_status())


@app.get("/api/macro/forecast")
def macro_forecast():
    return jsonify(load_macro_forecast())


@app.get("/api/macro/backtest")
def macro_backtest():
    return jsonify(load_macro_backtest())


@app.get("/api/examples")
def examples():
    return jsonify({"examples": DEMO_EXAMPLES})


@app.get("/api/example/<int:index>/fulltext")
def example_fulltext(index: int):
    """返回示例正文链接的抓取全文（尽力而为，失败回退到摘要）。"""
    if not 0 <= index < len(DEMO_EXAMPLES):
        return jsonify({"error": "示例不存在"}), 404
    example = DEMO_EXAMPLES[index]
    fetch = cached_fetch_full_text(example["url"])
    full_text = fetch.get("text", "").strip() or example["content"]
    return jsonify(
        {
            "full_text": full_text,
            "status": fetch.get("status", "no_url"),
            "fetched_chars": fetch.get("fetched_chars", 0),
            "summary_chars": len(example["content"]),
        }
    )


@app.get("/api/backtest")
def backtest():
    return jsonify(historical_backtest())


@app.get("/api/audit")
def audit():
    return jsonify(research_audit())


@app.get("/api/ai/status")
def ai_status():
    return jsonify(AI_LAYER.status())


@app.post("/api/ai/check")
def ai_check():
    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        return jsonify({"ok": False, "error": "请填写 DeepSeek API Key"}), 400
    if len(api_key) > 512:
        return jsonify({"ok": False, "error": "API Key 长度不合法"}), 400
    layer = request_ai_layer(api_key)
    try:
        result = layer.gateway.check_connection()
    except AIServiceError as exc:
        status_code = exc.status_code if exc.status_code in {401, 402, 403, 429} else 503
        return jsonify({"ok": False, "error": str(exc)}), status_code
    return jsonify({**result, "provider": "deepseek", "credential_retained": False})


@app.get("/api/replay/<case_id>")
def replay(case_id: str):
    case = load_replay_cases().get(case_id)
    if case is None:
        return jsonify({"error": "冻结回放案例不存在"}), 404
    payload = {key: str(value) for key, value in case["request"].items()}
    payload["analysis_mode"] = "hybrid"
    result = analyze_new_document(
        payload,
        read_csv("stock_pool.csv"),
        read_csv("rules.csv"),
        ai_layer=FrozenAIResearchLayer(case),
        use_ai=True,
        persist_ai_candidates=False,
    )
    if "error" in result:
        return jsonify(result), 422
    result["is_replay"] = True
    result["replay_metadata"] = case["metadata"]
    history = historical_backtest()
    result["historical_backtest"] = history
    result["text_forecast"] = live_text_forecast(result)
    result["report"] = generate_report(result, history)
    result["disclaimer"] = DISCLAIMER
    return jsonify(result)


def _perform_analysis(raw_payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    payload, error = validate_payload(raw_payload)
    if error:
        return {"error": error}, 400
    assert payload is not None
    api_key = payload.pop("api_key", "")
    ai_layer = request_ai_layer(api_key)
    # 实时路径：有正文链接则尽力抓取全文供 AI 阅读；只给链接时用全文补正文。
    source_url = payload.get("source_url", "").strip()
    if source_url:
        fetch = cached_fetch_full_text(source_url)
        payload["fetch_diagnostics"] = fetch
        fetched_text = fetch.get("text", "").strip()
        if not payload.get("content") and fetch.get("status") in {"failed", "no_url"}:
            return {"error": f"无法从链接抓取正文：{fetch.get('error') or '链接无效'}"}, 400
        if fetched_text:
            payload["fetched_content"] = fetched_text
            if not payload.get("content"):
                payload["content"] = fetched_text[:8000]
        source_diagnostics = assess_source(
            {
                "url": source_url,
                "content": payload.get("content", ""),
                "source_type": payload.get("source_type", ""),
                "source_name": payload.get("source_name", ""),
            },
            fetch,
        )
        payload["source_diagnostics"] = source_diagnostics
    try:
        result = analyze_new_document(
            payload,
            read_csv("stock_pool.csv"),
            read_csv("rules.csv"),
            ai_layer=ai_layer,
            use_ai=True,
            # 实时请求只展示未经历史统计验证的 AI 候选规则，不能污染
            # 受版本控制的演示样本；入库只能由离线人工核验流程完成。
            persist_ai_candidates=False,
        )
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}, 400
    if "error" in result:
        status_code = 503 if result.get("error_code") in {"ai_required", "embedding_required"} else 422
        return result, status_code
    result["ai_analysis"]["credential_source"] = "request" if api_key else "environment_or_fallback"
    history = historical_backtest()
    result["historical_backtest"] = history
    result["text_forecast"] = live_text_forecast(result)
    result["report"] = generate_report(result, history)
    result["disclaimer"] = DISCLAIMER
    return result, 200


@app.post("/api/analyze")
def analyze():
    result, status_code = _perform_analysis(request.get_json(silent=True) or {})
    return jsonify(result), status_code


@app.post("/api/macro/analyze")
def macro_analyze():
    result, status_code = _perform_analysis(request.get_json(silent=True) or {})
    return jsonify(result), status_code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("ALPHALENS_DEMO_PORT", "8701")), debug=False)
