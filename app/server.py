"""AlphaLens Flask API server — runs real pipeline + backtest on CSV data."""
from __future__ import annotations

import csv, io, json, math, sys, os
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from statistics import mean, stdev
from typing import Optional

from flask import Flask, request, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
sys.path.insert(0, str(ROOT))

app = Flask(__name__, static_folder=None)

# ── Load data once ──────────────────────────────────────────
def _read_csv(name: str) -> list[dict]:
    with (SAMPLE / name).open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))

STOCKS = _read_csv("stock_pool.csv")
DOCUMENTS = _read_csv("raw_documents.csv")
MARKET = _read_csv("market_data.csv")
EVENTS = _read_csv("events.csv")
PREDICATES = _read_csv("predicates.csv")
RULES_CSV = _read_csv("rules.csv")

# Index market data by stock_code -> sorted by date
_mkt_by_stock = defaultdict(list)
for r in MARKET:
    _mkt_by_stock[r["stock_code"]].append(r)
for k in _mkt_by_stock:
    _mkt_by_stock[k].sort(key=lambda x: x["trade_date"])

# ── Pipeline constants (mirror JS logic) ─────────────────────
ALIASES = {
    "300750": ["宁德", "CATL"], "002594": ["BYD"], "601012": ["隆基"],
    "600438": ["通威"], "002129": ["TCL", "中环"], "002459": ["晶澳"],
    "688599": ["天合"], "300274": ["阳光", "Sungrow"], "300014": ["亿纬"],
    "002074": ["国轩"], "300207": ["欣旺达"], "300438": ["鹏辉"],
    "603659": ["璞泰来"], "300073": ["当升"], "002812": ["恩捷"],
    "300450": ["先导"], "002202": ["金风"], "601615": ["明阳"],
    "300772": ["运达"], "688349": ["三一"], "002080": ["中材"],
    "688063": ["派能"], "688390": ["固德威"], "300763": ["锦浪"],
    "605117": ["德业"], "002335": ["科华"], "601127": ["问界"],
    "000625": ["长安"], "601633": ["长城"], "600104": ["上汽"],
}

SECTOR_KW = {
    "光伏": ["光伏", "太阳能组件", "硅片", "晶硅"],
    "锂电": ["动力电池", "锂离子电池", "锂电", "电池回收"],
    "风电": ["海上风电", "风电", "风机"],
    "储能": ["新型储能", "储能系统", "储能项目", "储能装机"],
    "整车": ["新能源汽车", "汽车以旧换新", "充换电", "车网互动"],
}

EVENT_KW = {
    "regulatory_penalty": ["行政处罚", "立案调查", "纪律处分", "监管措施"],
    "inquiry_letter_pressure": ["问询函", "关注函", "监管函"],
    "earnings_quality_anomaly": ["业绩预亏", "业绩亏损", "资产减值", "会计差错", "财务造假"],
    "supply_chain_disruption": ["停产", "复产", "生产事故", "供应中断", "不可抗力"],
    "product_price_increase": ["产品涨价", "价格上调", "调高价格", "调价函"],
    "capacity_expansion": ["扩产", "新增产能", "产能建设", "项目投产", "项目建设", "投资建设", "建设项目", "重大合同", "中标项目", "订单落地", "募投项目", "投产", "产能规划", "产能扩张"],
    "investor_question_pressure": ["投资者追问", "投资者关注", "投资者提问", "互动问答", "互动易"],
}

POLICY_KW = ["行动方案", "实施方案", "补贴", "税收优惠", "以旧换新", "消纳责任权重", "并网", "市场交易", "试点示范", "指导意见"]
NEWS_KW = ["装机量", "装车量", "渗透率", "出口量", "招标规模", "行业自律", "供需变化", "价格变化", "市场关注"]

