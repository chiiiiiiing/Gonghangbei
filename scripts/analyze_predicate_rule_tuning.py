"""Analyze predicate selection, rule thresholds, and factor construction.

This script is intentionally read-only for data/sample inputs. It writes a
Markdown tuning report for the current AlphaLens demo outputs.
"""

from __future__ import annotations

import itertools
import math
import os
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pandas as pd
except ModuleNotFoundError:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists() and Path(sys.executable) != venv_python:
        os.execv(str(venv_python), [str(venv_python), *sys.argv])
    raise

SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "谓词筛选与规则调参报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"

METADATA_COLUMNS = {"event_id", "doc_id", "stock_code", "event_type", "event_time"}
SCORE_COLUMNS = {"event_evidence_strength", "event_has_short_term_price_impact"}

PREDICATE_RECOMMENDATIONS = {
    "has_policy_support": (
        "保留并拆细",
        "当前只等价于 event_type=policy_support，适合作为政策类基础谓词；下一步可拆成税收/补贴/产业规划/以旧换新等政策路径，避免规则只学到事件类型。",
    ),
    "policy_directly_related_to_business": (
        "重写后保留",
        "当前完全包含于政策、核心产品和权威来源谓词，区分度偏弱；建议补充产业链环节词表，并拆成 demand_side_policy、supply_side_policy、capex_policy 等可解释子谓词。",
    ),
    "event_mentions_core_product": (
        "保留",
        "覆盖面适中且跨股票、跨事件类型，适合作为规则基础谓词；但需要和更具体的事件路径谓词组合，避免变成泛化关键词。",
    ),
    "evidence_from_authoritative_source": (
        "保留并拆分",
        "覆盖过宽且与政策类高度重合；建议兼容新增 source_government_or_exchange、source_company_announcement、source_major_media 三个派生谓词。",
    ),
    "source_government_or_exchange": (
        "保留",
        "从权威来源中拆出的政府/交易所来源谓词，适合政策、监管和交易所问询类规则。",
    ),
    "source_company_announcement": (
        "保留观察",
        "公司公告来源适合与风险披露、产能扩张、核心产品等谓词组合，单独使用可能过宽。",
    ),
    "source_major_media": (
        "保留观察",
        "主流财经媒体来源适合与关注扩散类谓词组合，避免把普通新闻都视为强信号。",
    ),
    "social_attention_spikes": (
        "保留并重定义共现",
        "当前只在 attention_spread 事件中为真，与政策支持规则天然不共现；建议允许政策文件被后续媒体扩散证据触发，或新增 policy_attention_followup。",
    ),
    "policy_attention_followup": (
        "新增保留",
        "用于连接政策事件与后续关注扩散，修复政策支持和 attention_spread 不共现导致的空规则问题。",
    ),
    "institutional_attention_increases": (
        "保留观察",
        "样本很少但当前收益/胜率表现较强，适合进入 exploratory 档；需要更多机构调研、研报、IR 活动证据后再升格为正式规则。",
    ),
    "investor_questions_increase": (
        "暂停/补数据",
        "当前没有触发样本；除非事件抽取开始稳定产出 investor_question_pressure，否则不应参与合格规则筛选。",
    ),
    "management_response_vague": (
        "暂停/补数据",
        "当前没有触发样本；后续应与互动问答事件一起构建信息不确定性规则，暂不单独作为规则条件。",
    ),
    "announcement_contains_uncertainty": (
        "合并为风险谓词",
        "当前仅 3 次触发，方向偏负但样本不足；建议并入 risk_or_uncertainty_disclosure，覆盖公告风险提示、问询函、减值和履约风险。",
    ),
    "risk_or_uncertainty_disclosure": (
        "新增保留",
        "把过稀的不确定性公告扩展为风险披露类谓词，便于形成可解释的风险观察规则。",
    ),
    "demand_side_policy": (
        "新增保留",
        "把政策支持拆成需求侧路径，便于解释政策如何作用于终端需求、消费或补贴。",
    ),
    "supply_side_policy": (
        "新增保留",
        "把政策支持拆成供给侧路径，便于解释政策如何作用于制造、产业链和设备更新。",
    ),
    "capacity_policy_support": (
        "新增保留",
        "把政策/权威事件与产能建设连接，便于形成产能政策规则族。",
    ),
    "event_evidence_strength": (
        "保留为权重",
        "该列与 events.evidence_strength 完全同源，适合作为权重或过滤阈值，不建议当作被挖掘的独立谓词。",
    ),
    "event_has_short_term_price_impact": (
        "保留为先验并重校准",
        "当前是事件类型先验，不是从样本学习出的结果；建议只在 discovery split 内校准，避免用全样本回报反向塑造因子。",
    ),
}


