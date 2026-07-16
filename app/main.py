from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
REPORT_PATH = ROOT / "查看材料" / "因子研究报告.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"


st.set_page_config(page_title="AlphaLens", layout="wide")


@st.cache_data
def load_csv(filename: str) -> pd.DataFrame:
    path = SAMPLE_DIR / filename
    return pd.read_csv(
        path,
        dtype={
            "stock_code": str,
            "doc_id": str,
            "event_id": str,
            "rule_id": str,
        },
        encoding="utf-8",
    )


def metric_row(items: list[tuple[str, str]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.metric(label, value)


def page_overview() -> None:
    st.title("AlphaLens")
    st.caption("基于大模型规则归纳的舆情另类因子挖掘与量化研究智能体")

    stocks = load_csv("stock_pool.csv")
    docs = load_csv("raw_documents.csv")
    events = load_csv("events.csv")
    rules = load_csv("rules.csv")
    metrics = load_csv("backtest_metrics.csv")
    metric_map = dict(zip(metrics["metric"], metrics["value"]))

    metric_row(
        [
            ("股票池", f"{len(stocks)}"),
            ("样本文本", f"{len(docs)}"),
            ("结构化事件", f"{len(events)}"),
            ("合格规则", f"{(rules['status'] == 'qualified').sum()}"),
            ("未来函数审计", metric_map.get("future_info_audit", "pending")),
        ]
    )

    st.graphviz_chart(
        """
        digraph {
          rankdir=LR;
          node [shape=box, style="rounded", fontname="Arial"];
          text [label="金融文本"];
          entity [label="实体链接"];
          event [label="事件抽取"];
          predicate [label="谓词矩阵"];
          rule [label="规则归纳"];
          factor [label="因子生成"];
          backtest [label="回测审计"];
          report [label="研究报告"];
          text -> entity -> event -> predicate -> rule -> factor -> backtest -> report;
        }
        """
    )

    st.subheader("当前自动化状态")
    st.dataframe(
        pd.DataFrame(
            [
                {"环节": "B 线数据", "状态": "已生成样例并通过字段校验"},
                {"环节": "事件/谓词", "状态": "已离线规则化生成，待人工核验正式样本"},
                {"环节": "规则/因子", "状态": "已生成 Demo 输出，待真实行情复核"},
                {"环节": "报告", "状态": "已生成 Markdown 初稿"},
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def page_input_data() -> None:
    st.title("输入数据")
    stock_pool = load_csv("stock_pool.csv")
    documents = load_csv("raw_documents.csv")
    source_counts = documents["source_type"].value_counts().reset_index()
    source_counts.columns = ["source_type", "count"]

    left, right = st.columns([2, 1])
    with left:
        st.subheader("股票池")
        st.dataframe(stock_pool, use_container_width=True, hide_index=True)
    with right:
        st.subheader("文本来源")
        fig = px.bar(source_counts, x="source_type", y="count", text="count")
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("样本文本")
    st.dataframe(
        documents[["doc_id", "source_type", "title", "publish_time", "source_name"]],
        use_container_width=True,
        hide_index=True,
    )


def page_event_extraction() -> None:
    st.title("事件抽取")
    events = load_csv("events.csv")
    links = load_csv("entity_links.csv")
    event_counts = events["event_type"].value_counts().reset_index()
    event_counts.columns = ["event_type", "count"]

    metric_row(
        [
            ("实体链接", f"{len(links)}"),
            ("事件数", f"{len(events)}"),
            ("平均证据强度", f"{events['evidence_strength'].astype(float).mean():.2f}"),
        ]
    )
    fig = px.bar(event_counts, x="event_type", y="count", text="count")
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=20, b=0))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(events, use_container_width=True, hide_index=True)


def page_predicates_rules() -> None:
    st.title("谓词与规则")
    matrix = load_csv("predicate_matrix.csv")
    rules = load_csv("rules.csv")

    st.subheader("谓词矩阵")
    st.dataframe(matrix.head(80), use_container_width=True, hide_index=True)
    st.subheader("候选规则")
    st.dataframe(rules, use_container_width=True, hide_index=True)


def page_factor_ranking() -> None:
    st.title("因子排名")
    snapshot = load_csv("factor_snapshot.csv")
    snapshot["factor_value"] = snapshot["factor_value"].astype(float)
    snapshot["raw_score"] = snapshot["raw_score"].astype(float)

    st.dataframe(snapshot, use_container_width=True, hide_index=True)
    fig = px.bar(
        snapshot.head(15).sort_values("factor_value"),
        x="factor_value",
        y="stock_name",
        color="industry_sector",
        orientation="h",
        hover_data=["stock_code", "trigger_rule_ids"],
    )
    fig.update_layout(height=520, margin=dict(l=0, r=0, t=20, b=0))
    st.plotly_chart(fig, use_container_width=True)


def page_backtest() -> None:
    st.title("回测看板")
    metrics = load_csv("backtest_metrics.csv")
    metric_map = dict(zip(metrics["metric"], metrics["value"]))
    groups = load_csv("group_returns.csv")
    groups["avg_forward_return_5d"] = groups["avg_forward_return_5d"].astype(float)
    rank_ic = load_csv("rank_ic_timeseries.csv")

    metric_row(
        [
            ("事件样本", metric_map.get("event_factor_sample_count", "0")),
            ("平均 Rank IC", f"{float(metric_map.get('avg_rank_ic_5d', 0)):.4f}"),
            ("G5-G1", f"{float(metric_map.get('top_bottom_group_spread_5d', 0)) * 100:.2f}%"),
            ("正收益比例", f"{float(metric_map.get('positive_forward_return_rate_5d', 0)) * 100:.2f}%"),
            ("未来函数审计", metric_map.get("future_info_audit", "pending")),
        ]
    )

    left, right = st.columns(2)
    with left:
        fig = px.bar(groups, x="group", y="avg_forward_return_5d", text="avg_forward_return_5d")
        fig.update_traces(texttemplate="%{text:.2%}")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=20, b=0), yaxis_tickformat=".1%")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        if len(rank_ic) > 0:
            rank_ic["rank_ic_5d"] = rank_ic["rank_ic_5d"].astype(float)
            fig = px.line(rank_ic, x="trade_date", y="rank_ic_5d", markers=True)
            fig.update_layout(height=360, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("当前 Demo 样本尚不足以形成逐日 Rank IC 序列。")

    st.dataframe(metrics, use_container_width=True, hide_index=True)


def page_report() -> None:
    st.title("研究报告")
    if REPORT_PATH.exists():
        st.markdown(REPORT_PATH.read_text(encoding="utf-8"))
    else:
        st.warning("研究报告尚未生成。")


PAGES = {
    "Pipeline Overview": page_overview,
    "Input Data": page_input_data,
    "Event Extraction": page_event_extraction,
    "Predicates & Rules": page_predicates_rules,
    "Factor Ranking": page_factor_ranking,
    "Backtest Dashboard": page_backtest,
    "Research Report": page_report,
}


with st.sidebar:
    st.title("AlphaLens")
    selected_page = st.radio("页面", list(PAGES.keys()), label_visibility="collapsed")
    st.divider()
    st.caption(DISCLAIMER)

PAGES[selected_page]()