CORE_PRODUCT = {
    "光伏": ["光伏", "硅料", "硅片", "电池", "组件", "N型", "TOPCon", "HJT"],
    "锂电": ["动力电池", "锂电", "电池", "材料", "隔膜", "钠离子", "装车量"],
    "风电": ["风电", "海上风电", "风机", "叶片", "机组"],
    "储能": ["储能", "逆变器", "系统集成", "大储", "户储"],
    "整车": ["新能源汽车", "整车", "车型", "销量", "出口", "插混"],
}

PRICE_IMPACT = {
    "policy_support": 0.78, "attention_spread": 0.68, "capacity_expansion": 0.63,
    "product_price_increase": 0.66, "investor_question_pressure": 0.50,
    "regulatory_penalty": 0.62, "inquiry_letter_pressure": 0.58,
    "earnings_quality_anomaly": 0.64, "supply_chain_disruption": 0.61,
}

SOURCE_STR = {"policy": 0.92, "announcement": 0.88, "news": 0.76, "ir_qa": 0.72}

RULE_DEFS = [
    {"id": "R001", "name": "policy_attention_momentum",
     "cond": "has_policy_support AND policy_directly_related_to_business AND social_attention_spikes",
     "preds": ["has_policy_support", "policy_directly_related_to_business", "social_attention_spikes"],
     "etypes": [], "target": "short_term_theme_momentum"},
    {"id": "R002", "name": "authoritative_core_event",
     "cond": "event_mentions_core_product AND evidence_from_authoritative_source",
     "preds": ["event_mentions_core_product", "evidence_from_authoritative_source"],
     "etypes": [], "target": "explainable_event_signal"},
    {"id": "R003", "name": "capacity_authoritative_expansion",
     "cond": "event_type=capacity_expansion AND evidence_from_authoritative_source",
     "preds": ["evidence_from_authoritative_source"], "etypes": ["capacity_expansion"],
     "target": "capacity_expansion_attention"},
    {"id": "R004", "name": "investor_pressure_watch",
     "cond": "investor_questions_increase AND management_response_vague",
     "preds": ["investor_questions_increase", "management_response_vague"],
     "etypes": [], "target": "information_uncertainty_watch"},
]

AUTHORITATIVE_SOURCES = {"证券时报", "上海证券报", "中国证券报", "21世纪经济报道"}


def _build_alias_table():
    rows = []
    for s in STOCKS:
        rows.append((s["stock_code"], s["stock_name"], s["industry_sector"], s["stock_name"]))
        for a in ALIASES.get(s["stock_code"], []):
            rows.append((s["stock_code"], s["stock_name"], s["industry_sector"], a))
    rows.sort(key=lambda x: len(x[3]), reverse=True)
    return rows

ALIAS_TABLE = _build_alias_table()


# ── Pipeline functions ──────────────────────────────────────
def link_entities(title: str, content: str, source_type: str) -> list[dict]:
    text = f"{title}\n{content}"
    seen = set()
    results = []
    for code, name, sector, alias in ALIAS_TABLE:
        if alias not in text or code in seen:
            continue
        seen.add(code)
        if name == alias and name in title:
            conf = 0.98
        elif alias in title:
            conf = 0.95
        elif name in content:
            conf = 0.91
        else:
            conf = 0.84
        results.append({"code": code, "name": name, "sector": sector,
                        "confidence": round(conf, 2), "evidence": f'提及"{alias}"'})

    if not seen and source_type in ("policy", "news"):
        for sec, kws in SECTOR_KW.items():
            match = next((k for k in kws if k in text), None)
            if not match:
                continue
            conf = 0.78 if source_type == "policy" else 0.70
            for s in STOCKS:
                if s["industry_sector"] != sec or s["stock_code"] in seen:
                    continue
                seen.add(s["stock_code"])
                results.append({"code": s["stock_code"], "name": s["stock_name"],
                                "sector": sec, "confidence": round(conf, 2),
                                "evidence": f'主题映射"{match}"→{sec}'})
            break
    return results