def read_csv(filename: str) -> pd.DataFrame:
    return pd.read_csv(
        SAMPLE_DIR / filename,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8",
    )


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def is_true(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value * 100:.{digits}f}%"


def num(value: float | None, digits: int = 4) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value:.{digits}f}"


def bps(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value * 10000:.1f}"


def safe_corr(left: pd.Series, right: pd.Series) -> float | None:
    frame = pd.DataFrame({"left": to_num(left), "right": to_num(right)}).dropna()
    if len(frame) < 3:
        return None
    if frame["left"].nunique() < 2 or frame["right"].nunique() < 2:
        return None
    value = frame["left"].corr(frame["right"])
    return float(value) if value is not None and math.isfinite(value) else None


def mean_or_none(series: pd.Series) -> float | None:
    values = to_num(series).dropna()
    if values.empty:
        return None
    return float(values.mean())


def win_rate_or_none(series: pd.Series) -> float | None:
    values = to_num(series).dropna()
    if values.empty:
        return None
    return float((values > 0).mean())


def top_counts(series: pd.Series, limit: int = 4) -> str:
    counts = series.value_counts()
    if counts.empty:
        return "-"
    return "、".join(f"{key}:{value}" for key, value in counts.head(limit).items())


def clean_cell(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def md_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    rows = list(rows)
    if not rows:
        return "_无_"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(clean_cell(value) for value in row) + " |")
    return "\n".join(lines)


def bool_predicate_columns(matrix: pd.DataFrame) -> list[str]:
    return [
        column
        for column in matrix.columns
        if column not in METADATA_COLUMNS and column not in SCORE_COLUMNS
    ]


def build_enriched_tables() -> dict[str, pd.DataFrame]:
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    matrix = read_csv("predicate_matrix.csv")
    returns = read_csv("event_forward_returns.csv")
    rules = read_csv("rules.csv")
    factors = read_csv("factors.csv")
    snapshot = read_csv("factor_snapshot.csv")
    metrics = read_csv("backtest_metrics.csv")
    stocks = read_csv("stock_pool.csv")

    enriched = (
        matrix.merge(
            returns[
                [
                    "event_id",
                    "entry_trade_date",
                    "forward_return_5d",
                    "forward_return_10d",
                    "future_info_ok",
                ]
            ],
            on="event_id",
            how="left",
        )
        .merge(events[["event_id", "evidence_strength", "subject", "object"]], on="event_id", how="left")
        .merge(stocks[["stock_code", "stock_name", "industry_sector"]], on="stock_code", how="left")
    )
    enriched["event_time_dt"] = pd.to_datetime(enriched["event_time"], errors="coerce")
    return {
        "events": events,
        "predicates": predicates,
        "matrix": matrix,
        "returns": returns,
        "rules": rules,
        "factors": factors,
        "snapshot": snapshot,
        "metrics": metrics,
        "stocks": stocks,
        "enriched": enriched,
    }


