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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_view_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def prepare_source_verification_queue() -> None:
    docs = read_csv("raw_documents.csv")
    rows = []
    for row in docs:
        candidate = "true" if "待人工核验" in row["content"] else "false"
        priority = "P0" if candidate == "true" else "P1"
        rows.append(
            {
                "doc_id": row["doc_id"],
                "source_type": row["source_type"],
                "title": row["title"],
                "publish_time": row["publish_time"],
                "source_name": row["source_name"],
                "url": row["url"],
                "needs_manual_verification": candidate,
                "verification_priority": priority,
                "verification_note": "核验原文、发布日期、来源 URL、涉及主体与主营业务相关性",
            }
        )
    write_csv(
        VIEW_DIR / "源文本核验队列.csv",
        [
            "doc_id",
            "source_type",
            "title",
            "publish_time",
            "source_name",
            "url",
            "needs_manual_verification",
            "verification_priority",
            "verification_note",
        ],
        rows,
    )


def deterministic_sample(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
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
    event_notes = load_manual_review_notes(event_sample_path, ["review_item"])
    predicate_notes = load_manual_review_notes(predicate_sample_path, ["event_id", "predicate_name"])

    event_sample_rows = []
    for event in deterministic_sample(events, 10):
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


def prepare_completeness_report() -> None:
    table_names = [
        "stock_pool.csv",
        "raw_documents.csv",
        "entity_links.csv",
        "events.csv",
        "predicates.csv",
        "market_data.csv",
        "predicate_matrix.csv",
        "event_forward_returns.csv",
        "rules.csv",
        "factors.csv",
        "factor_snapshot.csv",
        "backtest_metrics.csv",
    ]
    lines = [
        "# AlphaLens 数据完整性检查报告",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "| 文件 | 行数 | 空字符串单元格 | 说明 |",
        "|------|------|----------------|------|",
    ]
    for filename in table_names:
        rows = read_csv(filename)
        empty_cells = sum(1 for row in rows for value in row.values() if value == "")
        note = "通过" if empty_cells == 0 else "存在允许的空值或待补字段"
        lines.append(f"| `{filename}` | {len(rows)} | {empty_cells} | {note} |")
    lines.extend(
        [
            "",
            "说明：部分人工审阅字段位于 `查看材料/*人工抽检样本.csv`，其空值用于人工填写，不属于 B↔C 数据契约。",
            "",
        ]
    )
    write_text(VIEW_DIR / "数据完整性检查报告.md", lines)


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


def prepare_delivery_checklist() -> None:
    docs = read_csv("raw_documents.csv")
    candidate_count = sum(1 for row in docs if "待人工核验" in row["content"])
    source_counts = Counter(row["source_type"] for row in docs)
    lines = [
        "# AlphaLens B 线交付检查清单",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 已自动完成",
        "",
        "- [x] 30 只新能源股票池",
        "- [x] 100+ 文本样本真实来源候选版",
        "- [x] 实体链接结果",
        "- [x] 事件抽取结果",
        "- [x] 谓词判断结果",
        "- [x] 当前行情数据",
        "- [x] 数据质量报告",
        "- [x] 解释案例与 PPT 案例素材",
        "- [x] 未来函数审计",
        "- [x] Streamlit Demo 可运行",
        "- [x] 流水线保留输入安全模式",
        "- [x] 真实文本核验进度统计",
        "- [x] 真实行情导入模板和独立校验脚本",
        "- [x] 人工抽检结果合法值校验",
        "",
        "## 数据规模",
        "",
        f"- raw_documents.csv: {len(docs)} 行，其中待人工核验候选摘要 {candidate_count} 行",
        f"- policy: {source_counts['policy']}",
        f"- announcement: {source_counts['announcement']}",
        f"- news: {source_counts['news']}",
        f"- ir_qa: {source_counts['ir_qa']}",
        "",
        "## 人工完成后才能标记完成",
        "",
        "- [ ] 联网替换文本人工抽查确认",
        "- [ ] 真实前复权行情口径人工复核",
        "- [ ] 事件和谓词随机抽检",
        "- [ ] A 确认金融 schema",
        "- [ ] C 用真实数据联调回测",
        "- [ ] PPT 数字与最终 CSV 交叉检查",
        "",
    ]
    write_text(VIEW_DIR / "B线交付检查清单.md", lines)


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


def prepare_contract_checklist() -> None:
    lines = [
        "# AlphaLens C 联调数据契约清单",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 说明",
        "",
        "- 本清单用于 B→C 交接时快速确认 CSV 文件、字段顺序和行数。",
        "- `data/sample/*.csv` 是代码读取入口；`查看材料/*.md` 和人工抽检 CSV 是说明与核验材料。",
        "- 正式回测前仍需替换真实来源文本和真实前复权行情。",
        "",
        "## B 线锁定 CSV",
        "",
        "| 文件 | 行数 | 字段状态 | 字段顺序 |",
        "|------|------|----------|----------|",
    ]
    for filename, expected_header in CORE_SCHEMAS.items():
        rows = read_csv(filename)
        header = read_header(filename)
        status = "通过" if header == expected_header else "不一致"
        lines.append(f"| `{filename}` | {len(rows)} | {status} | `{', '.join(header)}` |")

    lines.extend(
        [
            "",
            "## 研究输出 CSV",
            "",
            "| 文件 | 行数 | 字段状态 | 用途 |",
            "|------|------|----------|------|",
        ]
    )
    usage = {
        "predicate_matrix.csv": "事件-谓词矩阵",
        "event_forward_returns.csv": "事件后收益对齐与未来函数审计",
        "rules.csv": "候选规则排序",
        "factors.csv": "事件级因子值",
        "factor_snapshot.csv": "Demo 截面展示",
        "group_returns.csv": "分组收益展示",
        "rank_ic_timeseries.csv": "Rank IC 时序展示",
        "backtest_metrics.csv": "报告和 Demo 指标卡",
    }
    for filename, expected_header in RESEARCH_SCHEMAS.items():
        rows = read_csv(filename)
        header = read_header(filename)
        status = "通过" if header == expected_header else "不一致"
        lines.append(f"| `{filename}` | {len(rows)} | {status} | {usage[filename]} |")

    lines.extend(
        [
            "",
            "## 联调检查顺序",
            "",
            "1. C 先按字符串读取 `stock_code`、`doc_id`、`event_id`，避免前导零丢失。",
            "2. 真实文本开始写入后，只使用 `.venv/bin/python run_pipeline.py --preserve-inputs` 或 `run_b_pipeline.py --skip-sample-generation` 复跑。",
            "3. 先跑 `scripts/validate_b_data.py`，再跑 `scripts/validate_research_outputs.py`。",
            "4. 检查 `event_forward_returns.csv` 中 `entry_trade_date` 是否严格晚于 `event_time`。",
            "5. 检查 `factors.csv` 的 `trigger_event_ids` 和 `trigger_rule_ids` 是否能回溯到事件和规则。",
            "6. 替换真实行情后必须重新生成 `event_forward_returns.csv`、`rules.csv`、`factors.csv` 和报告。",
            "",
        ]
    )
    write_text(VIEW_DIR / "C联调数据契约清单.md", lines)


def prepare_manual_verification_guide() -> None:
    lines = [
        "# AlphaLens 人工核验操作手册",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 核验目标",
        "",
        "把当前自动生成的候选摘要替换或确认成可追溯、可解释、可复核的正式样本文本，同时不改变 B↔C CSV 字段契约。",
        "",
        "## P0 核验顺序",
        "",
        "第一批真实文本目标：政策 20 条、公告 20 条、新闻 20 条、互动问答 10 条。进度可用 `查看材料/真实文本核验进度.md` 查看。",
        "",
        "1. 打开 `查看材料/源文本核验队列.csv`，先处理 `needs_manual_verification=true` 且 `verification_priority=P0` 的行。",
        "2. 对政策文本，优先到财政部、工信部、发改委、国家能源局官网核对标题、日期、正文和 URL。",
        "3. 对公告文本，优先到巨潮资讯网或交易所公告页面核对公告标题、发布日期和公司主体。",
        "4. 对互动问答，到互动易或上证 e 互动核对问答日期、问题、回复和公司简称。",
        "5. 对新闻摘要，只保留可确认来源、日期和事实链条的摘要，避免粘贴大段版权文本。",
        "6. 在 `data/sample/raw_documents.csv` 中按 `doc_id` 找到对应行，保留 `doc_id` 和 `source_type`，只替换 `title`、`content`、`publish_time`、`source_name`、`url`。",
        "7. URL 必须是正文、公告或问答详情页，不能只是网站首页；日期使用信息首次公开日期，格式 `YYYY-MM-DD`。",
        "8. `content` 建议包含“原文摘要”和明确标记的“项目关联”，不要编造公司关联、收益判断或股价方向。",
        "9. 每替换一批文本后运行 `.venv/bin/python run_pipeline.py --preserve-inputs`，确认字段、事件、谓词和报告仍能通过校验。",
        "10. 真实文本写入后不要使用 `--force-sample-generation`，否则会把输入重置为 Demo 候选摘要。",
        "",
        "## 每条文本要核验的项目",
        "",
        "| 项目 | 通过标准 | 写入位置 |",
        "|------|----------|----------|",
        "| 标题 | 与原始来源含义一致，可适度摘要 | `raw_documents.csv/title` |",
        "| 正文 | 足够支撑事件抽取，长度不少于 50 字符 | `raw_documents.csv/content` |",
        "| 发布日期 | 原始来源发布日期，格式 `YYYY-MM-DD` | `raw_documents.csv/publish_time` |",
        "| 来源名称 | 政府部门、公司、媒体或互动平台名称 | `raw_documents.csv/source_name` |",
        "| URL | 可访问或可追溯的原文链接 | `raw_documents.csv/url` |",
        "| 主体关联 | 文本确实涉及股票池公司或其主营链条 | `entity_links.csv` / `events.csv` |",
        "",
        "## 人工抽检记录",
        "",
        "- 事件抽检写入 `查看材料/事件人工抽检样本.csv` 的 `manual_review_result` 和 `manual_comment`。",
        "- 谓词抽检写入 `查看材料/谓词人工抽检样本.csv` 的 `manual_review_result` 和 `manual_comment`。",
        "- 建议 `manual_review_result` 使用 `pass`、`revise`、`drop` 三类值。",
        "- 可运行 `.venv/bin/python scripts/validate_manual_review_results.py` 校验取值是否合法；`revise` / `drop` 建议写清修改原因。",
        "",
        "## 不可越界",
        "",
        "- 不改变 `参考文档/数据格式规范.md` 中锁定字段名。",
        "- 不把 `data/raw/`、`data/processed/`、`data/external/` 加入 git。",
        "- 不把当前候选行情结果写成正式实证结论。",
        "- 不删除免责声明：本报告仅供研究参考，不构成投资建议。",
        "",
    ]
    write_text(VIEW_DIR / "人工核验操作手册.md", lines)


def prepare_view_material_index() -> None:
    files = [
        ("任务进度.md", "当前 B 线自动任务进度，本地维护且不提交"),
        ("人工待办.md", "必须人工完成或确认的事项，本地维护且不提交"),
        ("用户参与工作推进手册.md", "用户继续推进人工抽查、行情复核、A/C 确认和 PPT 检查的详细步骤"),
        ("A口径确认建议稿.md", "给 A 确认事件类型、谓词和表述边界的建议稿"),
        ("C联调运行手册.md", "给 C 复跑流水线、检查输出和定位问题的运行手册"),
        ("Demo演示脚本.md", "Streamlit Demo 演示顺序和讲解词草稿"),
        ("答辩问答素材.md", "围绕项目定位、数据、谓词、因子和回测的答辩问答素材"),
        ("人工核验操作手册.md", "人工核验真实来源文本和抽检事件/谓词的操作步骤"),
        ("真实数据替换验收清单.md", "人工替换真实文本和行情后的复跑、校验和验收步骤"),
        ("真实文本来源获取记录.md", "联网获取真实文本来源、替换数量和来源池记录"),
        ("真实文本核验进度.md", "真实来源文本核验完成数量、来源分布和 P0 剩余统计"),
        ("真实文本核验进度.csv", "真实文本核验进度的机器可读汇总表"),
        ("真实行情获取记录.md", "东方财富前复权行情获取参数、覆盖范围和注意事项"),
        ("真实行情导入模板.csv", "真实前复权行情导入字段模板"),
        ("真实行情校验报告.md", "当前行情文件的独立结构校验报告"),
        ("流水线输入保护验证报告.md", "安全模式复跑不覆盖 raw_documents.csv 的哈希验证报告"),
        ("人工抽检结果校验报告.md", "事件/谓词人工抽检结果 pass/revise/drop 合法值校验报告"),
        ("源文本核验队列.csv", "120 条文本的来源核验队列和优先级"),
        ("事件人工抽检样本.csv", "事件抽取人工抽检表"),
        ("谓词人工抽检样本.csv", "谓词判断人工抽检表"),
        ("案例索引.csv", "全量事件案例索引，便于挑 PPT 案例"),
        ("解释案例草稿.md", "可读版解释案例，含事件、谓词和当前收益路径"),
        ("PPT案例素材包.md", "PPT 可复用案例素材"),
        ("C联调数据契约清单.md", "给 C 检查字段、行数和联调顺序"),
        ("数据质量报告.md", "B 线核心 CSV 质量报告"),
        ("数据完整性检查报告.md", "样例数据和研究输出完整性概览"),
        ("未来函数审计明细.md", "事件收益对齐和未来函数审计"),
        ("交付包自检报告.md", "参考文档、查看材料、数据附件和 gitignore 的自动自检结果"),
        ("因子研究报告.md", "自动生成的研究报告初稿"),
        ("B线交付检查清单.md", "B 线交付前检查项"),
        ("B线阶段0进展.md", "早期阶段推进记录"),
        ("自动推进进展记录.md", "自动推进历史记录"),
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
        "3. `用户参与工作推进手册.md`：按步骤推进人工抽查、行情复核、A/C 确认和 PPT 检查。",
        "4. `A口径确认建议稿.md`：和 A 确认事件、谓词、对外表达边界前看。",
        "5. `人工核验操作手册.md`：开始核验真实文本前看。",
        "6. `真实文本来源获取记录.md` / `真实文本核验进度.md`：查看联网替换结果和剩余 P0 数量。",
        "7. `真实行情获取记录.md` / `真实行情导入模板.csv` / `真实行情校验报告.md`：替换真实前复权行情前后看。",
        "8. `流水线输入保护验证报告.md`：确认安全模式不会覆盖真实文本。",
        "9. `C联调数据契约清单.md` / `C联调运行手册.md`：和 C 对接字段、行数、回测输入时看。",
        "10. `Demo演示脚本.md` / `答辩问答素材.md`：准备演示和答辩前看。",
        "11. `解释案例草稿.md` / `PPT案例素材包.md`：准备展示材料时看。",
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


def prepare_real_data_acceptance_checklist() -> None:
    lines = [
        "# AlphaLens 真实数据替换验收清单",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 适用时机",
        "",
        "当人工完成真实来源文本核验、真实前复权行情替换、事件/谓词抽检修正后，用本清单验收 B 线数据能否交给 A/C 使用。",
        "",
        "## 替换前备份",
        "",
        "- 保留当前 Demo 数据副本或通过 git diff 确认变更范围。",
        "- 不把大体量原始材料放入 `data/raw/`、`data/processed/`、`data/external/` 后提交。",
        "- 不改变 `参考文档/数据格式规范.md` 锁定字段名和字段顺序。",
        "",
        "## 文本替换验收",
        "",
        "| 检查项 | 通过标准 | 工具/文件 |",
        "|--------|----------|-----------|",
        "| 文本数量 | `raw_documents.csv` 不少于 100 行 | `查看材料/源文本核验队列.csv` |",
        "| 来源覆盖 | policy / announcement / news / ir_qa 均有样本 | `查看材料/数据质量报告.md` |",
        "| 日期范围 | `publish_time` 在 2024-01-01 至 2026-06-30 | `scripts/validate_b_data.py` |",
        "| 文本长度 | `content` 不少于 50 字符 | `scripts/validate_b_data.py` |",
        "| URL 可追溯 | P0 样本 URL 指向正文、公告 PDF 或问答详情页 | `查看材料/真实文本来源获取记录.md` / `查看材料/人工核验操作手册.md` |",
        "| 进度统计 | 第一批目标剩余量可解释 | `查看材料/真实文本核验进度.md` |",
        "",
        "## 行情替换验收",
        "",
        "| 检查项 | 通过标准 | 工具/文件 |",
        "|--------|----------|-----------|",
        "| 股票覆盖 | 30 只股票均有行情 | `scripts/validate_b_data.py` |",
        "| 日期覆盖 | 覆盖 2024-01-01 至 2026-06-30 的交易窗口 | `market_data.csv` |",
        "| OHLC 合法 | high 不低于开收低，low 不高于开收高 | `scripts/validate_b_data.py` |",
        "| 导入字段 | 字段顺序与模板一致 | `查看材料/真实行情导入模板.csv` / `scripts/validate_real_market_data.py` |",
        "| 前复权口径 | open/high/low/close 来源说明清楚，adj_factor 口径已复核 | `查看材料/真实行情获取记录.md` / 人工确认数据供应商说明 |",
        "| 未来函数 | 入场日严格晚于事件日 | `查看材料/未来函数审计明细.md` |",
        "",
        "## 必跑命令",
        "",
        "```bash",
        ".venv/bin/python run_pipeline.py --preserve-inputs",
        ".venv/bin/python scripts/validate_input_preservation.py",
        ".venv/bin/python scripts/report_manual_verification_progress.py",
        ".venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv",
        ".venv/bin/python scripts/validate_manual_review_results.py",
        ".venv/bin/python scripts/validate_b_data.py",
        ".venv/bin/python scripts/validate_research_outputs.py",
        ".venv/bin/python scripts/validate_delivery_package.py",
        "```",
        "",
        "## 验收通过标准",
        "",
        "- `validate_b_data.py` 输出 `errors=0`。",
        "- `validate_real_market_data.py` 输出 `market_data_errors=0`。",
        "- `validate_manual_review_results.py` 输出 `manual_review_errors=0`。",
        "- `validate_research_outputs.py` 输出 `research_output_errors=0`。",
        "- `validate_delivery_package.py` 输出 `delivery_errors=0`。",
        "- `查看材料/因子研究报告.md`、`查看材料/解释案例草稿.md` 和 `查看材料/PPT案例素材包.md` 已用真实行情重刷。",
        "- `查看材料/事件人工抽检样本.csv` 与 `查看材料/谓词人工抽检样本.csv` 的人工结论已处理完 `revise` / `drop` 项。",
        "",
        "## 仍需人工确认",
        "",
        "- A 确认事件类型和谓词 schema 的金融口径。",
        "- C 确认真实行情读取、收益对齐和回测口径。",
        "- A 检查 PPT 中引用的数字与最终 CSV、报告一致。",
        "",
    ]
    write_text(VIEW_DIR / "真实数据替换验收清单.md", lines)


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
            "2. `capacity_expansion` 是否包括海外认证、交付能力、募投项目推进等间接扩产表述。",
            "3. `investor_question_pressure` 是否应该视为关注度信号，还是偏风险/不确定性信号。",
            "4. `social_attention_spikes` 的判断边界是否接受自动规则中的多源报道、政策专项行动、互动问答集中。",
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
    lines = [
        "# AlphaLens C 联调运行手册",
        "",
        f"生成日期：{today()}",
        "",
        DISCLAIMER,
        "",
        "## 目标",
        "",
        "帮助 C 在不改 B 线 CSV 字段契约的前提下，复跑流水线、检查研究输出、定位常见问题，并在真实行情替换后完成联调。",
        "",
        "## 推荐运行顺序",
        "",
        "```bash",
        ".venv/bin/python run_pipeline.py --preserve-inputs",
        ".venv/bin/python scripts/validate_input_preservation.py",
        ".venv/bin/python scripts/validate_b_data.py",
        ".venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv",
        ".venv/bin/python scripts/validate_manual_review_results.py",
        ".venv/bin/python scripts/validate_research_outputs.py",
        ".venv/bin/python scripts/validate_delivery_package.py",
        "```",
        "",
        "真实文本或真实行情开始写入后，不要使用 `--force-sample-generation`。如只重算 B 线中间表，用 `.venv/bin/python run_b_pipeline.py --skip-sample-generation`。",
        "",
        "## 当前关键指标",
        "",
        "| 指标 | 当前值 | 说明 |",
        "|------|--------|------|",
    ]
    metric_descriptions = {
        row["metric"]: row["description"] for row in metric_rows
    }
    for metric, value in sorted(metrics.items()):
        lines.append(f"| `{metric}` | {value} | {metric_descriptions.get(metric, '')} |")

    lines.extend(
        [
            "",
            "## C 侧重点",
            "",
            "- 读取 `stock_code`、`doc_id`、`event_id` 时必须保留字符串，避免前导零丢失。",
            "- `event_forward_returns.csv` 的 `entry_trade_date` 必须严格晚于 `event_time`。",
            "- `factors.csv` 中 `trigger_event_ids` 和 `trigger_rule_ids` 必须能回溯到 `events.csv` 和 `rules.csv`。",
            "- 替换真实行情后，需要重新生成收益、规则、因子、报告和 Demo 展示数据。",
            "- 不改变 `参考文档/数据格式规范.md` 中锁定字段名。",
            "",
            "## 常见问题定位",
            "",
            "| 现象 | 优先检查 |",
            "|------|----------|",
            "| 股票代码变成 274 或 2594 | CSV 读取时是否把 `stock_code` 当数字读入 |",
            "| 未来函数审计失败 | `entry_trade_date` 是否不晚于 `event_time` |",
            "| 因子没有触发规则 | 谓词是否缺失 MVP 字段，或规则支持数低于 5 |",
            "| Demo 报告缺失 | `查看材料/因子研究报告.md` 是否已生成 |",
            "| 交付包自检失败 | 先打开 `查看材料/交付包自检报告.md` 看 Errors |",
            "",
        ]
    )
    write_text(VIEW_DIR / "C联调运行手册.md", lines)


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
        "| 页面 | 讲解重点 | 建议话术 |",
        "|------|----------|----------|",
        "| Pipeline Overview | 展示文本到因子的完整流程 | 先让评委看到系统不是黑箱预测，而是分层结构化和审计 |",
        "| Input Data | 展示股票池和文本样本 | 数据覆盖新能源多个细分方向，文本已联网替换为真实来源候选版 |",
        "| Event Extraction | 展示事件卡片 | 文本先被约束成事件类型、主体、客体、影响路径和证据片段 |",
        "| Predicates & Rules | 展示谓词矩阵和规则触发 | 谓词是金融事件的标准化判断题，规则是可解释研究假设 |",
        "| Factor Ranking | 展示因子排序 | 因子值来自规则触发和证据强度，不是主观打分 |",
        "| Backtest Dashboard | 展示指标和审计 | 强调入场日严格晚于事件日，避免未来函数 |",
        "| Research Report | 展示自动报告 | 报告用于研究参考，不构成投资建议 |",
        "",
        "## 收尾 20 秒",
        "",
        "这个 Demo 证明了从非结构化舆情文本到结构化因子研究信号的链路可跑通。正式提交前，我们会人工抽查真实文本来源、复核前复权行情口径，并刷新报告和展示数字。",
        "",
    ]
    write_text(VIEW_DIR / "Demo演示脚本.md", lines)


def prepare_defense_qa_material() -> None:
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
        "当前文本已联网替换为可追溯来源候选版，用于验证字段契约和系统链路。正式版仍需要按 `查看材料/源文本核验队列.csv` 完成人工抽查确认。",
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
        "不能。当前行情已联网获取前复权价格候选版，但仍需要人工复核数据源和 `adj_factor` 口径。回测结果只能作为研究链路验证，不能作为投资结论。",
        "",
        "### Q8：如果真实回测效果一般，项目还有价值吗？",
        "",
        "有。项目价值不只在单个规则收益，而在于提供一套可复用的文本因子研究工作流：从文本结构化、规则候选生成、因子构造到回测审计，都能提升研究效率。",
        "",
        "## 交付计划",
        "",
        "### Q9：下一步最关键的工作是什么？",
        "",
        "第一是核验真实文本来源，第二是替换真实前复权行情，第三是由 A 确认事件和谓词口径，第四是与 C 联调真实数据回测。",
        "",
    ]
    write_text(VIEW_DIR / "答辩问答素材.md", lines)


def main() -> None:
    prepare_source_verification_queue()
    prepare_manual_review_samples()
    prepare_market_data_import_template()
    prepare_future_info_audit()
    prepare_completeness_report()
    prepare_ppt_case_pack()
    prepare_delivery_checklist()
    prepare_case_index()
    prepare_contract_checklist()
    prepare_a_schema_confirmation_brief()
    prepare_c_runbook()
    prepare_demo_script()
    prepare_defense_qa_material()
    prepare_manual_verification_guide()
    prepare_real_data_acceptance_checklist()
    prepare_view_material_index()
    print("B handoff materials prepared.")


if __name__ == "__main__":
    main()