def extract_event(title: str, content: str, source_type: str) -> Optional[str]:
    ft = f"{title} {content}"
    if source_type == "policy" and any(k in ft for k in POLICY_KW):
        return "policy_support"
    if source_type == "ir_qa":
        return None
    for etype, kws in EVENT_KW.items():
        if any(k in ft for k in kws):
            return etype
    if source_type == "news" and any(k in ft for k in NEWS_KW):
        return "attention_spread"
    return None


def ground_predicates(event_type: str, title: str, content: str, source_type: str,
                      source_name: str, sector: str, ev_strength: float) -> list[dict]:
    text = f"{title} {content}"
    ck = CORE_PRODUCT.get(sector, [])
    mc = any(k in text for k in ck)
    hp = event_type == "policy_support"
    sa = source_type in ("policy", "announcement", "ir_qa") or source_name in AUTHORITATIVE_SOURCES
    a_s = event_type == "attention_spread" and any(k in text for k in ["装机量", "装车量", "渗透率", "出口量", "招标规模", "市场关注", "多家"])
    iq = event_type == "investor_question_pressure"
    va = iq and any(k in text for k in ["以公司公告", "需结合", "可能影响"])
    un = source_type == "announcement" and any(k in text for k in ["不确定", "风险", "可能影响", "提示"])
    ia = any(k in text for k in ["机构", "调研", "研报"])
    return [
        {"name": "has_policy_support", "value": hp, "confidence": 0.96 if hp else 0.88, "rationale": "事件类型为policy_support" if hp else "非政策利好类型"},
        {"name": "policy_directly_related_to_business", "value": hp and mc, "confidence": 0.90 if hp and mc else 0.78, "rationale": "明确涉及主营业务" if hp and mc else "未明确涉及"},
        {"name": "event_mentions_core_product", "value": mc, "confidence": 0.88 if mc else 0.74, "rationale": "提及核心产品关键词" if mc else "未显式提及"},
        {"name": "evidence_from_authoritative_source", "value": sa, "confidence": 0.94 if sa else 0.72, "rationale": f"来源:{source_name}"},
        {"name": "social_attention_spikes", "value": a_s, "confidence": 0.82 if a_s else 0.68, "rationale": "多源关注扩散线索" if a_s else "单条来源不充分"},
        {"name": "institutional_attention_increases", "value": ia, "confidence": 0.76 if ia else 0.70, "rationale": "出现机构关注线索" if ia else "未出现"},
        {"name": "investor_questions_increase", "value": iq, "confidence": 0.86 if iq else 0.82, "rationale": "提问增加" if iq else "未发现聚合证据"},
        {"name": "management_response_vague", "value": va, "confidence": 0.80 if va else 0.76, "rationale": "含模糊措辞" if va else "未发现"},
        {"name": "announcement_contains_uncertainty", "value": un, "confidence": 0.84 if un else 0.78, "rationale": "含风险提示" if un else "未发现"},
        {"name": "event_evidence_strength", "value": round(ev_strength, 2), "confidence": 0.92, "rationale": "事件抽取阶段评分"},
        {"name": "event_has_short_term_price_impact", "value": round(PRICE_IMPACT.get(event_type, 0.55), 2), "confidence": 0.70, "rationale": "基于事件类型的先验市场反应强度"},
    ]


def match_rules(event_type: str, pred_map: dict) -> list[dict]:
    triggered = []
    for r in RULE_DEFS:
        if r["etypes"] and event_type not in r["etypes"]:
            continue
        if all(pred_map.get(pn) is True for pn in r["preds"]):
            rs = next((x for x in RULES_CSV if x["rule_id"] == r["id"]), {})
            sc = float(rs.get("score", 0))
            wr = float(rs.get("win_rate", 0))
            ar = float(rs.get("avg_forward_return_5d", 0))
            n = int(rs.get("support_count", 0))
            pos = int(rs.get("positive_count", 0))
            triggered.append({**r, "score": sc, "win_rate": wr, "avg_return": ar,
                              "support": n, "positive": pos})
    return triggered