def predicate_statistics(enriched: pd.DataFrame, bool_columns: list[str]) -> list[dict[str, object]]:
    total_events = len(enriched)
    all_returns = to_num(enriched["forward_return_5d"])
    all_avg_return = float(all_returns.dropna().mean()) if all_returns.notna().any() else None
    rows: list[dict[str, object]] = []

    for column in bool_columns:
        mask = is_true(enriched[column])
        returns = to_num(enriched.loc[mask, "forward_return_5d"]).dropna()
        evidence = to_num(enriched.loc[mask, "evidence_strength"]).dropna()
        trigger_count = int(mask.sum())
        avg_return = float(returns.mean()) if not returns.empty else None
        lift = avg_return - all_avg_return if avg_return is not None and all_avg_return is not None else None
        flags = []
        if trigger_count == 0:
            flags.append("未触发")
        elif trigger_count < 5 or trigger_count / total_events < 0.03:
            flags.append("过稀")
        if trigger_count / total_events > 0.8:
            flags.append("过密")
        if trigger_count > 0 and enriched.loc[mask, "event_type"].nunique() == 1:
            flags.append("事件类型单一")
        if trigger_count > 0 and enriched.loc[mask, "stock_code"].nunique() <= 3:
            flags.append("股票覆盖少")
        corr_ret = safe_corr(mask.astype(int), enriched["forward_return_5d"])
        if corr_ret is not None and abs(corr_ret) < 0.01:
            flags.append("收益区分弱")
        action, note = PREDICATE_RECOMMENDATIONS.get(column, ("观察", "暂无人工建议。"))
        rows.append(
            {
                "predicate": column,
                "trigger_count": trigger_count,
                "trigger_rate": trigger_count / total_events if total_events else 0.0,
                "event_type_count": int(enriched.loc[mask, "event_type"].nunique()),
                "stock_count": int(enriched.loc[mask, "stock_code"].nunique()),
                "event_types": top_counts(enriched.loc[mask, "event_type"]),
                "return_samples": int(len(returns)),
                "win_rate": float((returns > 0).mean()) if not returns.empty else None,
                "avg_return": avg_return,
                "lift": lift,
                "avg_evidence": float(evidence.mean()) if not evidence.empty else None,
                "corr_return": corr_ret,
                "corr_evidence": safe_corr(mask.astype(int), enriched["evidence_strength"]),
                "flags": "、".join(flags) if flags else "正常",
                "action": action,
                "note": note,
            }
        )
    return rows


def score_statistics(enriched: pd.DataFrame, score_columns: list[str]) -> list[dict[str, object]]:
    rows = []
    for column in score_columns:
        values = to_num(enriched[column]).dropna()
        q75 = float(values.quantile(0.75)) if not values.empty else None
        high_mask = to_num(enriched[column]) >= q75 if q75 is not None else pd.Series(False, index=enriched.index)
        high_returns = to_num(enriched.loc[high_mask, "forward_return_5d"]).dropna()
        rows.append(
            {
                "predicate": column,
                "count": int(values.count()),
                "unique": int(values.nunique()),
                "mean": float(values.mean()) if not values.empty else None,
                "std": float(values.std()) if len(values) > 1 else None,
                "min": float(values.min()) if not values.empty else None,
                "max": float(values.max()) if not values.empty else None,
                "corr_return": safe_corr(enriched[column], enriched["forward_return_5d"]),
                "corr_evidence": safe_corr(enriched[column], enriched["evidence_strength"]),
                "top_quartile_win_rate": float((high_returns > 0).mean()) if not high_returns.empty else None,
                "top_quartile_avg_return": float(high_returns.mean()) if not high_returns.empty else None,
                "action": PREDICATE_RECOMMENDATIONS.get(column, ("观察", ""))[0],
                "note": PREDICATE_RECOMMENDATIONS.get(column, ("观察", ""))[1],
            }
        )
    return rows


def redundancy_rows(enriched: pd.DataFrame, bool_columns: list[str]) -> list[dict[str, object]]:
    rows = []
    true_sets = {
        column: set(enriched.loc[is_true(enriched[column]), "event_id"])
        for column in bool_columns
    }
    for left, right in itertools.combinations(bool_columns, 2):
        left_set = true_sets[left]
        right_set = true_sets[right]
        union = left_set | right_set
        intersection = left_set & right_set
        if not union:
            continue
        left_in_right = len(intersection) / len(left_set) if left_set else 0.0
        right_in_left = len(intersection) / len(right_set) if right_set else 0.0
        jaccard = len(intersection) / len(union)
        corr = safe_corr(is_true(enriched[left]).astype(int), is_true(enriched[right]).astype(int))
        include = (
            left_in_right >= 0.95
            or right_in_left >= 0.95
            or jaccard >= 0.85
            or (corr is not None and abs(corr) >= 0.8)
        )
        if not include:
            continue
        if left_in_right >= 0.95:
            relation = f"{left} 基本包含于 {right}"
        elif right_in_left >= 0.95:
            relation = f"{right} 基本包含于 {left}"
        elif corr is not None and corr < -0.8:
            relation = "强负相关/近似互斥"
        else:
            relation = "高度重合"
        rows.append(
            {
                "left": left,
                "right": right,
                "left_count": len(left_set),
                "right_count": len(right_set),
                "intersection": len(intersection),
                "jaccard": jaccard,
                "corr": corr,
                "relation": relation,
            }
        )
    return sorted(rows, key=lambda row: (abs(row["corr"] or 0.0), row["jaccard"]), reverse=True)


