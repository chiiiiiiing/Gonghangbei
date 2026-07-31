"""Prepare B-role handoff materials that do not require human judgment."""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_b_data import SCHEMAS as CORE_SCHEMAS
from scripts.validate_research_outputs import SCHEMAS as RESEARCH_SCHEMAS

SAMPLE_DIR = ROOT / "data" / "sample"
VIEW_DIR = ROOT / "查看材料"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"


def today() -> str:
    return date.today().isoformat()


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_header(filename: str) -> list[str]:
    with (SAMPLE_DIR / filename).open("r", newline="", encoding="utf-8") as f:
        return csv.DictReader(f).fieldnames or []


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_text(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def read_view_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return [
            {key: value for key, value in row.items() if key is not None}
            for row in csv.DictReader(f)
        ]


def load_manual_review_notes(path: Path, key_fields: list[str]) -> dict[tuple[str, ...], dict[str, str]]:
    notes: dict[tuple[str, ...], dict[str, str]] = {}
    for row in read_view_csv(path):
        key = tuple(row.get(field, "") for field in key_fields)
        notes[key] = {
            "manual_review_result": row.get("manual_review_result", ""),
            "manual_comment": row.get("manual_comment", ""),
        }
    return notes


def apply_manual_review_note(
    row: dict[str, str],
    notes: dict[tuple[str, ...], dict[str, str]],
    key_fields: list[str],
) -> dict[str, str]:
    key = tuple(row.get(field, "") for field in key_fields)
    note = notes.get(key)
    if note:
        row["manual_review_result"] = note["manual_review_result"]
        row["manual_comment"] = note["manual_comment"]
    return row


def deterministic_sample(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    if count <= 0:
        return []
    if len(rows) <= count:
        return rows
    step = max(len(rows) // count, 1)
    selected = [rows[index] for index in range(0, len(rows), step)]
    return selected[:count]


def prepare_manual_review_samples() -> None:
    docs = {row["doc_id"]: row for row in read_csv("raw_documents.csv")}
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    event_sample_path = VIEW_DIR / "事件人工抽检样本.csv"
    predicate_sample_path = VIEW_DIR / "谓词人工抽检样本.csv"
    existing_event_rows = read_view_csv(event_sample_path)
    event_notes = load_manual_review_notes(event_sample_path, ["review_item"])
    predicate_notes = load_manual_review_notes(predicate_sample_path, ["event_id", "predicate_name"])

    event_sample_rows = [row for row in existing_event_rows if row.get("manual_review_result", "").strip()]
    reviewed_ids = {row.get("review_item", "") for row in event_sample_rows}
    remaining_slots = max(10 - len(event_sample_rows), 0)
    event_candidates = [event for event in events if event["event_id"] not in reviewed_ids]
    for event in deterministic_sample(event_candidates, remaining_slots):
        doc = docs[event["doc_id"]]
        row = {
            "review_item": event["event_id"],
            "doc_id": event["doc_id"],
            "source_type": doc["source_type"],
            "title": doc["title"],
            "stock_code": event["stock_code"],
            "event_type": event["event_type"],
            "event_time": event["event_time"],
            "evidence_strength": event["evidence_strength"],
            "evidence_text": event["evidence_text"],
            "manual_review_result": "",
            "manual_comment": "",
        }
        event_sample_rows.append(apply_manual_review_note(row, event_notes, ["review_item"]))
    write_csv(
        event_sample_path,
        [
            "review_item",
            "doc_id",
            "source_type",
            "title",
            "stock_code",
            "event_type",
            "event_time",
            "evidence_strength",
            "evidence_text",
            "manual_review_result",
            "manual_comment",
        ],
        event_sample_rows,
    )

    predicate_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in predicates:
        predicate_by_event[row["event_id"]].append(row)

    predicate_sample_rows = []
    for event in deterministic_sample(events, 10):
        for pred in predicate_by_event[event["event_id"]]:
            if pred["predicate_name"] in {
                "has_policy_support",
                "policy_directly_related_to_business",
                "evidence_from_authoritative_source",
                "social_attention_spikes",
                "event_evidence_strength",
                "event_has_short_term_price_impact",
            }:
                row = {
                    "event_id": event["event_id"],
                    "doc_id": event["doc_id"],
                    "stock_code": event["stock_code"],
                    "event_type": event["event_type"],
                    "predicate_name": pred["predicate_name"],
                    "value": pred["value"],
                    "confidence": pred["confidence"],
                    "rationale": pred["rationale"],
                    "manual_review_result": "",
                    "manual_comment": "",
                }
                predicate_sample_rows.append(
                    apply_manual_review_note(row, predicate_notes, ["event_id", "predicate_name"])
                )
    write_csv(
        predicate_sample_path,
        [
            "event_id",
            "doc_id",
            "stock_code",
            "event_type",
            "predicate_name",
            "value",
            "confidence",
            "rationale",
            "manual_review_result",
            "manual_comment",
        ],
        predicate_sample_rows,
    )


def prepare_event_review_resolution() -> None:
    review_rows = read_view_csv(VIEW_DIR / "事件人工抽检样本.csv")
    reviewed_rows = [row for row in review_rows if row.get("manual_review_result", "").strip()]
    result_counts = Counter(row["manual_review_result"] for row in reviewed_rows)
    lines = [
        "# AlphaLens 事件抽检问题处理记录",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 人工抽检结论",
        "",
        f"- 已审事件：{len(reviewed_rows)} 条",
        f"- pass：{result_counts['pass']} 条",
        f"- revise：{result_counts['revise']} 条",
        f"- drop：{result_counts['drop']} 条",
        f"- drop 结论已完成规则处理：{result_counts['drop']} 条",
        "",
        "## 发现的系统性问题",
        "",
        "1. 公告抽取曾把 AlphaLens 人工补写的“项目关联”说明当作原文证据，导致评级、分红法律意见、保荐代表人变更等例行文件被误判为产能扩张。",
        "2. 互动问答曾把任意一条问题直接标记为 investor_question_pressure，但单条问答无法证明提问数量或压力增加。",
        "",
        "## 已实施修复",
        "",
        "- 事件与谓词只读取“项目关联：”之前的原文摘要，不再使用系统生成的项目关联说明作为事实证据。",
        "- 公告只有明确出现扩产、投产、重大合同、处罚、问询、停复产等事件事实时才生成相应事件。",
        "- 单条互动问答保留在证据文本库，但不生成 investor_question_pressure；未来只有加入时间窗聚合统计后才允许判断提问增加。",
        "- 谓词不再使用系统生成的 event.object 自动命中核心产品；政策直接相关性必须由原文证据支持。",
        "- 原始 10 条人工结论继续保留在 `查看材料/事件人工抽检样本.csv`，作为问题发现和修复审计记录。",
        "",
        "## 结论",
        "",
        "本轮 10 条 drop 已按共性根因处理并从当前事件结果中排除。后续新增事件类型或改动抽取规则时，应重新抽样验证，不能把本轮结论外推为永久准确率。",
        "",
    ]
    write_text(VIEW_DIR / "事件抽检问题处理记录.md", lines)


def prepare_market_data_import_template() -> None:
    rows = [
        {
            "trade_date": "2024-01-02",
            "stock_code": "300750",
            "open": "150.00",
            "high": "155.00",
            "low": "148.00",
            "close": "153.00",
            "volume": "50000000",
            "adj_factor": "1.000000",
        },
        {
            "trade_date": "2024-01-03",
            "stock_code": "300750",
            "open": "153.00",
            "high": "158.00",
            "low": "151.50",
            "close": "156.80",
            "volume": "48000000",
            "adj_factor": "1.000000",
        },
    ]
    write_csv(
        VIEW_DIR / "真实行情导入模板.csv",
        ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"],
        rows,
    )


def prepare_future_info_audit() -> None:
    forward_rows = read_csv("event_forward_returns.csv")
    event_rows = read_csv("events.csv")
    failed = [row for row in forward_rows if row["future_info_ok"] != "true"]
    late_entry = [
        row
        for row in forward_rows
        if row["future_info_ok"] == "true" and row["entry_trade_date"] <= row["event_time"]
    ]
    lines = [
        "# AlphaLens 未来函数审计明细",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 审计规则",
        "",
        "- 入场日必须严格晚于 `event_time`。",
        "- forward return 使用事件发生后的第 1 个交易日开盘至第 N 个交易日收盘。",
        "- 当前行情使用 `data/sample/market_data.csv`；事件若缺少完整未来收益窗口，不进入收益、规则和因子计算。",
        "",
        "## 结果",
        "",
        f"- 事件总数：{len(event_rows)}",
        f"- 可计算未来收益事件数：{len(forward_rows)}",
        f"- 因未来窗口不足排除事件数：{max(len(event_rows) - len(forward_rows), 0)}",
        f"- `future_info_ok != true`：{len(failed)}",
        f"- 入场日不晚于事件日：{len(late_entry)}",
        f"- 审计结论：{'pass' if not failed and not late_entry else 'fail'}",
        "",
    ]
    write_text(VIEW_DIR / "未来函数审计明细.md", lines)


def prepare_ppt_case_pack() -> None:
    docs = {row["doc_id"]: row for row in read_csv("raw_documents.csv")}
    stocks = {row["stock_code"]: row for row in read_csv("stock_pool.csv")}
    events = read_csv("events.csv")
    returns = {row["event_id"]: row for row in read_csv("event_forward_returns.csv")}
    predicates = defaultdict(dict)
    for row in read_csv("predicates.csv"):
        predicates[row["event_id"]][row["predicate_name"]] = row["value"]

    candidates = []
    for event in events:
        fwd = returns.get(event["event_id"], {})
        if not fwd or not fwd.get("forward_return_5d"):
            continue
        doc = docs[event["doc_id"]]
        stock = stocks[event["stock_code"]]
        ret = float(fwd["forward_return_5d"])
        candidates.append((abs(ret), ret, event, doc, stock, fwd))
    candidates.sort(reverse=True, key=lambda item: item[0])
    selected = candidates[:8]

    lines = [
        "# AlphaLens PPT 案例素材包",
        "",
        f"生成日期：{today()}",
        "",
        "说明：以下案例收益路径基于当前 `data/sample/market_data.csv`，用于 PPT 链路说明；正式答辩前仍需人工复核行情复权口径和案例叙事。",
        "",
        DISCLAIMER,
        "",
    ]
    for idx, (_abs_ret, ret, event, doc, stock, fwd) in enumerate(selected, start=1):
        pred = predicates[event["event_id"]]
        lines.extend(
            [
                f"## 案例 {idx}: {stock['stock_name']} / {event['event_type']}",
                "",
                f"- 文档：{doc['doc_id']} {doc['title']}",
                f"- 来源：{doc['source_type']} / {doc['source_name']} / {doc['publish_time']}",
                f"- 股票：{event['stock_code']} {stock['stock_name']}（{stock['industry_sector']}）",
                f"- 事件时间：{event['event_time']}",
                f"- 影响路径：{event['impact_path']}",
                f"- 证据：{event['evidence_text']}",
                f"- 谓词：policy={pred.get('has_policy_support')}，business={pred.get('policy_directly_related_to_business')}，source={pred.get('evidence_from_authoritative_source')}，attention={pred.get('social_attention_spikes')}",
                f"- 5 日收益：{ret * 100:.2f}%（{fwd['entry_trade_date']} 至 {fwd['exit_trade_date_5d']}）",
                "- PPT 讲法：文本先转成事件，事件再落成谓词，最后作为可解释规则和因子样本进入回测审计。",
                "",
            ]
        )
    write_text(VIEW_DIR / "PPT案例素材包.md", lines)


def prepare_case_index() -> None:
    docs = {row["doc_id"]: row for row in read_csv("raw_documents.csv")}
    stocks = {row["stock_code"]: row for row in read_csv("stock_pool.csv")}
    returns = {row["event_id"]: row for row in read_csv("event_forward_returns.csv")}
    predicates: dict[str, dict[str, str]] = defaultdict(dict)
    for row in read_csv("predicates.csv"):
        predicates[row["event_id"]][row["predicate_name"]] = row["value"]

    rows = []
    for event in read_csv("events.csv"):
        doc = docs[event["doc_id"]]
        stock = stocks[event["stock_code"]]
        fwd = returns.get(event["event_id"], {})
        pred = predicates[event["event_id"]]
        ret_5d = fwd.get("forward_return_5d", "")
        if ret_5d == "":
            direction = "missing"
        elif float(ret_5d) > 0:
            direction = "positive"
        elif float(ret_5d) < 0:
            direction = "negative"
        else:
            direction = "flat"
        rows.append(
            {
                "event_id": event["event_id"],
                "doc_id": event["doc_id"],
                "source_type": doc["source_type"],
                "title": doc["title"],
                "stock_code": event["stock_code"],
                "stock_name": stock["stock_name"],
                "industry_sector": stock["industry_sector"],
                "event_type": event["event_type"],
                "event_time": event["event_time"],
                "evidence_strength": event["evidence_strength"],
                "has_policy_support": pred.get("has_policy_support", ""),
                "policy_directly_related_to_business": pred.get("policy_directly_related_to_business", ""),
                "evidence_from_authoritative_source": pred.get("evidence_from_authoritative_source", ""),
                "social_attention_spikes": pred.get("social_attention_spikes", ""),
                "forward_return_5d": ret_5d,
                "demo_return_direction": direction,
                "manual_case_priority": "P1" if event["event_type"] in {"policy_support", "capacity_expansion"} else "P2",
            }
        )
    write_csv(
        VIEW_DIR / "案例索引.csv",
        [
            "event_id",
            "doc_id",
            "source_type",
            "title",
            "stock_code",
            "stock_name",
            "industry_sector",
            "event_type",
            "event_time",
            "evidence_strength",
            "has_policy_support",
            "policy_directly_related_to_business",
            "evidence_from_authoritative_source",
            "social_attention_spikes",
            "forward_return_5d",
            "demo_return_direction",
            "manual_case_priority",
        ],
        rows,
    )


def prepare_view_material_index() -> None:
    files = [
        ("任务进度.md", "当前 B 线自动任务进度，本地维护且不提交"),
        ("人工待办.md", "必须人工完成或确认的事项，本地维护且不提交"),
        ("团队对接手册.md", "B 与 A/C 的交接步骤、确认模板、联调标准和收口顺序"),
        ("A口径确认建议稿.md", "给 A 确认事件类型、谓词和表述边界的建议稿"),
        ("C联调运行手册.md", "给 C 复跑流水线、检查输出和定位问题的运行手册"),
        ("可演示成果优化与下一步对接说明.md", "Flask Demo 架构、演示步骤与团队收口顺序"),
        ("Demo演示脚本.md", "Flask Demo 演示顺序和讲解词草稿"),
        ("Demo桌面截图.png", "1440×900 视口的完整分析结果截图"),
        ("Demo移动端截图.png", "390×844 视口的完整分析结果截图"),
        ("答辩问答素材.md", "围绕项目定位、数据、谓词、因子和回测的答辩问答素材"),
        ("真实文本来源核验报告.md", "130 条真实文本的联网、域名、详情页、摘要结构和唯一性核验结论"),
        ("源文本核验明细.csv", "逐条真实文本来源核验状态和说明"),
        ("真实行情获取记录.md", "东方财富前复权行情获取参数、覆盖范围和注意事项"),
        ("真实行情导入模板.csv", "真实前复权行情导入字段模板"),
        ("真实行情校验报告.md", "当前行情文件的独立结构校验报告"),
        ("流水线输入保护验证报告.md", "安全模式复跑不覆盖 raw_documents.csv 的哈希验证报告"),
        ("人工抽检结果校验报告.md", "事件/谓词人工抽检结果 pass/revise/drop 合法值校验报告"),
        ("事件人工抽检样本.csv", "事件抽取人工抽检表"),
        ("事件抽检问题处理记录.md", "10 条事件 drop 结论、误判根因和规则修复闭环"),
        ("谓词人工抽检样本.csv", "谓词判断人工抽检表"),
        ("案例索引.csv", "全量事件案例索引，便于挑 PPT 案例"),
        ("PPT案例素材包.md", "PPT 可复用案例素材"),
        ("数据质量报告.md", "核心 CSV、研究输出完整性、分布和风险警告的统一质量报告"),
        ("未来函数审计明细.md", "事件收益对齐和未来函数审计"),
        ("交付包自检报告.md", "参考文档、查看材料、数据附件和 gitignore 的自动自检结果"),
        ("因子研究报告.md", "自动生成的研究报告初稿"),
    ]
    lines = [
        "# AlphaLens 查看材料索引",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 建议阅读顺序",
        "",
        "1. `任务进度.md`：先看当前自动推进到哪里。",
        "2. `人工待办.md`：再看哪些必须人工处理。",
        "3. `团队对接手册.md`：按步骤把材料交给 A 和 C，并完成确认、联调与收口。",
        "4. `A口径确认建议稿.md`：和 A 确认事件、谓词、对外表达边界前看。",
        "5. `真实文本来源核验报告.md` / `源文本核验明细.csv`：查看联网获取方法和逐条核验结论。",
        "6. `真实行情获取记录.md` / `真实行情导入模板.csv` / `真实行情校验报告.md`：替换真实前复权行情前后看。",
        "7. `流水线输入保护验证报告.md`：确认安全模式不会覆盖真实文本。",
        "8. `数据质量报告.md` / `未来函数审计明细.md`：查看数据完整性、质量警告和回测时间审计。",
        "9. `C联调运行手册.md`：和 C 对接字段、行数、回测输入时看。",
        "10. `可演示成果优化与下一步对接说明.md`：查看 Demo 架构、演示步骤和团队收口顺序。",
        "11. `Demo演示脚本.md` / `答辩问答素材.md`：准备演示和答辩前看。",
        "12. `PPT案例素材包.md` / `案例索引.csv`：准备展示材料时看。",
        "",
        "## 文件说明",
        "",
        "| 文件 | 用途 |",
        "|------|------|",
    ]
    for filename, description in files:
        lines.append(f"| `{filename}` | {description} |")
    lines.extend(
        [
            "",
            "## 目录边界",
            "",
            "- `查看材料/`：报告、进度、待办、核验表、案例和联调材料，面向查看和交付。",
            "- `参考文档/`：赛题说明、数据规范、工作指南、schema、prompt、论文等底层参考资料。",
            "- `data/sample/`：代码实际读取的数据契约文件，不为了展示而改名。",
            "",
        ]
    )
    write_text(VIEW_DIR / "材料索引.md", lines)


def prepare_a_schema_confirmation_brief() -> None:
    event_counts = Counter(row["event_type"] for row in read_csv("events.csv"))
    predicate_rows = read_csv("predicates.csv")
    predicate_counts = Counter(row["predicate_name"] for row in predicate_rows)
    lines = [
        "# AlphaLens A 口径确认建议稿",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 需要 A 确认的核心口径",
        "",
        "本建议稿用于让 A 快速确认事件类型、谓词定义和对外表述边界。当前内容是自动候选版，不能替代项目负责人最终判断。",
        "",
        "## 建议项目定位",
        "",
        "- AlphaLens 是 AI 量化研究助手，不是股票价格预测系统。",
        "- 项目目标是把非结构化金融文本转化为可解释、可回测、可复用的另类因子研究素材。",
        "- 对外表达应强调规则归纳、因子挖掘、回测审计和投研效率，不承诺收益或交易建议。",
        "",
        "## 当前事件分布",
        "",
        "| event_type | 样本数 | A 是否确认 | 备注 |",
        "|------------|--------|------------|------|",
    ]
    for event_type, count in sorted(event_counts.items()):
        lines.append(f"| `{event_type}` | {count} | 待确认 | 自动抽取候选口径 |")

    lines.extend(
        [
            "",
            "## 当前谓词覆盖",
            "",
            "| predicate_name | 样本数 | A 是否确认 | 建议确认点 |",
            "|----------------|--------|------------|------------|",
        ]
    )
    for predicate_name, count in sorted(predicate_counts.items()):
        if predicate_name in {"event_evidence_strength", "event_has_short_term_price_impact"}:
            confirm_point = "分数区间和解释口径是否合理"
        else:
            confirm_point = "true/false 判断边界是否清晰"
        lines.append(f"| `{predicate_name}` | {count} | 待确认 | {confirm_point} |")

    lines.extend(
        [
            "",
            "## 建议 A 重点确认",
            "",
            "1. `policy_support` 是否只覆盖政策利好，还是也包含产业行动方案、税收优惠、补贴、目录管理。",
            "2. `capacity_expansion` 是否只接受明确的募投项目、产能建设和项目投产事实；评级报告背景描述和泛化交付能力不自动算事件。",
            "3. `investor_question_pressure` 需要多长时间窗和多少条提问才能成立；当前单条互动问答不生成该事件。",
            "4. `social_attention_spikes` 的判断边界是否要求可量化变化或多源报道，单篇新闻和单条问答不自动成立。",
            "5. `event_has_short_term_price_impact` 是否作为经验强度分数保留，还是改名为更中性的 `historical_attention_impact_score`。",
            "",
            "## 对外表述边界",
            "",
            "- 可以说：将文本事件结构化为可解释因子候选信号。",
            "- 可以说：通过未来函数审计和样例回测验证研究链路。",
            "- 不应说：系统预测股价、推荐买卖、保证收益。",
            "- 不应把当前候选行情结果描述为正式实证结论。",
            "",
        ]
    )
    write_text(VIEW_DIR / "A口径确认建议稿.md", lines)


def prepare_c_runbook() -> None:
    metric_rows = read_csv("backtest_metrics.csv")
    metrics = {row["metric"]: row["value"] for row in metric_rows}
    metric_descriptions = {row["metric"]: row["description"] for row in metric_rows}
    lines = [
        "# AlphaLens C 联调运行手册",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 目标",
        "",
        "帮助 C 在不改 B 线 CSV 字段契约的前提下接收真实文本、事件和谓词，复跑规则、因子、回测与 Demo，并给出可复现的联调结论。",
        "",
        "## B 与 C 的边界",
        "",
        "- B 负责 stock_pool、raw_documents、entity_links、events、predicates 的事实、字段和语义质量。",
        "- C 负责规则搜索、因子计算、收益对齐、回测指标、Flask Demo 和运行环境。",
        "- C 发现 B 数据问题时提交 doc_id/event_id、复现命令和期望结果，不直接改锁定字段或批量覆盖输入。",
        "- B 修改输入或抽取规则后，必须通知 C 重新生成全部研究输出，不能只替换单个下游 CSV。",
        "",
        "## B 线锁定输入",
        "",
        "| 文件 | 当前行数 | 字段状态 |",
        "|------|----------|----------|",
    ]
    for filename, expected_header in CORE_SCHEMAS.items():
        status = "通过" if read_header(filename) == expected_header else "不一致"
        lines.append(f"| `data/sample/{filename}` | {len(read_csv(filename))} | {status} |")

    lines.extend(
        [
            "",
            "## C 线研究输出",
            "",
            "| 文件 | 当前行数 | 字段状态 | 用途 |",
            "|------|----------|----------|------|",
        ]
    )
    usage = {
        "predicate_matrix.csv": "事件-谓词矩阵",
        "event_forward_returns.csv": "事件后收益与未来函数审计",
        "rules.csv": "候选规则和支持数",
        "factors.csv": "事件级因子值与触发路径",
        "factor_snapshot.csv": "Demo 截面展示",
        "group_returns.csv": "分组收益展示",
        "rank_ic_timeseries.csv": "Rank IC 时序",
        "backtest_metrics.csv": "报告和 Demo 指标",
    }
    for filename, expected_header in RESEARCH_SCHEMAS.items():
        status = "通过" if read_header(filename) == expected_header else "不一致"
        lines.append(f"| `data/sample/{filename}` | {len(read_csv(filename))} | {status} | {usage[filename]} |")

    lines.extend(
        [
            "",
            "## 推荐运行顺序",
            "",
            "```bash",
            "git pull",
            ".venv/bin/python -m pip install -r requirements.txt",
            ".venv/bin/python run_pipeline.py --preserve-inputs",
            ".venv/bin/python scripts/validate_input_preservation.py",
            ".venv/bin/python scripts/audit_text_sources.py",
            ".venv/bin/python scripts/validate_b_data.py",
            ".venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv",
            ".venv/bin/python scripts/validate_manual_review_results.py",
            ".venv/bin/python scripts/validate_research_outputs.py",
            ".venv/bin/python scripts/validate_delivery_package.py",
            ".venv/bin/python app/server.py",
            "# 另开终端：cd tests && npm ci && npx playwright install chromium && ALPHALENS_PYTHON=../.venv/bin/python npm test",
            "```",
            "",
            "真实文本或真实行情开始写入后，不要使用 `--force-sample-generation`。只重算 B 线时使用 `run_b_pipeline.py --skip-sample-generation`。",
            "",
            "## 当前关键指标",
            "",
            "| 指标 | 当前值 | 说明 |",
            "|------|--------|------|",
        ]
    )
    for metric, value in sorted(metrics.items()):
        lines.append(f"| `{metric}` | {value} | {metric_descriptions.get(metric, '')} |")

    lines.extend(
        [
            "",
            "## C 侧重点",
            "",
            "- 读取 stock_code、doc_id、event_id 时必须保留字符串，避免前导零丢失。",
            "- event_forward_returns.csv 的 entry_trade_date 必须严格晚于 event_time。",
            "- factors.csv 的 trigger_event_ids 和 trigger_rule_ids 必须能回溯到事件和规则。",
            "- 当前 adj_factor=1.000000 是已接受的字段占位，不是真实复权因子序列。",
            "- 不改变 `参考文档/数据格式规范.md` 中锁定字段名。",
            "- Demo 至少检查来源元数据、事件/谓词追溯、冻结规则、候选因子、历史回测参考和报告下载。",
            "- 新文本接口不得临时生成未来收益；历史指标只能读取正式 CSV。",
            "",
            "## C 的通过标准",
            "",
            "1. 安全流水线退出码为 0，输入保护报告前后 SHA256 一致。",
            "2. B 数据、研究输出和交付包校验均为 0 errors。",
            "3. 收益入场日全部严格晚于事件日。",
            "4. 因子触发事件和规则均可回溯，future_info_ok 全为 true。",
            "5. Demo 从新进程启动后无空白、报错或旧数据缓存。",
            "6. 联调记录写明 commit、环境、命令、结果、warning 和是否可冻结数字。",
            "",
            "## 常见问题定位",
            "",
            "| 现象 | 优先检查 |",
            "|------|----------|",
            "| 股票代码变成 274 或 2594 | CSV 读取时是否把 stock_code 当数字 |",
            "| 未来函数审计失败 | entry_trade_date 是否不晚于 event_time |",
            "| 因子没有触发规则 | 谓词是否缺失，或规则支持数低于 5 |",
            "| Demo 报告缺失 | `查看材料/因子研究报告.md` 是否生成 |",
            "| 交付包自检失败 | 查看 `查看材料/交付包自检报告.md` |",
            "",
            "## C 回传模板",
            "",
            "```text",
            "C 联调记录",
            "commit：",
            "Python/系统：",
            "运行命令：",
            "errors/warnings：",
            "Demo 页面结果：",
            "发现问题（附 doc_id/event_id）：",
            "需要 B 修改：",
            "是否可进入 PPT 数字冻结：是/否",
            "```",
            "",
        ]
    )
    write_text(VIEW_DIR / "C联调运行手册.md", lines)


def prepare_team_handoff_manual() -> None:
    docs = read_csv("raw_documents.csv")
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    rules = read_csv("rules.csv")
    qualified_rules = sum(1 for row in rules if row["status"] == "qualified")
    source_counts = Counter(row["source_type"] for row in docs)
    source_summary = "、".join(
        f"{source_type} {source_counts[source_type]} 条"
        for source_type in ("policy", "announcement", "news", "ir_qa")
    )
    lines = [
        "# AlphaLens 团队对接手册",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 这份手册解决什么",
        "",
        "本手册供 B 负责人直接执行，目标是把当前数据成果交给 A 做金融口径确认、交给 C 做工程联调，并按固定顺序收口。不要把整份仓库直接丢给队友后口头说明；每次交接都要有指定材料、明确问题和书面结论。",
        "",
        "## 当前可交接状态",
        "",
        f"- 真实来源文本：{len(docs)} 条，{source_summary}；程序化来源核验已完成。",
        f"- 结构化结果：{len(events)} 条事件、{len(predicates)} 条谓词判断。",
        f"- 研究输出：{len(rules)} 条候选规则，其中 {qualified_rules} 条达到当前支持数和评分门槛。",
        "- 行情：东方财富 `fqt=1` 前复权价格候选版；`adj_factor=1` 是已接受的字段占位，不是真实复权因子序列。",
        "- 事件抽检：已完成并处理 drop 项；谓词抽检仍需人工完成。",
        "- 当前分支和最新 commit 由 B 在发起交接时填写，队友必须从同一 commit 开始复现。",
        "",
        "## 三个人各自负责什么",
        "",
        "| 角色 | 最终责任 | 本轮必须交付 | 不应擅自修改 |",
        "|------|----------|--------------|--------------|",
        "| A：项目负责人/量化研究 | 金融口径、项目定位、PPT、答辩和数字冻结 | Schema 确认结论、PPT 案例选择、最终数字确认 | 不直接批量修改 B 的 CSV 或 C 的回测实现 |",
        "| B：数据与事件谓词 | 文本来源、实体链接、事件、谓词和数据质量 | 可复现输入、核验材料、抽检闭环、问题修订 | 不替 A 决定金融口径，不替 C 决定回测实现 |",
        "| C：工程与回测 | 规则、因子、收益对齐、回测、Demo 和运行环境 | 联调记录、问题清单、可运行 Demo、回测口径确认 | 不改锁定字段名，不覆盖 B 的真实文本输入 |",
        "",
        "## 你现在按这个顺序做",
        "",
        "1. 在 GitHub 确认 B 的最新提交已推送，把分支名和 commit 写入发给 A、C 的消息。",
        "2. 先把 A 材料包发给 A，让 A 只回答五个口径问题；不要等待 C 才开始。",
        "3. 同时把 C 材料包发给 C，让 C 从同一 commit 运行安全流水线和 Demo。",
        "4. 你完成 `谓词人工抽检样本.csv` 的剩余人工结论；只填 `pass`、`revise`、`drop`。",
        "5. 收到 A 的书面确认后，由 B 修改 Schema/抽取规则并安全复跑；不要让 A 直接改生成 CSV。",
        "6. 把 B 新 commit 发给 C，由 C 重跑规则、因子、回测和 Demo，并回传联调记录。",
        "7. C 通过后，A 才从最终 CSV 和报告取 PPT 数字；此前 PPT 中的指标只能标为候选。",
        "8. 三人共同做一次 CSV、报告、Demo、PPT 交叉检查，确认后冻结比赛成果包。",
        "",
        "## B 给 A 的材料包",
        "",
        "按以下顺序发给 A，不需要 A 阅读全部代码：",
        "",
        "| 顺序 | 文件 | A 要做什么 |",
        "|------|------|------------|",
        "| 1 | `查看材料/A口径确认建议稿.md` | 对五个金融口径逐项确认 |",
        "| 2 | `参考文档/事件与谓词Schema.md` | 检查事件、谓词定义是否适合对外表达 |",
        "| 3 | `查看材料/事件抽检问题处理记录.md` | 了解已删除误判和修复边界 |",
        "| 4 | `查看材料/谓词人工抽检样本.csv` | 复核 B 标出的争议样本 |",
        "| 5 | `查看材料/PPT案例素材包.md` 与 `案例索引.csv` | 选 2 至 3 个答辩案例 |",
        "| 6 | `查看材料/真实文本来源核验报告.md` | 确认数据来源表述 |",
        "| 7 | `查看材料/因子研究报告.md` 与 `数据质量报告.md` | 确认 PPT 能引用哪些数字及限制 |",
        "",
        "### A 必须明确回答的五个问题",
        "",
        "1. `policy_support` 是否包含产业行动方案、税收优惠、补贴和目录管理，还是只指直接利好政策。",
        "2. `capacity_expansion` 是否只认明确的募投、产能建设和项目投产事实。",
        "3. `investor_question_pressure` 的聚合时间窗和最低提问数量分别是多少。",
        "4. `social_attention_spikes` 是否必须有量化变化或多源报道，单篇新闻能否成立。",
        "5. `event_has_short_term_price_impact` 是否保留；若保留，是否改成更中性的 `historical_attention_impact_score`。",
        "",
        "### 与 A 的 45 分钟会议安排",
        "",
        "| 时间 | 内容 | 产出 |",
        "|------|------|------|",
        "| 会前 | A 阅读上述 1 至 4 号材料 | 标出不认可或边界不清的项目 |",
        "| 0-5 分钟 | B 说明当前数据链路和已完成程度 | 统一项目不是股价预测系统 |",
        "| 5-25 分钟 | 逐项讨论五个口径问题 | 每项得到保留/修改/删除结论 |",
        "| 25-35 分钟 | 查看 2 至 3 个案例的事件-谓词-规则路径 | 确定 PPT 案例 |",
        "| 35-40 分钟 | 确认行情和 `adj_factor=1` 限制话术 | 确定答辩表达 |",
        "| 40-45 分钟 | 复述结论、责任人和完成时间 | 留下书面确认记录 |",
        "",
        "### A 回传模板",
        "",
        "```text",
        "A 口径确认记录",
        "基于 commit：",
        "policy_support：保留/修改/删除；边界：",
        "capacity_expansion：保留/修改/删除；边界：",
        "investor_question_pressure：时间窗；最低数量：",
        "social_attention_spikes：成立条件：",
        "短期价格影响谓词：保留/改名/删除；名称：",
        "PPT 选用案例 event_id：",
        "允许引用的指标：",
        "必须披露的限制：",
        "A 确认人和日期：",
        "```",
        "",
        "### A 侧通过标准",
        "",
        "- 五个口径均有明确答案，不接受只回复“看起来可以”。",
        "- PPT 案例能从 doc_id 追溯到真实来源，从 event_id 追溯到谓词和规则。",
        "- 对外话术不出现预测股价、推荐买卖或保证收益。",
        "- 明确披露 `adj_factor=1` 仅为占位和当前回测只验证研究链路。",
        "",
        "## B 给 C 的材料包",
        "",
        "| 顺序 | 文件/信息 | C 要做什么 |",
        "|------|-----------|------------|",
        "| 1 | GitHub 分支与 commit | 从完全相同的代码和数据版本开始 |",
        "| 2 | `查看材料/C联调运行手册.md` | 按命令复跑并填写回传模板 |",
        "| 3 | `参考文档/数据格式规范.md` | 按锁定字段和 dtype 读取 CSV |",
        "| 4 | `data/sample/` | 接收 B 输入并生成研究输出 |",
        "| 5 | `查看材料/数据质量报告.md` 与 `未来函数审计明细.md` | 对照行数、warning 和时间关系 |",
        "| 6 | `查看材料/真实行情获取记录.md` | 接受当前行情口径并保留限制说明 |",
        "",
        "### C 必须完成的动作",
        "",
        "1. 使用 `.venv/bin/python run_pipeline.py --preserve-inputs`，确认 `raw_documents.csv` 哈希未变化。",
        "2. 依次运行 B 数据、行情、人工抽检、研究输出和交付包校验。",
        "3. 检查所有 `entry_trade_date` 严格晚于 `event_time`。",
        "4. 随机挑选至少 3 个因子值，回溯 `trigger_rule_ids`、`trigger_event_ids`、谓词和原文。",
        "5. 从新进程启动 Flask，检查桌面/移动页面、离线 Plotly、数据状态和报告下载。",
        "6. 回传 commit、环境、命令、errors/warnings、Demo 结果和需要 B 修复的 ID。",
        "",
        "### C 侧通过标准",
        "",
        "- 不改锁定 CSV 字段，不把股票代码读成数字，不覆盖真实文本。",
        "- 所有校验为 0 errors；warning 必须逐条解释。",
        "- 未来函数审计通过，因子触发链可回溯。",
        "- Demo 可从干净进程启动，页面没有空白、旧缓存或文件缺失。",
        "- C 明确回复“可以/不可以进入 PPT 数字冻结”，不能只回复“代码能跑”。",
        "",
        "## 三人收口顺序",
        "",
        "```text",
        "A 确认金融 Schema",
        "        ↓",
        "B 修改规则并安全复跑，提交新 commit",
        "        ↓",
        "C 重跑规则、因子、回测和 Demo",
        "        ↓",
        "A 从最终 CSV/报告冻结 PPT 数字",
        "        ↓",
        "A/B/C 交叉检查并冻结成果包",
        "```",
        "",
        "不要颠倒顺序。A 的口径变化会影响 B 的事件和谓词；B 的输出变化会影响 C 的规则和回测；因此 PPT 数字必须最后冻结。",
        "",
        "## 问题归属表",
        "",
        "| 问题 | 第一责任人 | 处理方式 |",
        "|------|------------|----------|",
        "| URL、日期、文本摘要、股票关联错误 | B | 修正 `raw_documents.csv` 或来源核验规则后复跑 |",
        "| 实体、事件类型、证据片段、谓词值错误 | B | 修改抽取逻辑，保留抽检记录 |",
        "| 金融定义、PPT 叙事、可否对外表述 | A | 书面确认口径，B/C 按结论实现 |",
        "| 收益对齐、规则支持数、因子公式、回测指标 | C | 提供复现记录和测试，必要时请 A 确认金融含义 |",
        "| CSV 字段冲突 | B 与 C | 以 `参考文档/数据格式规范.md` 为准，不单方面改字段 |",
        "| PPT 数字与 CSV 不一致 | A 主责，B/C 协查 | 回到最终 commit 逐项查来源，禁止手填修饰 |",
        "",
        "## Git 交接规则",
        "",
        "1. 每次交接都写清分支名和完整 commit，不使用“最新版”这种描述。",
        "2. A/C 开始前先 `git status`；有本地改动时先提交到自己的分支，不覆盖他人修改。",
        "3. B 的数据修订、C 的工程修订分别做独立 commit，提交信息写清影响范围。",
        "4. 任何人修改锁定 CSV 字段前必须三人确认；正常情况下不允许修改。",
        "5. 合并后由 C 用合并 commit 复跑，由 A 用同一 commit 核对 PPT。",
        "6. `data/raw/`、`data/processed/`、`data/external/` 不得提交；`任务进度.md` 和 `人工待办.md` 保持本地忽略。",
        "",
        "## 建议 48 小时节奏",
        "",
        "| 截止时间 | A | B | C |",
        "|----------|---|---|---|",
        "| T+4 小时 | 阅读口径材料并标注问题 | 发材料包，继续谓词抽检 | 拉取 commit，完成首次安全复跑 |",
        "| T+12 小时 | 完成五项口径确认 | 汇总抽检和 A 结论 | 回传首次联调问题 |",
        "| T+24 小时 | 确认修订后案例 | 修改抽取规则、复跑、提交 | 等待 B 新 commit 或并行修工程问题 |",
        "| T+36 小时 | 起草最终 PPT 数字页 | 支持问题定位 | 用新 commit 完成回测和 Demo 联调 |",
        "| T+48 小时 | 冻结 PPT | 核对数据与案例追溯 | 冻结 Demo 和回测输出 |",
        "",
        "## 可直接发送给 A 的消息",
        "",
        "```text",
        "AlphaLens 的 B 线数据已整理到 GitHub。请基于我发你的分支和 commit，先阅读《A口径确认建议稿》和《事件与谓词Schema》，并按《团队对接手册》的 A 回传模板确认五个口径问题。请同时从 PPT 案例素材包中选 2-3 个 event_id。当前回测只验证研究链路，adj_factor=1 为占位字段，这两点需要保留在答辩表述中。",
        "```",
        "",
        "## 可直接发送给 C 的消息",
        "",
        "```text",
        "AlphaLens 的 B 线输入与 Flask Demo 已整理到 GitHub。请基于我发你的分支和 commit，按《C联调运行手册》使用 run_pipeline.py --preserve-inputs 复跑，不要使用 --force-sample-generation。请完成全部校验、未来函数检查、3 个因子链路回溯和桌面/移动页面检查，再按手册模板回传联调记录，并明确是否可进入 PPT 数字冻结。",
        "```",
        "",
        "## 最终交接清单",
        "",
        "- [ ] A 已书面确认五个金融口径。",
        "- [ ] B 已完成谓词人工抽检并处理 revise/drop。",
        "- [ ] B 已依据 A 结论安全复跑并推送 commit。",
        "- [ ] C 已在同一 commit 上完成全量校验和 Demo 联调。",
        "- [ ] C 已确认未来函数审计和因子追溯通过。",
        "- [ ] A 已从最终 CSV/报告核对 PPT 数字。",
        "- [ ] 答辩材料已披露行情与 `adj_factor=1` 限制。",
        "- [ ] 三人确认项目不宣称预测股价或提供投资建议。",
        "",
    ]
    write_text(VIEW_DIR / "团队对接手册.md", lines)


def prepare_demo_script() -> None:
    docs = read_csv("raw_documents.csv")
    events = read_csv("events.csv")
    predicates = read_csv("predicates.csv")
    rules = read_csv("rules.csv")
    qualified_rules = sum(1 for row in rules if row["status"] == "qualified")
    lines = [
        "# AlphaLens Demo 演示脚本",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 开场 20 秒",
        "",
        "AlphaLens 的定位不是用 AI 预测股价，而是让 AI 帮研究员把政策、公告、新闻和互动问答转化为可解释、可回测、可复用的另类因子研究素材。",
        "",
        "## 当前 Demo 数据规模",
        "",
        f"- 文本样本：{len(docs)} 条",
        f"- 结构化事件：{len(events)} 条",
        f"- 谓词判断：{len(predicates)} 条",
        f"- 合格规则：{qualified_rules} 条",
        "",
        "## 页面讲解顺序",
        "",
        "| 操作 | 讲解重点 | 建议话术 |",
        "|------|----------|----------|",
        "| 查看研究数据 | 展示 Git 数据版本和当前规模 | 页面读取正式流水线产物，不使用手填演示数字 |",
        "| 点击储能政策并分析 | 运行实体、事件和谓词共享函数 | 来源元数据参与权威来源谓词判断 |",
        "| 查看谓词与规则 | 展示锁定字段和冻结规则 | 规则来自历史样本且支持数不少于 5 |",
        "| 查看候选因子 | 展示规则评分和证据加权 | 候选值不是收益预测或买卖信号 |",
        "| 查看历史回测参考 | 展示正式 CSV 指标与审计 | 历史回测不是刚输入文本的单次回测 |",
        "| 下载研究记录 | 展示可追溯 Markdown | 报告保留限制与免责声明 |",
        "",
        "## 现场启动",
        "",
        "```bash",
        ".venv/bin/python app/server.py",
        "```",
        "",
        "## 收尾 20 秒",
        "",
        "这个 Demo 证明了从非结构化舆情文本到结构化因子研究信号的链路可跑通。当前行情采用东方财富前复权价格候选版，adj_factor=1 仅为字段占位；结果用于研究链路验证，不是投资结论。",
        "",
    ]
    write_text(VIEW_DIR / "Demo演示脚本.md", lines)


def prepare_defense_qa_material() -> None:
    documents = read_csv("raw_documents.csv")
    source_counts = Counter(row["source_type"] for row in documents)
    source_summary = "、".join(f"{name} {source_counts[name]} 条" for name in ["policy", "announcement", "news", "ir_qa"])
    lines = [
        "# AlphaLens 答辩问答素材",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 项目定位",
        "",
        "### Q1：你们是不是在用大模型预测股价？",
        "",
        "不是。AlphaLens 不让大模型直接预测股价，也不输出买卖建议。大模型主要用于文本结构化、事件抽取、谓词判断和规则归纳辅助，最终产出的是可解释、可回测的另类因子研究素材。",
        "",
        "### Q2：项目的创新点是什么？",
        "",
        "创新点在于把非结构化金融文本拆成事件和谓词，再通过规则触发形成可审计的因子信号。相比直接情绪打分，这条链路更容易解释、复核和回测。",
        "",
        "## 数据和质量",
        "",
        "### Q3：当前样本文本是否都是真实来源？",
        "",
        f"当前 {len(documents)} 条文本已完成程序化联网核验：{source_summary}，URL 全局唯一，并检查详情页、日期、白名单域名、摘要结构和访问状态。逐条结论见 `查看材料/源文本核验明细.csv`；这不等同于版权授权或法律认证。",
        "",
        "### Q4：如何避免未来函数？",
        "",
        "事件只使用 `event_time` 当天及之前可获得的信息。收益对齐时，`entry_trade_date` 必须严格晚于 `event_time`，并由 `event_forward_returns.csv` 和 `查看材料/未来函数审计明细.md` 审计。",
        "",
        "## 谓词和因子",
        "",
        "### Q5：谓词是什么？",
        "",
        "谓词是金融事件的标准化判断题。例如是否存在政策支持、是否直接相关主营业务、证据是否来自权威来源、证据强度是多少。它把自然语言事件转成机器可计算的特征。",
        "",
        "### Q6：因子怎么生成？",
        "",
        "系统先把文本挂到股票，再抽取事件，再判断谓词。满足规则的事件会触发因子信号，结合规则得分和证据强度生成某日某股票的因子值。",
        "",
        "## 回测和边界",
        "",
        "### Q7：当前回测结果能说明策略赚钱吗？",
        "",
        "不能。当前 open/high/low/close 使用东方财富 `fqt=1` 前复权价格候选版；项目已接受 `adj_factor=1` 作为字段占位，但它不是真实复权因子序列。回测只能验证研究链路，不能作为投资结论或收益承诺。",
        "",
        "### Q8：如果真实回测效果一般，项目还有价值吗？",
        "",
        "有。项目价值不只在单个规则收益，而在于提供一套可复用的文本因子研究工作流：从文本结构化、规则候选生成、因子构造到回测审计，都能提升研究效率。",
        "",
        "## 交付计划",
        "",
        "### Q9：下一步最关键的工作是什么？",
        "",
        "下一步是完成人工谓词抽检，由 A 确认事件和谓词金融口径，再与 C 联调当前真实文本和候选行情，最后冻结 PPT 与 CSV 数字。",
        "",
    ]
    write_text(VIEW_DIR / "答辩问答素材.md", lines)


def main() -> None:
    prepare_manual_review_samples()
    prepare_event_review_resolution()
    prepare_market_data_import_template()
    prepare_future_info_audit()
    prepare_ppt_case_pack()
    prepare_case_index()
    prepare_a_schema_confirmation_brief()
    prepare_c_runbook()
    prepare_team_handoff_manual()
    prepare_demo_script()
    prepare_defense_qa_material()
    prepare_view_material_index()
    print("B handoff materials prepared.")


if __name__ == "__main__":
    main()