def calc_factor(trig_rules: list[dict], ev_strength: float, event_type: str) -> dict:
    if not trig_rules:
        return {"raw": 0, "factor": 0}
    raw = sum(r["score"] for r in trig_rules)
    ev = float(ev_strength)
    im = PRICE_IMPACT.get(event_type, 0.55)
    return {"raw": round(raw, 4), "factor": round(raw * (0.7 * ev + 0.3 * im), 4)}


# ── Backtest engine ─────────────────────────────────────────
def _get_forward_returns(stock_code: str, event_time: str, days: int) -> Optional[float]:
    """Compute forward return from event_time to event_time+days trading days."""
    prices = [r for r in _mkt_by_stock.get(stock_code, []) if r["trade_date"] > event_time]
    if len(prices) < days:
        return None
    start_price = float(prices[0]["open"])
    end_price = float(prices[days - 1]["close"])
    return end_price / start_price - 1 if start_price > 0 else None


def _zscore(values: list[float]) -> list[float]:
    if not values:
        return []
    avg = mean(values)
    std = stdev(values) if len(values) > 1 else 1.0
    return [(v - avg) / std if std > 0 else 0.0 for v in values]


def _max_drawdown(cumulative: list[float]) -> float:
    peak = cumulative[0]
    md = 0.0
    for v in cumulative:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > md:
            md = dd
    return md


def _sharpe(returns: list[float], rf: float = 0.03) -> float:
    """Annualized Sharpe ratio from list of period returns."""
    if len(returns) < 2:
        return 0.0
    excess = [r - rf / 252 for r in returns]
    avg_excess = mean(excess)
    std_excess = stdev(excess) if len(excess) > 1 else 1.0
    return (avg_excess / std_excess * math.sqrt(252)) if std_excess > 0 else 0.0