def period_mask(enriched: pd.DataFrame, period: str) -> pd.Series:
    if period == "discovery":
        return enriched["event_time_dt"].le(pd.Timestamp("2025-12-31"))
    if period == "oos":
        return enriched["event_time_dt"].between(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-30"))
    return pd.Series(True, index=enriched.index)


def matched_rule_mask(enriched: pd.DataFrame, rule_def: dict[str, object]) -> pd.Series:
    predicate_true = rule_def.get("predicate_true", [])
    event_types = rule_def.get("event_types", [])
    mask = pd.Series(True, index=enriched.index)
    for predicate_name in predicate_true:
        if predicate_name in enriched.columns:
            mask &= is_true(enriched[predicate_name])
        else:
            mask &= False
    if event_types:
        mask &= enriched["event_type"].isin(event_types)
    return mask


def rule_def_from_rule_row(rule_row: pd.Series) -> dict[str, object]:
    predicate_true: list[str] = []
    event_types: list[str] = []
    for part in str(rule_row["condition"]).split(" AND "):
        item = part.strip()
        if not item:
            continue
        if item.startswith("event_type = "):
            event_types.append(item.split("=", 1)[1].strip())
        else:
            predicate_true.append(item)
    return {
        "rule_id": str(rule_row["rule_id"]),
        "condition": str(rule_row["condition"]),
        "target_label": str(rule_row.get("target_label", "")),
        "predicate_true": predicate_true,
        "event_types": event_types,
    }


def current_rule_diagnostics(enriched: pd.DataFrame, rules: pd.DataFrame) -> list[dict[str, object]]:
    rows = []
    for _, rule_row in rules.iterrows():
        rule_def = rule_def_from_rule_row(rule_row)
        rule_id = str(rule_row["rule_id"])
        mask = matched_rule_mask(enriched, rule_def)
        all_returns = to_num(enriched.loc[mask, "forward_return_5d"]).dropna()
        discovery = to_num(enriched.loc[mask & period_mask(enriched, "discovery"), "forward_return_5d"]).dropna()
        oos = to_num(enriched.loc[mask & period_mask(enriched, "oos"), "forward_return_5d"]).dropna()
        support_count = int(rule_row["support_count"])
        status = str(rule_row["status"])
        reason = "通过 balanced 自动筛选阈值" if status == "qualified" else "保留为候选观察规则，未进入因子生成"
        if support_count < 5:
            reason = "discovery 支持度低于 5，仅可作为探索线索"
        elif status != "qualified":
            reason = "支持度够但胜率、平均收益、证据强度或股票覆盖未全部达标"
        rows.append(
            {
                "rule_id": rule_id,
                "condition": str(rule_row["condition"]),
                "terms": len(rule_def.get("predicate_true", [])) + len(rule_def.get("event_types", [])),
                "support": support_count,
                "win_rate": float(rule_row["win_rate"]),
                "avg_return": float(rule_row["avg_forward_return_5d"]),
                "score": float(rule_row["score"]),
                "status": status,
                "discovery_support": int(len(discovery)),
                "discovery_win": float((discovery > 0).mean()) if not discovery.empty else None,
                "discovery_avg": float(discovery.mean()) if not discovery.empty else None,
                "oos_support": int(len(oos)),
                "oos_win": float((oos > 0).mean()) if not oos.empty else None,
                "oos_avg": float(oos.mean()) if not oos.empty else None,
                "reason": reason,
            }
        )
    return rows


def candidate_combos(enriched: pd.DataFrame, bool_columns: list[str]) -> list[dict[str, object]]:
    active_columns = [column for column in bool_columns if is_true(enriched[column]).any()]
    rows = []
    for length in (1, 2, 3):
        for combo in itertools.combinations(active_columns, length):
            mask = pd.Series(True, index=enriched.index)
            for column in combo:
                mask &= is_true(enriched[column])
            returns = to_num(enriched.loc[mask, "forward_return_5d"]).dropna()
            if len(returns) < 3:
                continue
            discovery_returns = to_num(
                enriched.loc[mask & period_mask(enriched, "discovery"), "forward_return_5d"]
            ).dropna()
            oos_returns = to_num(enriched.loc[mask & period_mask(enriched, "oos"), "forward_return_5d"]).dropna()
            rows.append(
                {
                    "condition": " AND ".join(combo),
                    "terms": length,
                    "support": int(len(returns)),
                    "win_rate": float((returns > 0).mean()),
                    "avg_return": float(returns.mean()),
                    "stock_count": int(enriched.loc[mask, "stock_code"].nunique()),
                    "event_type_count": int(enriched.loc[mask, "event_type"].nunique()),
                    "event_types": top_counts(enriched.loc[mask, "event_type"], 3),
                    "avg_evidence": mean_or_none(enriched.loc[mask, "evidence_strength"]),
                    "discovery_support": int(len(discovery_returns)),
                    "discovery_win": float((discovery_returns > 0).mean()) if not discovery_returns.empty else None,
                    "discovery_avg": float(discovery_returns.mean()) if not discovery_returns.empty else None,
                    "oos_support": int(len(oos_returns)),
                    "oos_win": float((oos_returns > 0).mean()) if not oos_returns.empty else None,
                    "oos_avg": float(oos_returns.mean()) if not oos_returns.empty else None,
                }
            )
    return sorted(rows, key=lambda row: (row["win_rate"], row["avg_return"], row["support"]), reverse=True)


PARAMETER_PROFILES = [
    {
        "name": "conservative",
        "min_occurrences": 20,
        "min_win_rate": 0.55,
        "min_avg_return": 0.005,
        "min_avg_evidence": 0.75,
        "min_stock_count": 10,
        "max_terms": 2,
        "usage": "答辩主展示；宁可少，但要跨股票、有稳定解释。",
    },
    {
        "name": "balanced",
        "min_occurrences": 5,
        "min_win_rate": 0.50,
        "min_avg_return": 0.0,
        "min_avg_evidence": 0.75,
        "min_stock_count": 5,
        "max_terms": 3,
        "usage": "Demo 默认；保证规则数量和可解释性之间的平衡。",
    },
    {
        "name": "exploratory",
        "min_occurrences": 3,
        "min_win_rate": 0.45,
        "min_avg_return": -0.005,
        "min_avg_evidence": 0.70,
        "min_stock_count": 3,
        "max_terms": 3,
        "usage": "研究候选池；只展示为待验证线索，不进入正式结论。",
    },
]


def profile_counts(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for profile in PARAMETER_PROFILES:
        qualified = [
            row
            for row in candidates
            if row["discovery_support"] >= profile["min_occurrences"]
            and (row["discovery_win"] is not None and row["discovery_win"] >= profile["min_win_rate"])
            and (row["discovery_avg"] is not None and row["discovery_avg"] >= profile["min_avg_return"])
            and (row["avg_evidence"] is not None and row["avg_evidence"] >= profile["min_avg_evidence"])
            and row["stock_count"] >= profile["min_stock_count"]
            and row["terms"] <= profile["max_terms"]
        ]
        examples = "；".join(row["condition"] for row in qualified[:3]) or "-"
        rows.append({**profile, "candidate_count": len(qualified), "examples": examples})
    return rows


def factor_diagnostics(factors: pd.DataFrame, snapshot: pd.DataFrame, metrics: pd.DataFrame) -> dict[str, object]:
    rule_counts = factors["trigger_rule_ids"].value_counts().to_dict() if "trigger_rule_ids" in factors else {}
    snapshot_rule_counts = snapshot["trigger_rule_ids"].value_counts().to_dict() if "trigger_rule_ids" in snapshot else {}
    metric_map = {row["metric"]: row["value"] for _, row in metrics.iterrows()}
    return {
        "factor_rows": len(factors),
        "snapshot_rows": len(snapshot),
        "rule_counts": rule_counts,
        "snapshot_rule_counts": snapshot_rule_counts,
        "factor_value_min": mean_or_none(pd.Series([to_num(factors["factor_value"]).min()])) if not factors.empty else None,
        "factor_value_max": mean_or_none(pd.Series([to_num(factors["factor_value"]).max()])) if not factors.empty else None,
        "raw_score_min": mean_or_none(pd.Series([to_num(factors["raw_score"]).min()])) if not factors.empty else None,
        "raw_score_max": mean_or_none(pd.Series([to_num(factors["raw_score"]).max()])) if not factors.empty else None,
        "metrics": metric_map,
    }


def render_report() -> str:
    tables = build_enriched_tables()
    enriched = tables["enriched"]
    matrix = tables["matrix"]
    rules = tables["rules"]
    factors = tables["factors"]
    snapshot = tables["snapshot"]
    metrics = tables["metrics"]
    bool_columns = bool_predicate_columns(matrix)
    score_columns = [column for column in matrix.columns if column in SCORE_COLUMNS]

    pred_rows = predicate_statistics(enriched, bool_columns)
    score_rows = score_statistics(enriched, score_columns)
    redundancy = redundancy_rows(enriched, bool_columns)
    rule_rows = current_rule_diagnostics(enriched, rules)
    candidates = candidate_combos(enriched, bool_columns)
    profile_rows = profile_counts(candidates)
    factor_info = factor_diagnostics(factors, snapshot, metrics)

    all_returns = to_num(enriched["forward_return_5d"]).dropna()
    discovery_returns = to_num(enriched.loc[period_mask(enriched, "discovery"), "forward_return_5d"]).dropna()
    oos_returns = to_num(enriched.loc[period_mask(enriched, "oos"), "forward_return_5d"]).dropna()

    lines = [
        "# AlphaLens 谓词筛选与规则调参报告",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        f"> {DISCLAIMER}",
        "",
        "## 1. 数据概览",
        "",
        md_table(
            ["项目", "数值"],
            [
                ["事件数", len(tables["events"])],
                ["谓词判断行数", len(tables["predicates"])],
                ["谓词矩阵事件数", len(matrix)],
                ["有 forward_return_5d 的事件数", int(all_returns.count())],
                ["当前规则数 / 合格规则数", f"{len(rules)} / {(rules['status'] == 'qualified').sum()}"],
                ["事件级因子样本数", factor_info["factor_rows"]],
                ["全样本 5 日胜率 / 平均收益", f"{pct(float((all_returns > 0).mean()))} / {pct(float(all_returns.mean()), 2)}"],
                [
                    "discovery 2024-2025 胜率 / 平均收益",
                    f"{pct(float((discovery_returns > 0).mean()))} / {pct(float(discovery_returns.mean()), 2)}",
                ],
                [
                    "OOS 2026H1 胜率 / 平均收益",
                    f"{pct(float((oos_returns > 0).mean()))} / {pct(float(oos_returns.mean()), 2)}",
                ],
            ],
        ),
        "",
        "口径说明：本报告只读取 `data/sample/*.csv` 的现有结果，不覆盖真实输入数据，不改动 CSV 字段名。收益相关统计仅用于研究验证，不代表预测股价或投资建议。",
        "",
        "## 2. 谓词统计表",
        "",
        md_table(
            [
                "谓词",
                "触发次数",
                "触发率",
                "事件类型数",
                "股票数",
                "主要 event_type",
                "5D胜率",
                "5D均值",
                "相对全样本bp",
                "平均证据强度",
                "ret相关",
                "证据相关",
                "标记",
            ],
            [
                [
                    row["predicate"],
                    row["trigger_count"],
                    pct(row["trigger_rate"]),
                    row["event_type_count"],
                    row["stock_count"],
                    row["event_types"],
                    pct(row["win_rate"]),
                    pct(row["avg_return"], 2),
                    bps(row["lift"]),
                    num(row["avg_evidence"], 3),
                    num(row["corr_return"], 3),
                    num(row["corr_evidence"], 3),
                    row["flags"],
                ]
                for row in pred_rows
            ],
        ),
        "",
        "### score 型谓词",
        "",
        md_table(
            [
                "谓词",
                "样本",
                "唯一值",
                "均值",
                "标准差",
                "范围",
                "ret相关",
                "证据相关",
                "Top四分位胜率",
                "Top四分位均值",
                "建议",
            ],
            [
                [
                    row["predicate"],
                    row["count"],
                    row["unique"],
                    num(row["mean"], 3),
                    num(row["std"], 3),
                    f"{num(row['min'], 2)}~{num(row['max'], 2)}",
                    num(row["corr_return"], 3),
                    num(row["corr_evidence"], 3),
                    pct(row["top_quartile_win_rate"]),
                    pct(row["top_quartile_avg_return"], 2),
                    row["action"],
                ]
                for row in score_rows
            ],
        ),
        "",
        "## 3. 谓词保留/删除/合并建议",
        "",
        md_table(
            ["谓词", "建议", "原因与兼容方案"],
            [[row["predicate"], row["action"], row["note"]] for row in [*pred_rows, *score_rows]],
        ),
        "",
        "兼容方案：不修改 `predicates.csv` / `predicate_matrix.csv` 现有字段名；新增谓词以追加 `predicate_name` 枚举和追加矩阵列的方式进入，旧字段继续保留。若 Demo 暂不扩矩阵，可先在分析层生成派生列，例如 `source_government_or_exchange`，待 B/C 确认后再固化。",
        "",
        "## 4. 稀疏、密集、冗余与区分度诊断",
        "",
        "关键结论：",
        "",
        "- `investor_questions_increase` 与 `management_response_vague` 当前 0 触发，参与规则只会产生空规则。",
        "- `announcement_contains_uncertainty` 仅 3 次触发，方向偏负但样本不足，应先合并为更宽的风险披露谓词。",
        "- `policy_directly_related_to_business` 完全包含于多个更基础谓词，当前更像重复过滤条件，需要重写词表或拆分政策路径。",
        "- `event_evidence_strength` 与 `events.evidence_strength` 完全同源，适合做权重，不适合当独立解释谓词。",
        "",
        md_table(
            ["谓词A", "谓词B", "关系", "A触发", "B触发", "交集", "Jaccard", "相关"],
            [
                [
                    row["left"],
                    row["right"],
                    row["relation"],
                    row["left_count"],
                    row["right_count"],
                    row["intersection"],
                    num(row["jaccard"], 3),
                    num(row["corr"], 3),
                ]
                for row in redundancy[:12]
            ],
        ),
        "",
        "## 5. 当前自动规则有效性诊断",
        "",
        "当前 `src/backtest/demo_engine.py` 已从布尔谓词中自动枚举 2/3 阶组合，并使用 discovery split 的支持度、胜率、平均收益、证据强度和股票覆盖筛选合格规则。`rules.csv` 中 `status=qualified` 的规则才进入因子生成。",
        "",
        md_table(
            [
                "规则",
                "条件",
                "复杂度",
                "全样本支持",
                "胜率",
                "均值",
                "分数",
                "状态",
                "Discovery支持",
                "OOS支持",
                "诊断",
            ],
            [
                [
                    row["rule_id"],
                    row["condition"],
                    row["terms"],
                    row["support"],
                    pct(row["win_rate"]),
                    pct(row["avg_return"], 2),
                    num(row["score"], 4),
                    row["status"],
                    f"{row['discovery_support']} / {pct(row['discovery_win'])} / {pct(row['discovery_avg'], 2)}",
                    f"{row['oos_support']} / {pct(row['oos_win'])} / {pct(row['oos_avg'], 2)}",
                    row["reason"],
                ]
                for row in rule_rows
            ],
        ),
        "",
        "解释：规则有效性现在不再只看出现次数。支持度够但胜率、平均收益、证据强度或股票覆盖不足的组合会保留为候选观察规则，不参与当期因子生成。OOS 指标只用于验证展示，不参与规则评分。",
        "",
        "## 6. 三档调参建议",
        "",
        md_table(
            [
                "档位",
                "min_occurrences",
                "min_win_rate",
                "min_avg_return",
                "min_avg_evidence",
                "min_stock_count",
                "max_terms",
                "当前候选数",
                "示例",
                "使用场景",
            ],
            [
                [
                    row["name"],
                    row["min_occurrences"],
                    pct(row["min_win_rate"], 0),
                    pct(row["min_avg_return"], 2),
                    num(row["min_avg_evidence"], 2),
                    row["min_stock_count"],
                    row["max_terms"],
                    row["candidate_count"],
                    row["examples"],
                    row["usage"],
                ]
                for row in profile_rows
            ],
        ),
        "",
        "建议默认采用 balanced 档，但在规则表中明确标注 discovery 与 OOS 表现。conservative 档用于答辩主叙事，exploratory 档只作为“待验证规则候选”，避免为了回测曲线刷高阈值。",
        "",
        "### 候选组合 Top 12（全样本，仅供筛选参考）",
        "",
        md_table(
            [
                "条件",
                "支持",
                "胜率",
                "均值",
                "股票数",
                "事件类型",
                "Discovery",
                "OOS",
            ],
            [
                [
                    row["condition"],
                    row["support"],
                    pct(row["win_rate"]),
                    pct(row["avg_return"], 2),
                    row["stock_count"],
                    row["event_types"],
                    f"{row['discovery_support']} / {pct(row['discovery_win'])} / {pct(row['discovery_avg'], 2)}",
                    f"{row['oos_support']} / {pct(row['oos_win'])} / {pct(row['oos_avg'], 2)}",
                ]
                for row in candidates[:12]
            ],
        ),
        "",
        "## 7. 因子生成逻辑诊断",
        "",
        "当前路径：`rules.csv` 中 `status == qualified` 的规则进入事件级 `factors.csv`；每个事件匹配合格规则后，`raw_score = sum(rule.score)`，`factor_value = raw_score * (0.7 * event_evidence_strength + 0.3 * event_has_short_term_price_impact)`。`factor_snapshot.csv` 则把截至最后交易日的触发事件按 45 日半衰期衰减后累加，再在 `industry_sector` 内做 z-score。",
        "",
        md_table(
            ["项目", "当前值"],
            [
                ["factors.csv 行数", factor_info["factor_rows"]],
                ["事件级 trigger_rule_ids 分布", "；".join(f"{key}:{value}" for key, value in factor_info["rule_counts"].items())],
                ["factor_snapshot 行数", factor_info["snapshot_rows"]],
                ["snapshot trigger_rule_ids 分布", "；".join(f"{key or '无触发'}:{value}" for key, value in factor_info["snapshot_rule_counts"].items())],
                ["factor_value 范围", f"{num(factor_info['factor_value_min'], 6)} ~ {num(factor_info['factor_value_max'], 6)}"],
                ["raw_score 范围", f"{num(factor_info['raw_score_min'], 6)} ~ {num(factor_info['raw_score_max'], 6)}"],
                ["回测样本数", factor_info["metrics"].get("event_factor_sample_count", "-")],
                ["Rank IC 均值", factor_info["metrics"].get("avg_rank_ic_5d", "-")],
                ["未来函数审计", factor_info["metrics"].get("future_info_audit", "-")],
            ],
        ),
        "",
        "改进建议：",
        "",
        "- 将 `factors.csv` 拆成 feature 表和 evaluation label 表的概念；`forward_return_5d` 可以保留在研究输出中，但不能参与任何当期因子计算。",
        "- 规则分数必须只用 discovery split 估计，OOS 只做展示验证；否则 `rule.score` 会把 2026H1 的回报信息带回规则权重。",
        "- 因子名已由触发规则的 `target_label` 决定，例如 policy、attention、capacity、risk、authoritative_core_event 等规则族，不再所有事件共用一个因子名。",
        "- 对重复触发规则做去冗余：若 A 基本包含于 B，只保留更可解释的一条或降低重复加分，避免双触发事件被机械放大。",
        "- 对 `event_evidence_strength` 和 `event_has_short_term_price_impact` 做截尾或分档，减少规则分数被少数先验值主导，同时保留可解释性。",
        "",
        "## 8. 下一步代码改造清单",
        "",
        "1. 继续人工抽检新增谓词的 rationale，优先核验 `policy_attention_followup`、`risk_or_uncertainty_disclosure` 和来源拆分谓词。",
        "2. 继续重写 `policy_directly_related_to_business`：从简单核心产品关键词升级为产业链环节 + 政策路径映射，并保留 rationale。",
        "3. 在自动规则搜索中继续加入互斥过滤、规则去冗余和规则族上限，避免同质规则过多。",
        "4. 在不破坏现有 `rules.csv` 字段的前提下，后续可另写 `rule_candidates.csv` 存放 discovery/OOS 明细。",
        "5. 规则评分保持只在 2024-2025 discovery split 上估计，2026H1 OOS 只做验证展示。",
        "6. 因子生成继续保留 `trigger_event_ids` / `trigger_rule_ids` 审计链路，并对多规则触发做去冗余权重。",
        "7. Flask Demo 继续展示结果页，但新增“谓词筛选/规则调参”研究说明页时应明确不是投资建议，不声称预测股价。",
        "",
        f"> {DISCLAIMER}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