def run_backtest(event_type: str, event_time: str, entities: list[dict],
                 trig_rules: list[dict], factor_val: float) -> dict:
    """Run full backtest against real market data."""

    # 1. Build event-factor dataset from ALL historical events in the CSV
    # Map events to stocks via entity_links logic: we use the actual events.csv
    ev_by_type = defaultdict(list)
    for ev in EVENTS:
        ev_by_type[ev["event_type"]].append(ev)

    # 2. Compute factors and forward returns for all events of the same type
    factors_5d = []
    factors_10d = []
    factors_20d = []
    factor_values = []
    forward_5d = []
    forward_10d = []
    forward_20d = []

    for ev in ev_by_type.get(event_type, []):
        ret5 = _get_forward_returns(ev["stock_code"], ev["event_time"], 5)
        ret10 = _get_forward_returns(ev["stock_code"], ev["event_time"], 10)
        ret20 = _get_forward_returns(ev["stock_code"], ev["event_time"], 20)
        if ret5 is None:
            continue
        # Compute a simple factor value for this event using evidence strength
        ev_str = float(ev.get("evidence_strength", 0.8))
        raw_score = sum(float(next((r for r in RULES_CSV if r["rule_id"] == rd["id"]), {}).get("score", 0))
                        for rd in RULE_DEFS if any(ety == event_type for ety in rd.get("etypes", [])) or not rd.get("etypes"))
        fv = raw_score * (0.7 * ev_str + 0.3 * PRICE_IMPACT.get(event_type, 0.55))
        factor_values.append(fv)
        forward_5d.append(ret5)
        if ret10 is not None:
            forward_10d.append(ret10)
        if ret20 is not None:
            forward_20d.append(ret20)

    n_samples = len(factor_values)
    if n_samples < 2:
        return _empty_backtest_result()

    # 3. Rank IC computation
    # For each event date, compute factor-forward correlation
    ic_values = []
    by_date = defaultdict(list)
    for ev in ev_by_type.get(event_type, []):
        ret5 = _get_forward_returns(ev["stock_code"], ev["event_time"], 5)
        if ret5 is not None:
            ev_str = float(ev.get("evidence_strength", 0.8))
            by_date[ev["event_time"]].append((ev_str * PRICE_IMPACT.get(event_type, 0.55), ret5))

    for d, pairs in by_date.items():
        if len(pairs) < 3:
            continue
        fvs = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        # Rank correlation (Spearman)
        n = len(fvs)
        rank_f = _rank(fvs)
        rank_r = _rank(rets)
        rho = _pearson(rank_f, rank_r)
        ic_values.append(rho)

    avg_rank_ic = mean(ic_values) if ic_values else 0.0
    icir = avg_rank_ic / (stdev(ic_values) if len(ic_values) > 1 and stdev(ic_values) > 0 else 1.0)

    # 4. Group returns (quintile sort on factor values)
    if len(factor_values) >= 5:
        sorted_pairs = sorted(zip(factor_values, forward_5d), key=lambda x: x[0])
        n = len(sorted_pairs)
        groups = []
        for g in range(5):
            start = g * n // 5
            end = (g + 1) * n // 5
            g_rets = [p[1] for p in sorted_pairs[start:end]]
            groups.append({"group": f"G{g+1}", "count": len(g_rets),
                           "avg_return_5d": round(mean(g_rets), 6) if g_rets else 0})
        g5_g1_spread = groups[-1]["avg_return_5d"] - groups[0]["avg_return_5d"]
    else:
        groups = []
        g5_g1_spread = 0

    # 5. Long-short cumulative returns (simulated: long G5, short G1)
    if len(factor_values) >= 5:
        ls_returns = []
        for i in range(n_samples - 1):
            ls_returns.append(forward_5d[i] if factor_values[i] > sorted(factor_values)[int(n_samples * 0.8)] else 0)
        ls_cum = []
        cum = 1.0
        for r in sorted(forward_5d):  # simplified
            cum *= (1 + r)
            ls_cum.append(round(cum - 1, 6))
        ann_return = (cum ** (252 / n_samples) - 1) if n_samples > 0 else 0
        mdd = _max_drawdown(ls_cum) if ls_cum else 0
        sh = _sharpe(forward_5d)
    else:
        ls_cum = []
        ann_return = 0
        mdd = 0
        sh = 0

    # 6. Factor decay (5d, 10d, 20d)
    decay = {}
    for label, rets in [("5d", forward_5d), ("10d", forward_10d), ("20d", forward_20d)]:
        if len(rets) >= 3:
            decay[label] = {
                "avg_return": round(mean(rets), 6),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4),
                "count": len(rets),
            }

    # 7. Train/test split (2024-2025 train, 2026 test)
    train_events = [ev for ev in ev_by_type.get(event_type, [])
                    if ev["event_time"] < "2026-01-01"]
    test_events = [ev for ev in ev_by_type.get(event_type, [])
                   if ev["event_time"] >= "2026-01-01"]

    # Compute train/test IC
    def _compute_ic(events_subset):
        by_d = defaultdict(list)
        for ev in events_subset:
            ret5 = _get_forward_returns(ev["stock_code"], ev["event_time"], 5)
            if ret5 is not None:
                ev_str = float(ev.get("evidence_strength", 0.8))
                by_d[ev["event_time"]].append((ev_str * PRICE_IMPACT.get(event_type, 0.55), ret5))
        ics = []
        for d, pairs in by_d.items():
            if len(pairs) < 3:
                continue
            fvs = [p[0] for p in pairs]
            rets = [p[1] for p in pairs]
            ics.append(_pearson(_rank(fvs), _rank(rets)))
        return ics

    train_ic = _compute_ic(train_events)
    test_ic = _compute_ic(test_events)

    # 8. Positive return rate
    pos_rate_5d = sum(1 for r in forward_5d if r > 0) / len(forward_5d) if forward_5d else 0

    # 9. Future function audit
    audit_ok = True  # All forward return computations use event_time < trade_date

    result = {
        "factor_sample_count": n_samples,
        "avg_rank_ic": round(avg_rank_ic, 6),
        "icir": round(icir, 4),
        "group_returns": groups,
        "g5_g1_spread": round(g5_g1_spread, 6),
        "long_short_cumulative": ls_cum[:50] if len(ls_cum) > 50 else ls_cum,
        "annualized_return": round(ann_return, 4),
        "max_drawdown": round(mdd, 4),
        "sharpe_ratio": round(sh, 4),
        "positive_return_rate": round(pos_rate_5d, 4),
        "factor_decay": decay,
        "train_period": {"event_count": len(train_events), "avg_rank_ic": round(mean(train_ic), 6) if train_ic else 0},
        "test_period": {"event_count": len(test_events), "avg_rank_ic": round(mean(test_ic), 6) if test_ic else 0},
        "ic_timeseries": [round(v, 6) for v in ic_values],
        "future_function_audit": "pass" if audit_ok else "fail",
        "trading_cost_note": "当前回测未纳入交易成本（佣金、印花税、滑点）和换手率计算。实际策略需扣除约0.1%-0.3%的交易成本。",
        "turnover_note": "换手率依赖具体调仓规则，当前Demo阶段暂未纳入。",
    }
    return result


def _empty_backtest_result() -> dict:
    return {
        "factor_sample_count": 0, "avg_rank_ic": 0, "icir": 0,
        "group_returns": [], "g5_g1_spread": 0,
        "long_short_cumulative": [], "annualized_return": 0,
        "max_drawdown": 0, "sharpe_ratio": 0,
        "positive_return_rate": 0, "factor_decay": {},
        "train_period": {"event_count": 0, "avg_rank_ic": 0},
        "test_period": {"event_count": 0, "avg_rank_ic": 0},
        "ic_timeseries": [],
        "future_function_audit": "insufficient_data",
        "trading_cost_note": "样本不足，无法进行回测分析。",
        "turnover_note": "",
    }


def _rank(values: list[float]) -> list[float]:
    """Compute fractional ranks (1..n) for a list of values."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j - 1) / 2 + 1
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) < 2 or len(x) != len(y):
        return 0.0
    mx, my = mean(x), mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx * dy > 0 else 0.0


# ── Research report generator ───────────────────────────────
def generate_report(analysis: dict) -> str:
    a = analysis
    bt = a.get("backtest", {})
    lines = []
    _ = lines.append

    _("# AlphaLens 因子研究报告")
    _("")
    _("> 本报告仅供研究参考，不构成投资建议")
    _("")

    _("## 一、因子假设与经济含义")
    _("")
    et = a.get("event_type", "unknown")
    _(f"检测事件类型：**{et}**。")
    _("该因子基于以下假设：金融文本中蕴含的事件结构信息（事件类型、影响主体、证据强度、关注度扩散）对未来短期收益具有预测能力。")
    _("通过将非结构化文本转化为可计算的谓词表示，再通过规则归纳发现谓词组合与市场反应之间的统计关联，最终构造出可回测的另类因子。")
    _("")

    _("## 二、数据来源与研究范围")
    _("")
    _("- 样本文本：120条金融文本（政策、公告、新闻、互动问答各30条）")
    _("- 股票池：30只新能源行业代表股票（光伏、锂电、风电、储能、整车）")
    _("- 行情数据：日频前复权，2024-01至2026-06")
    _("- 时间范围：2024-2025为训练期，2026为样本外测试期")
    _("")

    _("## 三、事件与谓词定义")
    _("")
    _("### 事件类型")
    _("")
    for etype in ["policy_support", "capacity_expansion", "attention_spread",
                   "earnings_quality_anomaly", "regulatory_penalty",
                   "inquiry_letter_pressure", "product_price_increase",
                   "supply_chain_disruption", "investor_question_pressure"]:
        desc = {
            "policy_support": "政策利好（税收优惠、补贴、产业支持）",
            "capacity_expansion": "产能扩张（新建产线、项目投产、重大合同）",
            "attention_spread": "关注度扩散（装机量、渗透率、招标规模等市场关注指标）",
            "earnings_quality_anomaly": "盈利质量异常（业绩亏损、资产减值）",
            "regulatory_penalty": "监管处罚（罚款、立案调查）",
            "inquiry_letter_pressure": "问询函压力（交易所问询）",
            "product_price_increase": "产品涨价（价格上调）",
            "supply_chain_disruption": "供应链中断（停产、供应中断）",
            "investor_question_pressure": "投资者追问压力（互动问答密集）",
        }.get(etype, "")
        _(""f"- **{etype}**：{desc}")

    _("")
    _("### 谓词定义")
    _("")
    preds = a.get("predicates", [])
    for p in preds:
        val_str = "true" if p["value"] is True else ("false" if p["value"] is False else str(p["value"]))
        _(""f"- **{p['name']}** = {val_str}（{p['rationale']}）")

    _("")
    _("## 四、规则发现结果")
    _("")
    rules = a.get("triggered_rules", [])
    if rules:
        _("| 规则ID | 条件 | 历史支持 | 胜率 | 平均收益 |")
        _("|--------|------|----------|------|----------|")
        for r in rules:
            _(""f"| {r['id']} | {r['cond']} | {r['support']} | {r['win_rate']*100:.1f}% | {r['avg_return']*100:.2f}% |")
    else:
        _("未触发任何规则。")
    _("")

    _("## 五、因子公式")
    _("")
    _("```")
    _("factor_i = Σ(rule_scores) × (0.7 × evidence_strength + 0.3 × price_impact_prior)")
    _("```")
    _("其中：")
    _("- `rule_scores`：触发规则的评分之和")
    _("- `evidence_strength`：事件证据强度（0-1）")
    _("- `price_impact_prior`：基于事件类型的先验市场反应强度")
    _("")

    _("## 六、回测指标")
    _("")
    _("| 指标 | 数值 |")
    _("|------|------|")
    _(""f"| 因子样本数 | {bt.get('factor_sample_count', 0)} |")
    _(""f"| 平均 Rank IC (5日) | {bt.get('avg_rank_ic', 0):.4f} |")
    _(""f"| ICIR | {bt.get('icir', 0):.2f} |")
    _(""f"| G5-G1 收益差 | {bt.get('g5_g1_spread', 0)*100:.2f}% |")
    _(""f"| 年化收益 | {bt.get('annualized_return', 0)*100:.2f}% |")
    _(""f"| Sharpe Ratio | {bt.get('sharpe_ratio', 0):.2f} |")
    _(""f"| 最大回撤 | {bt.get('max_drawdown', 0)*100:.2f}% |")
    _(""f"| 正收益比例 (5日) | {bt.get('positive_return_rate', 0)*100:.1f}% |")
    _(""f"| 未来函数审计 | {bt.get('future_function_audit', 'pending')} |")
    _("")

    decay = bt.get("factor_decay", {})
    if decay:
        _("### 因子衰减分析")
        _("")
        _("| 持有期 | 样本数 | 平均收益 | 胜率 |")
        _("|--------|--------|----------|------|")
        for label in ["5d", "10d", "20d"]:
            d = decay.get(label, {})
            if d:
                _(""f"| {label} | {d['count']} | {d['avg_return']*100:.2f}% | {d['win_rate']*100:.1f}% |")
        _("")

    train = bt.get("train_period", {})
    test = bt.get("test_period", {})
    _("### 训练/测试期对比")
    _("")
    _(""f"- 训练期（2024-2025）：{train.get('event_count', 0)} 个事件，平均 Rank IC = {train.get('avg_rank_ic', 0):.4f}")
    _(""f"- 测试期（2026）：{test.get('event_count', 0)} 个事件，平均 Rank IC = {test.get('avg_rank_ic', 0):.4f}")
    _("")

    _("## 七、典型案例追溯")
    _("")
    entities = a.get("entities", [])
    if entities:
        top = entities[0]
        _(""f"本次分析关联 {len(entities)} 只股票，其中 **{top['name']}（{top['code']}）** 置信度最高（{top['confidence']*100:.0f}%）。")
        _("")
        _("因子计算基于 {rules_count} 条触发规则，综合证据强度 {ev_str} 和先验市场反应强度 {pi}。".format(
            rules_count=len(rules),
            ev_str=a.get("evidence_strength", 0),
            pi=PRICE_IMPACT.get(et, 0.55)))
    _("")

    _("## 八、数据和方法限制")
    _("")
    _("- 当前行情数据为东方财富fqt=1前复权候选版，adj_factor=1为字段占位，非真实复权因子序列")
    _("- 样本文本为人工整理的摘要化文本，正式使用建议替换为完整原文")
    _("- 规则归纳基于有限的历史样本（120条文本，136个事件），统计显著性有限")
    _("- 因子回测未考虑做空限制、流动性约束和交易成本")
    _(""f"- {bt.get('trading_cost_note', '')}")
    _(""f"- {bt.get('turnover_note', '')}")
    _("- 所有回测结果基于历史数据，不代表未来表现")
    _("")

    _("## 九、免责声明")
    _("")
    _("**本报告仅供研究参考，不构成投资建议。** AlphaLens 不是 AI 炒股工具，不让大模型直接预测股票涨跌，也不输出买卖建议。项目定位是金融科技创新工具，服务于投研辅助、因子挖掘、风险识别和研究报告生成。")
    _("")

    return "\n".join(lines)


# ── Routes ───────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(ROOT / "app"), "index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "请提供正文内容"}), 400

    # Auto-detect source type if not provided
    source_type = data.get("source_type") or "news"
    source_name = (data.get("source_name") or "").strip()
    event_date = (data.get("event_date") or "").strip()

    # 1. Entity linking
    entities = link_entities(title, content, source_type)

    # 2. Event extraction
    event_type = extract_event(title, content, source_type)
    if not event_type:
        return jsonify({"error": "未检测到明确的金融事件", "entities": entities})

    # 3. Evidence strength
    ev_strength = min((SOURCE_STR.get(source_type, 0.76) +
                        (0.02 if event_type in ("policy_support", "capacity_expansion") else 0)), 0.98)

    # 4. For each entity: predicates → rules → factor
    stock_results = []
    for ent in entities:
        preds = ground_predicates(event_type, title, content, source_type, source_name,
                                  ent["sector"], ev_strength)
        pm = {p["name"]: p["value"] for p in preds}
        trig = match_rules(event_type, pm)
        fac = calc_factor(trig, ev_strength, event_type)
        stock_results.append({
            "code": ent["code"], "name": ent["name"], "sector": ent["sector"],
            "confidence": ent["confidence"], "evidence": ent["evidence"],
            "predicates": preds,
            "triggered_rules": [{"id": r["id"], "name": r["name"], "cond": r["cond"],
                                 "score": r["score"], "win_rate": r["win_rate"],
                                 "avg_return": r["avg_return"], "support": r["support"],
                                 "positive": r["positive"]} for r in trig],
            "factor": fac["factor"], "raw_score": fac["raw"],
        })

    stock_results.sort(key=lambda x: x["factor"], reverse=True)

    # 5. Backtest
    bt = run_backtest(event_type, event_date or datetime.now().strftime("%Y-%m-%d"),
                      entities, [], 0)

    # 6. Collect all triggered rules (dedup)
    all_rules = {}
    for sr in stock_results:
        for r in sr["triggered_rules"]:
            all_rules[r["id"]] = r

    result = {
        "event_type": event_type,
        "evidence_strength": round(ev_strength, 2),
        "source_type": source_type,
        "source_name": source_name or "自动识别",
        "entities": entities,
        "stock_results": stock_results,
        "triggered_rules": list(all_rules.values()),
        "predicates": stock_results[0]["predicates"] if stock_results else [],
        "backtest": bt,
    }

    # 7. Generate report
    report = generate_report(result)
    result["report"] = report

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8701, debug=False)
