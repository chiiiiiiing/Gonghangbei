"""Auditable RIFT-style research for carbonate-lithium futures.

The module keeps the transferable parts of RIFT: a fixed Boolean predicate
schema, contrastive source contexts, one/two-predicate rules, coverage scoring,
a frozen top-k rulebook, and rule-enhanced LLM inference.  It deliberately does
not manufacture market history when controlled CSV inputs are absent.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from src.ai.gateway import OpenAICompatibleGateway


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
RESEARCH_BOUNDARY = "研究对象为碳酸锂期货价格方向与策略增量，不提供买卖建议、目标价或收益保证"
DISCOVERY_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 12, 31)
OOS_START = date(2026, 1, 1)
PROSPECTIVE_FREEZE_DATE = date(2026, 8, 14)
PROSPECTIVE_START = date(2026, 8, 14)
PROSPECTIVE_TEXT_WEIGHT = 1.0
BACKTEST_ENGINE_VERSION = "lithium-backtest-v3-decision-ledger-20260814"
HORIZON_DAYS = 5
LABEL_THRESHOLD = 0.01
MIN_RULE_DOCUMENTS = 5
MIN_RULE_DATES = 3
TOP_K_RULES = 5
RULE_LAMBDA = 1.0
PRIMARY_STRATEGIES = ("pure_trend", "zero_shot_llm", "rift_enhanced_trend")
PROSPECTIVE_STRATEGY = "prospective_rule_confirmed_trend"
PROSPECTIVE_DECISION_FILE = "lithium_prospective_decisions.csv"

TEXT_FIELDS = [
    "doc_id", "source_type", "title", "content", "publish_time",
    "source_name", "url", "review_status",
]
CONTRACT_FIELDS = [
    "trade_date", "contract", "open", "high", "low", "close", "settlement",
    "volume", "open_interest", "source_name", "source_url",
]
WAREHOUSE_FIELDS = [
    "trade_date", "variety", "warehouse_receipt", "change", "source_name", "source_url",
]
SIGNAL_FIELDS = [
    "doc_id", "publish_time", "direction_label", "direction_score", "zero_shot_score",
    "confidence", "horizon_days", "activated_rules", "predicate_consensus",
    "evidence_text", "inference_mode", "model", "request_id",
]
RULE_FIELDS = [
    "rule_id", "target_label", "conditions", "score", "coverage_positive",
    "coverage_negative", "support_documents", "support_dates", "status",
]

PREDICATE_DEFINITIONS: dict[str, str] = {
    "supply_disruption": "矿山、盐湖、冶炼或物流出现减产、停产、事故、环保约束等供应扰动。",
    "supply_expansion": "新增、扩建或爬坡产能将增加可交付锂盐供应。",
    "production_resumption": "此前受限产能明确复产或恢复交付。",
    "demand_ev_positive": "新能源汽车产销、排产或动力电池需求出现可核验的正向变化。",
    "demand_storage_positive": "储能装机、招标或电芯排产出现可核验的正向变化。",
    "demand_weak_or_price_war": "终端需求转弱、去库压力或价格战压制锂盐采购。",
    "inventory_drawdown": "产业库存出现可核验下降。",
    "inventory_build": "产业库存出现可核验累积。",
    "warehouse_receipt_decline": "广期所碳酸锂仓单数量下降。",
    "warehouse_receipt_increase": "广期所碳酸锂仓单数量上升。",
    "policy_demand_support": "政策直接支持新能源汽车、动力电池或储能需求。",
    "import_supply_pressure": "进口锂矿或锂盐增长带来供应压力。",
    "cost_support": "矿端、化工或加工成本上升形成价格成本支撑。",
    "delivery_pressure": "临近交割、仓单集中或可交割货源增加形成盘面压力。",
    "authoritative_source": "来源为政府、交易所、上市公司公告或其他可核验一手来源。",
    "quantitative_evidence": "文本包含与判断直接相关的日期、数量、产能、销量或同比数据。",
    "uncertainty_high": "文本明确存在审批、执行、口径、传闻或时间上的重大不确定性。",
}

_POSITIVE_KEYWORDS = {
    "supply_disruption": ("停产", "减产", "事故", "检修", "环保限产", "供应中断"),
    "supply_expansion": ("扩产", "新增产能", "投产", "产能释放", "产量增长"),
    "production_resumption": ("复产", "恢复生产", "恢复供应"),
    "demand_ev_positive": ("新能源汽车销量增长", "电动车销量增长", "动力电池排产增长", "以旧换新"),
    "demand_storage_positive": ("储能装机增长", "储能招标", "储能电池排产", "新型储能规模化"),
    "demand_weak_or_price_war": ("需求疲弱", "需求下降", "价格战", "减产去库", "订单下滑"),
    "inventory_drawdown": ("库存下降", "去库存", "库存去化"),
    "inventory_build": ("库存增加", "库存累积", "库存上升", "累库"),
    "warehouse_receipt_decline": ("仓单减少", "仓单下降", "注销仓单"),
    "warehouse_receipt_increase": ("仓单增加", "仓单上升", "注册仓单"),
    "policy_demand_support": ("购置税减免", "消费补贴", "以旧换新", "储能规模化", "政策支持"),
    "import_supply_pressure": ("进口增长", "进口量增加", "到港增加"),
    "cost_support": ("成本上升", "矿价上涨", "加工费上涨", "成本支撑"),
    "delivery_pressure": ("交割压力", "可交割货源", "仓单集中", "临近交割"),
    "uncertainty_high": ("尚需审批", "存在不确定性", "传闻", "预计", "可能", "风险提示"),
}
_AUTHORITATIVE_TERMS = (
    "国务院", "国家发展改革委", "国家能源局", "工业和信息化部", "广期所",
    "广州期货交易所", "上市公司", "公告", "政府网", "交易所",
)


def _read_csv(name: str, directory: Path = SAMPLE_DIR) -> list[dict[str, str]]:
    path = directory / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(name: str, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path = SAMPLE_DIR / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _parse_day(value: str, field: str) -> date:
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field} 必须使用 YYYY-MM-DD") from exc


def _float(value: Any, field: str, *, nonnegative: bool = False, positive: bool = False) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} 不能是 NaN 或无穷值")
    if positive and number <= 0:
        raise ValueError(f"{field} 必须大于 0")
    if nonnegative and number < 0:
        raise ValueError(f"{field} 不能小于 0")
    return number


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_controlled_data(
    texts: list[dict[str, str]],
    contracts: list[dict[str, str]],
    warehouse: list[dict[str, str]],
) -> list[str]:
    """Return all data errors so an import can be fixed in one pass."""
    errors: list[str] = []
    seen_docs: set[str] = set()
    for index, row in enumerate(texts, 2):
        prefix = f"lithium_texts.csv 第 {index} 行"
        missing = [field for field in TEXT_FIELDS if field not in row]
        if missing:
            errors.append(f"{prefix} 缺少字段: {', '.join(missing)}")
            continue
        doc_id = row["doc_id"].strip()
        if not doc_id or doc_id in seen_docs:
            errors.append(f"{prefix} doc_id 为空或重复")
        seen_docs.add(doc_id)
        try:
            _parse_day(row["publish_time"], "publish_time")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        if not row["title"].strip() or not row["content"].strip() or not row["source_name"].strip():
            errors.append(f"{prefix} 标题、正文和来源名称不能为空")
        if not _valid_url(row["url"].strip()):
            errors.append(f"{prefix} 必须包含有效 HTTP(S) 来源 url")
        if row["review_status"].strip() not in {"accepted", "pending_review", "rejected"}:
            errors.append(f"{prefix} review_status 必须为 accepted/pending_review/rejected")

    seen_contracts: set[tuple[str, str]] = set()
    previous_contract_day: date | None = None
    for index, row in enumerate(contracts, 2):
        prefix = f"lithium_contract_daily.csv 第 {index} 行"
        missing = [field for field in CONTRACT_FIELDS if field not in row]
        if missing:
            errors.append(f"{prefix} 缺少字段: {', '.join(missing)}")
            continue
        day: date | None = None
        try:
            day = _parse_day(row["trade_date"], "trade_date")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        prices: dict[str, float] = {}
        for field in ("open", "high", "low", "close", "settlement"):
            try:
                prices[field] = _float(row[field], field, positive=True)
            except ValueError as exc:
                errors.append(f"{prefix}: {exc}")
        if len(prices) == 5:
            if prices["high"] < max(prices["open"], prices["low"], prices["close"]):
                errors.append(f"{prefix} high 不能低于 open/low/close")
            if prices["low"] > min(prices["open"], prices["high"], prices["close"]):
                errors.append(f"{prefix} low 不能高于 open/high/close")
        for field in ("volume", "open_interest"):
            if field == "open_interest" and not row[field].strip():
                continue
            try:
                _float(row[field], field, nonnegative=True)
            except ValueError as exc:
                errors.append(f"{prefix}: {exc}")
        contract = row["contract"].strip().upper()
        if not re.fullmatch(r"LC\d{4}", contract):
            errors.append(f"{prefix} contract 必须匹配 LC+4位合约月份")
        key = (row["trade_date"], contract)
        if key in seen_contracts:
            errors.append(f"{prefix} trade_date+contract 重复")
        seen_contracts.add(key)
        if day and previous_contract_day and day < previous_contract_day:
            errors.append(f"{prefix} 未按 trade_date 升序排列")
        if day:
            previous_contract_day = day
        if not row["source_name"].strip() or not _valid_url(row["source_url"].strip()):
            errors.append(f"{prefix} 必须包含来源名称和有效 source_url")

    seen_warehouse: set[tuple[str, str]] = set()
    previous_warehouse_day: date | None = None
    for index, row in enumerate(warehouse, 2):
        prefix = f"lithium_warehouse_receipts.csv 第 {index} 行"
        missing = [field for field in WAREHOUSE_FIELDS if field not in row]
        if missing:
            errors.append(f"{prefix} 缺少字段: {', '.join(missing)}")
            continue
        day: date | None = None
        try:
            day = _parse_day(row["trade_date"], "trade_date")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        try:
            _float(row["warehouse_receipt"], "warehouse_receipt", nonnegative=True)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        try:
            _float(row["change"], "change")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        key = (row["trade_date"], row["variety"].strip())
        if key in seen_warehouse:
            errors.append(f"{prefix} trade_date+variety 重复")
        seen_warehouse.add(key)
        if day and previous_warehouse_day and day < previous_warehouse_day:
            errors.append(f"{prefix} 未按 trade_date 升序排列")
        if day:
            previous_warehouse_day = day
        if not row["source_name"].strip() or not _valid_url(row["source_url"].strip()):
            errors.append(f"{prefix} 必须包含来源名称和有效 source_url")
    return errors


def text_provenance_report(
    texts: list[dict[str, str]],
    audit_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Verify that every controlled text has a matching immutable audit row."""
    errors: list[str] = []
    by_doc: dict[str, dict[str, str]] = {}
    duplicate_audit_ids: set[str] = set()
    for row in audit_rows:
        doc_id = row.get("doc_id", "").strip()
        if not doc_id:
            errors.append("lithium_text_fetch_audit.csv 存在空 doc_id")
            continue
        if doc_id in by_doc:
            duplicate_audit_ids.add(doc_id)
        by_doc[doc_id] = row
    if duplicate_audit_ids:
        errors.append(
            "lithium_text_fetch_audit.csv doc_id 重复: "
            + ", ".join(sorted(duplicate_audit_ids))
        )

    quality_counts: defaultdict[str, int] = defaultdict(int)
    text_ids: set[str] = set()
    for text in texts:
        doc_id = text.get("doc_id", "").strip()
        text_ids.add(doc_id)
        audit = by_doc.get(doc_id)
        if audit is None:
            errors.append(f"{doc_id or '<empty>'} 缺少文本来源审计记录")
            continue
        expected_hash = hashlib.sha256(text.get("content", "").encode("utf-8")).hexdigest()
        if audit.get("content_sha256", "").strip() != expected_hash:
            errors.append(f"{doc_id} 正文 SHA-256 与审计记录不一致")
        if audit.get("url", "").strip() != text.get("url", "").strip():
            errors.append(f"{doc_id} 来源 URL 与审计记录不一致")
        if audit.get("source_name", "").strip() != text.get("source_name", "").strip():
            errors.append(f"{doc_id} 来源名称与审计记录不一致")
        try:
            audited_chars = int(audit.get("selected_chars", ""))
        except ValueError:
            audited_chars = -1
        if audited_chars != len(text.get("content", "")):
            errors.append(f"{doc_id} 正文长度与审计记录不一致")

        provenance = audit.get("provenance", "")
        fetch_status = audit.get("fetch_status", "")
        if provenance.startswith("gfex_warehouse_receipt_structured_fact:"):
            quality_counts["derived_official_fact"] += 1
            if fetch_status != "derived_from_audited_official_json":
                errors.append(f"{doc_id} 仓单事实缺少官方 JSON 派生标记")
        elif provenance.startswith("verified_repository_input:"):
            base_status = fetch_status
            while base_status.startswith("reused_audited_corpus:"):
                base_status = base_status.split(":", 1)[1]
            if base_status == "ok":
                quality_counts["fetched_full"] += 1
            elif base_status == "partial":
                quality_counts["fetched_partial"] += 1
            elif base_status == "failed":
                quality_counts["repository_snapshot_only"] += 1
            else:
                quality_counts["unknown"] += 1
                errors.append(f"{doc_id} 包含未知抓取状态: {fetch_status or '<empty>'}")
        else:
            quality_counts["unknown"] += 1
            errors.append(f"{doc_id} 包含未知 provenance: {provenance or '<empty>'}")

    orphan_audit = sorted(set(by_doc) - text_ids)
    if orphan_audit:
        errors.append(f"文本审计表存在 {len(orphan_audit)} 条无对应正文记录")
    return {
        "verified": not errors,
        "audited_documents": len(texts) - sum(1 for row in texts if row.get("doc_id", "") not in by_doc),
        "quality_counts": dict(sorted(quality_counts.items())),
        "errors": errors,
    }


def build_main_continuous(contracts: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Select each day's dominant contract using only same-day information."""
    grouped: dict[date, list[dict[str, str]]] = defaultdict(list)
    for row in contracts:
        grouped[_parse_day(row["trade_date"], "trade_date")].append(row)
    result: list[dict[str, Any]] = []
    previous = ""
    for day in sorted(grouped):
        candidates = grouped[day]
        has_oi = any(str(row.get("open_interest", "")).strip() for row in candidates)
        if has_oi:
            selected = max(
                candidates,
                key=lambda row: (_float(row.get("open_interest") or 0, "open_interest"), _float(row["volume"], "volume"), row["contract"]),
            )
            basis = "same_day_open_interest"
        else:
            selected = max(candidates, key=lambda row: (_float(row["volume"], "volume"), row["contract"]))
            basis = "same_day_volume_fallback"
        contract = selected["contract"].strip().upper()
        result.append({
            "trade_date": day.isoformat(),
            "contract": contract,
            "open": _float(selected["open"], "open", positive=True),
            "high": _float(selected["high"], "high", positive=True),
            "low": _float(selected["low"], "low", positive=True),
            "close": _float(selected["close"], "close", positive=True),
            "settlement": _float(selected["settlement"], "settlement", positive=True),
            "volume": _float(selected["volume"], "volume", nonnegative=True),
            "open_interest": _float(selected.get("open_interest") or 0, "open_interest", nonnegative=True),
            "selection_basis": basis,
            "rolled": bool(previous and previous != contract),
            "source_name": selected["source_name"],
            "source_url": selected["source_url"],
        })
        previous = contract
    return result


def forward_label(
    publish_time: str,
    continuous: list[dict[str, Any]],
    horizon: int = HORIZON_DAYS,
    contracts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    publish_day = _parse_day(publish_time, "publish_time")
    entry_index = next((index for index, row in enumerate(continuous) if _parse_day(row["trade_date"], "trade_date") > publish_day), None)
    if entry_index is None or entry_index + horizon >= len(continuous):
        return None
    entry = continuous[entry_index]
    exit_row = continuous[entry_index + horizon]
    contract_lookup = {
        (row["trade_date"], row["contract"].strip().upper()): row
        for row in (contracts or [])
    }
    known_index = entry_index - 1
    selected_contract = str(continuous[known_index]["contract"]) if known_index >= 0 else str(entry["contract"])
    contract_entry = contract_lookup.get((entry["trade_date"], selected_contract))
    contract_exit = contract_lookup.get((exit_row["trade_date"], selected_contract))
    if contract_lookup and (contract_entry is None or contract_exit is None):
        return None
    if contract_entry is not None and contract_exit is not None:
        forward_return = _float(contract_exit["open"], "open", positive=True) / _float(contract_entry["open"], "open", positive=True) - 1.0
    else:
        forward_return = float(exit_row["open"]) / float(entry["open"]) - 1.0
    label = "bullish" if forward_return >= LABEL_THRESHOLD else "bearish" if forward_return <= -LABEL_THRESHOLD else "neutral"
    return {
        "entry_trade_date": entry["trade_date"],
        "exit_trade_date": exit_row["trade_date"],
        "forward_open_return": forward_return,
        "direction_label": label,
        "horizon_days": horizon,
        "future_info_ok": entry["trade_date"] > publish_day.isoformat(),
    }


def deterministic_predicates(document: dict[str, str]) -> dict[str, dict[str, Any]]:
    text = f"{document.get('title', '')}\n{document.get('content', '')}"
    source = document.get("source_name", "")
    result: dict[str, dict[str, Any]] = {}
    for name in PREDICATE_DEFINITIONS:
        if name == "authoritative_source":
            evidence = next((term for term in _AUTHORITATIVE_TERMS if term in f"{source} {text}"), "")
        elif name == "quantitative_evidence":
            match = re.search(r"\d+(?:\.\d+)?(?:%|万吨|吨|亿元|万辆|GWh|GW|套|家|个)", text, re.IGNORECASE)
            evidence = match.group(0) if match else ""
        else:
            evidence = next((term for term in _POSITIVE_KEYWORDS.get(name, ()) if term in text), "")
        result[name] = {"value": bool(evidence), "evidence_text": evidence, "confidence": 0.70 if evidence else 0.65}
    return result


def predicate_consensus(
    deterministic: dict[str, dict[str, Any]],
    ai_predicates: list[dict[str, Any]],
    source_text: str,
) -> list[dict[str, Any]]:
    by_name = {str(row.get("name", "")): row for row in ai_predicates if isinstance(row, dict)}
    if set(by_name) != set(PREDICATE_DEFINITIONS):
        missing = sorted(set(PREDICATE_DEFINITIONS) - set(by_name))
        extra = sorted(set(by_name) - set(PREDICATE_DEFINITIONS))
        raise ValueError(f"LLM 谓词 Schema 不完整；缺少 {missing}，多出 {extra}")
    rows: list[dict[str, Any]] = []
    for name in PREDICATE_DEFINITIONS:
        ai = by_name[name]
        ai_value = ai.get("value")
        if not isinstance(ai_value, bool):
            raise ValueError(f"谓词 {name} 的 value 必须是 Boolean")
        evidence = str(ai.get("evidence_text", "")).strip()
        if ai_value and (not evidence or evidence not in source_text):
            raise ValueError(f"谓词 {name} 的证据文本无法回溯到输入原文")
        confidence = _float(ai.get("confidence", 0), f"{name}.confidence")
        if not 0 <= confidence <= 1:
            raise ValueError(f"谓词 {name} 的 confidence 必须在 0 到 1")
        deterministic_value = bool(deterministic[name]["value"])
        if deterministic_value and ai_value:
            status = "agreed_true"
        elif not deterministic_value and not ai_value:
            status = "agreed_false"
        else:
            status = "disputed"
        rows.append({
            "name": name,
            "deterministic_value": deterministic_value,
            "ai_value": ai_value,
            "status": status,
            "confidence": confidence,
            "evidence_text": evidence or deterministic[name]["evidence_text"],
        })
    return rows


def induce_rulebook(
    records: list[dict[str, Any]],
    *,
    min_documents: int = MIN_RULE_DOCUMENTS,
    min_dates: int = MIN_RULE_DATES,
    top_k: int = TOP_K_RULES,
    penalty_lambda: float = RULE_LAMBDA,
    anchor_predicates: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Induce global top-k bullish and bearish short rules from discovery only."""
    discovery = [
        row for row in records
        if _parse_day(str(row["publish_time"]), "publish_time") <= DISCOVERY_END
        and row.get("direction_label") in {"bullish", "bearish", "neutral"}
    ]
    predicates = list(PREDICATE_DEFINITIONS)
    candidates = [(name,) for name in predicates]
    candidates.extend(itertools.combinations(predicates, 2))
    if anchor_predicates is not None:
        candidates = [
            conditions for conditions in candidates
            if any(name in anchor_predicates for name in conditions)
        ]
    rules: list[dict[str, Any]] = []
    for target in ("bullish", "bearish"):
        positives = [row for row in discovery if row["direction_label"] == target]
        negatives = [row for row in discovery if row["direction_label"] != target]
        if not positives or not negatives:
            continue
        for conditions in candidates:
            matched = [row for row in discovery if all(row.get("predicate_status", {}).get(name) == "agreed_true" for name in conditions)]
            support_docs = {str(row["doc_id"]) for row in matched}
            support_dates = {str(row["publish_time"])[:10] for row in matched}
            if len(support_docs) < min_documents or len(support_dates) < min_dates:
                continue
            positive_hits = sum(all(row.get("predicate_status", {}).get(name) == "agreed_true" for name in conditions) for row in positives)
            negative_hits = sum(all(row.get("predicate_status", {}).get(name) == "agreed_true" for name in conditions) for row in negatives)
            coverage_positive = positive_hits / len(positives)
            coverage_negative = negative_hits / len(negatives)
            score = coverage_positive - penalty_lambda * coverage_negative
            if score <= 0:
                continue
            rules.append({
                "target_label": target,
                "conditions": list(conditions),
                "score": score,
                "coverage_positive": coverage_positive,
                "coverage_negative": coverage_negative,
                "support_documents": len(support_docs),
                "support_dates": len(support_dates),
                "status": "qualified",
            })
    selected: list[dict[str, Any]] = []
    target_prefix = {"bullish": "BULL", "bearish": "BEAR"}
    for target in ("bullish", "bearish"):
        ranked = sorted(
            (rule for rule in rules if rule["target_label"] == target),
            key=lambda rule: (-rule["score"], len(rule["conditions"]), -rule["support_documents"], tuple(rule["conditions"])),
        )[:top_k]
        for index, rule in enumerate(ranked, 1):
            selected.append({"rule_id": f"LC-{target_prefix[target]}-{index:02d}", **rule})
    return selected


def activated_rules(rulebook: list[dict[str, Any]], consensus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statuses = {row["name"]: row["status"] for row in consensus}
    return [rule for rule in rulebook if all(statuses.get(name) == "agreed_true" for name in rule["conditions"])]


def _analysis_schema() -> dict[str, Any]:
    predicate_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "enum": list(PREDICATE_DEFINITIONS)},
            "value": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_text": {"type": "string"},
        },
        "required": ["name", "value", "confidence", "evidence_text"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "direction_label": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "direction_score": {"type": "number", "minimum": -1, "maximum": 1},
            "zero_shot_score": {"type": "number", "minimum": -1, "maximum": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "horizon_days": {"type": "integer", "enum": [5]},
            "evidence_text": {"type": "string"},
            "predicates": {"type": "array", "minItems": len(PREDICATE_DEFINITIONS), "maxItems": len(PREDICATE_DEFINITIONS), "items": predicate_schema},
        },
        "required": ["direction_label", "direction_score", "zero_shot_score", "confidence", "horizon_days", "evidence_text", "predicates"],
    }


def _analysis_messages(document: dict[str, str], rulebook: list[dict[str, Any]], contexts: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload = {
        "task": "预测该文本公开后，碳酸锂主力连续合约未来5个交易日 open-to-open 价格方向。",
        "label_definition": {"bullish": ">=+1%", "bearish": "<=-1%", "neutral": "其余"},
        "document": {key: document.get(key, "") for key in ("title", "content", "publish_time", "source_name", "url")},
        "predicate_schema": PREDICATE_DEFINITIONS,
        "frozen_rulebook": rulebook,
        "contrastive_source_contexts": contexts,
        "constraints": [
            "每个谓词必须且只能返回一次。",
            "value=true 时 evidence_text 必须是输入标题或正文中的连续原文；false 时可为空。",
            "zero_shot_score 表示不看规则簿的直接判断，direction_score 表示结合规则簿后的最终判断。",
            "不得使用 publish_time 之后的信息，不得给出交易建议或目标价。",
        ],
    }
    return [
        {"role": "system", "content": "你是审慎的碳酸锂产业研究模型。只输出符合 Schema 的 JSON，不输出投资建议。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _character_vector(text: str) -> dict[str, float]:
    normalized = re.sub(r"\s+", "", text.lower())
    counts: dict[str, float] = defaultdict(float)
    for size in (2, 3):
        for index in range(max(0, len(normalized) - size + 1)):
            counts[normalized[index:index + size]] += 1.0
    return counts


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    dot = sum(value * right.get(key, 0.0) for key, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def retrieve_contexts(document: dict[str, str], historical: list[dict[str, Any]], per_label: int = 3) -> list[dict[str, Any]]:
    query = _character_vector(f"{document.get('title', '')} {document.get('content', '')}")
    scored: list[tuple[float, dict[str, Any]]] = []
    publish_day = _parse_day(document["publish_time"], "publish_time")
    for row in historical:
        if _parse_day(str(row["publish_time"]), "publish_time") >= publish_day:
            continue
        text = f"{row.get('title', '')} {row.get('content', '')}"
        scored.append((_cosine(query, _character_vector(text)), row))
    result: list[dict[str, Any]] = []
    for label in ("bullish", "bearish", "neutral"):
        ranked = sorted((item for item in scored if item[1].get("direction_label") == label), key=lambda item: -item[0])[:per_label]
        result.extend({"doc_id": row.get("doc_id", ""), "direction_label": label, "similarity": round(score, 6), "title": row.get("title", "")} for score, row in ranked)
    return result


def _validate_direction(payload: dict[str, Any], source_text: str) -> None:
    if payload.get("direction_label") not in {"bullish", "bearish", "neutral"}:
        raise ValueError("direction_label 不合法")
    for field in ("direction_score", "zero_shot_score"):
        value = _float(payload.get(field), field)
        if not -1 <= value <= 1:
            raise ValueError(f"{field} 必须在 -1 到 1")
    confidence = _float(payload.get("confidence"), "confidence")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence 必须在 0 到 1")
    if int(payload.get("horizon_days", 0)) != HORIZON_DAYS:
        raise ValueError("horizon_days 必须为 5")
    evidence = str(payload.get("evidence_text", "")).strip()
    if evidence and evidence not in source_text:
        raise ValueError("方向证据无法回溯到输入原文")


def analyze_document(
    document: dict[str, str],
    gateway: OpenAICompatibleGateway,
    rulebook: list[dict[str, Any]],
    historical_contexts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_text = f"{document.get('title', '')}\n{document.get('content', '')}"
    contexts = retrieve_contexts(document, historical_contexts or [])
    raw, metadata = gateway.chat_json(_analysis_messages(document, rulebook, contexts), _analysis_schema(), "lithium_rift_direction")
    _validate_direction(raw, source_text)
    deterministic = deterministic_predicates(document)
    consensus = predicate_consensus(deterministic, raw.get("predicates", []), source_text)
    active = activated_rules(rulebook, consensus)
    if active:
        score = max(-1.0, min(1.0, float(raw["direction_score"])))
        label = "bullish" if score >= 0.10 else "bearish" if score <= -0.10 else "neutral"
        mode = "rift_rule_enhanced_llm"
    else:
        score = 0.0
        label = "neutral"
        mode = "rulebook_inactive"
    return {
        "research_type": "lithium_rift_direction",
        "doc_id": document.get("doc_id", "live-input"),
        "publish_time": document["publish_time"],
        "direction_label": label,
        "direction_score": score,
        "zero_shot_score": float(raw["zero_shot_score"]),
        "confidence": float(raw["confidence"]),
        "horizon_days": HORIZON_DAYS,
        "activated_rules": active,
        "predicate_consensus": consensus,
        "evidence_text": str(raw.get("evidence_text", "")),
        "inference_mode": mode,
        "contrastive_contexts": contexts,
        "model": metadata.get("model", gateway.settings.chat_model),
        "request_id": metadata.get("request_id", ""),
        "usage": metadata.get("usage", {}),
        "disclaimer": DISCLAIMER,
        "research_boundary": RESEARCH_BOUNDARY,
    }


def _split_for_day(day: date) -> str:
    if day <= DISCOVERY_END:
        return "discovery"
    if VALIDATION_START <= day <= VALIDATION_END:
        return "validation"
    if day >= OOS_START:
        return "oos"
    return "unassigned"


def _signal_score(signal: dict[str, Any], field: str) -> float:
    return max(-1.0, min(1.0, float(signal.get(field, 0) or 0)))


def _active_text_score(day_index: int, days: list[str], signals: list[dict[str, Any]], field: str) -> float:
    if day_index < 0:
        return 0.0
    recent_days = days[max(0, day_index - 4):day_index + 1]
    day_positions = {value: index for index, value in enumerate(days)}
    numerator = 0.0
    denominator = 0.0
    for signal in signals:
        publish = str(signal.get("publish_time", ""))[:10]
        signal_day = next((candidate for candidate in days[:day_index + 1] if candidate >= publish), None)
        if signal_day not in recent_days:
            continue
        age = day_index - day_positions[signal_day]
        decay = (5 - age) / 5
        confidence = max(0.0, min(1.0, float(signal.get("confidence", 0) or 0)))
        weight = confidence * decay
        numerator += _signal_score(signal, field) * weight
        denominator += weight
    return numerator / denominator if denominator else 0.0


def _momentum_context(
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]] | None,
) -> tuple[list[str], dict[tuple[str, str], dict[str, str]], list[float | None], float]:
    days = [str(row["trade_date"]) for row in continuous]
    contract_lookup = {
        (row["trade_date"], row["contract"].strip().upper()): row
        for row in (contracts or [])
    }
    momentum: list[float | None] = [None] * len(continuous)
    for index in range(20, len(continuous)):
        selected_contract = str(continuous[index]["contract"])
        old_contract_row = contract_lookup.get((days[index - 20], selected_contract))
        if old_contract_row is not None:
            momentum[index] = (
                float(continuous[index]["close"])
                / _float(old_contract_row["close"], "close", positive=True)
                - 1.0
            )
        elif not contract_lookup:
            momentum[index] = (
                float(continuous[index]["close"])
                / float(continuous[index - 20]["close"])
                - 1.0
            )
    validation_values = [
        value for value, day in zip(momentum, days)
        if value is not None and VALIDATION_START <= _parse_day(day, "trade_date") <= VALIDATION_END
    ]
    validation_std = statistics.pstdev(validation_values) if len(validation_values) >= 2 else 0.0
    return days, contract_lookup, momentum, validation_std


def map_prediction_to_strategy(
    publish_time: str,
    direction_score: float,
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Map one text forecast to the frozen trend-overlay strategy at its information date."""
    if len(continuous) < 21:
        return {
            "status": "insufficient_market_history",
            "baseline_strategy": "pure_trend_20d",
            "enhanced_strategy": PROSPECTIVE_STRATEGY,
        }
    days, _, momentum, validation_std = _momentum_context(continuous, contracts)
    publish_day = _parse_day(publish_time, "publish_time").isoformat()
    signal_index = next((index for index, day in enumerate(days) if day >= publish_day), None)
    if signal_index is None:
        return {
            "status": "awaiting_signal_day_market_data",
            "publish_time": publish_day,
            "latest_market_date": days[-1],
            "baseline_strategy": "pure_trend_20d",
            "enhanced_strategy": PROSPECTIVE_STRATEGY,
        }
    raw_momentum = momentum[signal_index]
    if raw_momentum is None or validation_std <= 0:
        return {
            "status": "insufficient_momentum_context",
            "publish_time": publish_day,
            "signal_market_date": days[signal_index],
            "baseline_strategy": "pure_trend_20d",
            "enhanced_strategy": PROSPECTIVE_STRATEGY,
        }
    trend_score = math.tanh(float(raw_momentum) / validation_std)
    text_score = max(-1.0, min(1.0, float(direction_score)))
    enhanced_score = (
        max(-1.0, min(1.0, trend_score + PROSPECTIVE_TEXT_WEIGHT * text_score))
        if trend_score * text_score > 0
        else trend_score
    )
    execution_day = days[signal_index + 1] if signal_index + 1 < len(days) else ""
    return {
        "status": "mapped" if execution_day else "awaiting_next_trading_day",
        "publish_time": publish_day,
        "signal_market_date": days[signal_index],
        "execution_trade_date": execution_day,
        "execution_timing": "信号日收盘后形成，下一交易日开盘执行",
        "baseline_strategy": "pure_trend_20d",
        "enhanced_strategy": PROSPECTIVE_STRATEGY,
        "momentum_20d": float(raw_momentum),
        "validation_std": validation_std,
        "baseline_position": trend_score,
        "text_direction_score": text_score,
        "enhanced_position": enhanced_score,
        "position_delta": enhanced_score - trend_score,
        "text_confirmed_trend": trend_score * text_score > 0,
        "position_range": [-1.0, 1.0],
        "formula": (
            "若 trend_score 与 text_direction_score 同号，"
            "enhanced=clip(trend_score+text_direction_score,-1,1)；否则保持纯趋势仓位"
        ),
    }


def build_prospective_decision(
    signal_date: str,
    continuous: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    contracts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Freeze the aggregate strategy position known after one signal-day close."""
    days, _, momentum, validation_std = _momentum_context(continuous, contracts)
    try:
        signal_index = days.index(_parse_day(signal_date, "signal_date").isoformat())
    except ValueError as exc:
        raise ValueError(f"signal_date={signal_date} 不在受控主力连续行情中") from exc
    raw_momentum = momentum[signal_index]
    if raw_momentum is None or validation_std <= 0:
        raise ValueError("信号日缺少20日动量或验证期标准差")
    trend_score = math.tanh(float(raw_momentum) / validation_std)
    text_score = _active_text_score(signal_index, days, signals, "direction_score")
    enhanced_score = (
        max(-1.0, min(1.0, trend_score + PROSPECTIVE_TEXT_WEIGHT * text_score))
        if trend_score * text_score > 0
        else trend_score
    )
    return {
        "strategy_version": "lithium-prospective-v2",
        "signal_date": days[signal_index],
        "selected_contract": str(continuous[signal_index]["contract"]),
        "baseline_strategy": "pure_trend_20d",
        "enhanced_strategy": PROSPECTIVE_STRATEGY,
        "momentum_20d": float(raw_momentum),
        "validation_std": validation_std,
        "active_text_score": text_score,
        "baseline_position": trend_score,
        "enhanced_position": enhanced_score,
        "position_delta": enhanced_score - trend_score,
        "text_confirmed_trend": trend_score * text_score > 0,
        "cost_bps": 5.0,
        "execution_rule": "next_available_trading_day_open_to_following_open",
    }


def evaluate_prospective_decisions(
    decisions: list[dict[str, str]],
    contracts: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Settle only positions that were immutably recorded before their entry open."""
    continuous = build_main_continuous(contracts)
    days = [str(row["trade_date"]) for row in continuous]
    day_positions = {day: index for index, day in enumerate(days)}
    contract_lookup = {
        (row["trade_date"], row["contract"].strip().upper()): row
        for row in contracts
    }
    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    pending = 0
    previous_positions = {"pure_trend": 0.0, PROSPECTIVE_STRATEGY: 0.0}
    navs = {"pure_trend": 1.0, PROSPECTIVE_STRATEGY: 1.0}
    seen_dates: set[str] = set()
    for decision in sorted(decisions, key=lambda row: row.get("signal_date", "")):
        signal_day = decision.get("signal_date", "")
        if signal_day in seen_dates:
            invalid.append({"signal_date": signal_day, "reason": "duplicate_signal_date"})
            continue
        seen_dates.add(signal_day)
        signal_index = day_positions.get(signal_day)
        if signal_index is None:
            invalid.append({"signal_date": signal_day, "reason": "missing_signal_market_day"})
            continue
        if signal_index + 2 >= len(days):
            pending += 1
            continue
        entry_day = days[signal_index + 1]
        exit_day = days[signal_index + 2]
        try:
            recorded_at = datetime.fromisoformat(decision.get("recorded_at", ""))
        except ValueError:
            invalid.append({"signal_date": signal_day, "reason": "invalid_recorded_at"})
            continue
        if recorded_at.date() >= _parse_day(entry_day, "entry_trade_date"):
            invalid.append({"signal_date": signal_day, "reason": "recorded_after_entry_open"})
            continue
        selected_contract = decision.get("selected_contract", "").strip().upper()
        entry_contract = contract_lookup.get((entry_day, selected_contract))
        exit_contract = contract_lookup.get((exit_day, selected_contract))
        if entry_contract is None or exit_contract is None:
            invalid.append({"signal_date": signal_day, "reason": "contract_not_tradeable_through_exit"})
            continue
        market_return = (
            _float(exit_contract["open"], "open", positive=True)
            / _float(entry_contract["open"], "open", positive=True)
            - 1.0
        )
        cost_rate = float(decision.get("cost_bps", 5) or 5) / 10000.0
        positions = {
            "pure_trend": float(decision["baseline_position"]),
            PROSPECTIVE_STRATEGY: float(decision["enhanced_position"]),
        }
        for strategy, position in positions.items():
            turnover = abs(position - previous_positions[strategy])
            net_return = position * market_return - turnover * cost_rate
            navs[strategy] *= 1.0 + net_return
            rows.append({
                "trade_date": entry_day,
                "signal_date": signal_day,
                "split": "prospective_oos",
                "strategy": strategy,
                "position": position,
                "market_open_return": market_return,
                "turnover": turnover,
                "cost_bps": cost_rate * 10000.0,
                "net_return": net_return,
                "nav": navs[strategy],
                "trend_score": float(decision["baseline_position"]),
                "active_text_score": (
                    float(decision.get("active_text_score", 0) or 0)
                    if strategy == PROSPECTIVE_STRATEGY else 0.0
                ),
                "validation_std": float(decision.get("validation_std", 0) or 0),
            })
            previous_positions[strategy] = position
    latest = max((row.get("signal_date", "") for row in decisions), default="")
    return rows, {
        "file": PROSPECTIVE_DECISION_FILE,
        "recorded_decisions": len(decisions),
        "settled_decisions": len(rows) // 2,
        "pending_decisions": pending,
        "invalid_decisions": invalid,
        "latest_signal_date": latest,
        "evidence_mode": "append_only_pre_trade_decision_ledger",
    }


def _strategy_rows(
    continuous: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    cost_bps: float,
    contracts: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if len(continuous) < 22:
        return []
    days, contract_lookup, momentum, validation_std = _momentum_context(continuous, contracts)
    rows: list[dict[str, Any]] = []
    previous_positions = {name: 0.0 for name in (*PRIMARY_STRATEGIES, PROSPECTIVE_STRATEGY)}
    navs = {name: 1.0 for name in previous_positions}
    cost_rate = cost_bps / 10000.0
    for signal_index in range(20, len(continuous) - 2):
        signal_day = days[signal_index]
        entry_day = days[signal_index + 1]
        raw_momentum = float(momentum[signal_index] or 0.0)
        trend_score = math.tanh(raw_momentum / validation_std) if validation_std > 0 else 0.0
        rift_text = _active_text_score(signal_index, days, signals, "direction_score")
        zero_text = _active_text_score(signal_index, days, signals, "zero_shot_score")
        positions = {
            "pure_trend": trend_score,
            "zero_shot_llm": max(-1.0, min(1.0, 0.70 * trend_score + 0.30 * zero_text)),
            "rift_enhanced_trend": max(-1.0, min(1.0, 0.70 * trend_score + 0.30 * rift_text)),
            PROSPECTIVE_STRATEGY: (
                max(-1.0, min(1.0, trend_score + PROSPECTIVE_TEXT_WEIGHT * rift_text))
                if trend_score * rift_text > 0 else trend_score
            ),
        }
        # The contract is selected with close-i open interest, then traded from
        # open i+1 to open i+2.  Holding the same contract through this interval
        # prevents a calendar spread from appearing as strategy P&L on rolls.
        selected_contract = str(continuous[signal_index]["contract"])
        entry_contract = contract_lookup.get((days[signal_index + 1], selected_contract))
        exit_contract = contract_lookup.get((days[signal_index + 2], selected_contract))
        if contract_lookup and (entry_contract is None or exit_contract is None):
            continue
        if entry_contract is not None and exit_contract is not None:
            market_return = _float(exit_contract["open"], "open", positive=True) / _float(entry_contract["open"], "open", positive=True) - 1.0
        else:
            market_return = float(continuous[signal_index + 2]["open"]) / float(continuous[signal_index + 1]["open"]) - 1.0
        split = _split_for_day(_parse_day(entry_day, "trade_date"))
        for strategy, position in positions.items():
            turnover = abs(position - previous_positions[strategy])
            net_return = position * market_return - turnover * cost_rate
            navs[strategy] *= 1.0 + net_return
            rows.append({
                "trade_date": entry_day,
                "signal_date": signal_day,
                "split": split,
                "strategy": strategy,
                "position": position,
                "market_open_return": market_return,
                "turnover": turnover,
                "cost_bps": cost_bps,
                "net_return": net_return,
                "nav": navs[strategy],
                "trend_score": trend_score,
                "active_text_score": rift_text if strategy in {"rift_enhanced_trend", PROSPECTIVE_STRATEGY} else zero_text if strategy == "zero_shot_llm" else 0.0,
                "validation_std": validation_std,
            })
            previous_positions[strategy] = position
    return rows


def _metrics(
    rows: list[dict[str, Any]],
    split: str = "oos",
    strategies: Iterable[str] = PRIMARY_STRATEGIES,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for strategy in strategies:
        selected = [row for row in rows if row["strategy"] == strategy and row["split"] == split]
        returns = [float(row["net_return"]) for row in selected]
        if not returns:
            output.append({"strategy": strategy, "split": split, "observations": 0, "annual_return": 0.0, "annual_volatility": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "annual_turnover": 0.0})
            continue
        total = math.prod(1.0 + value for value in returns)
        annual_return = total ** (252 / len(returns)) - 1.0 if total > 0 else -1.0
        volatility = statistics.pstdev(returns) * math.sqrt(252) if len(returns) >= 2 else 0.0
        nav = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in returns:
            nav *= 1.0 + value
            peak = max(peak, nav)
            max_drawdown = min(max_drawdown, nav / peak - 1.0)
        output.append({
            "strategy": strategy,
            "split": split,
            "observations": len(returns),
            "annual_return": annual_return,
            "annual_volatility": volatility,
            "sharpe": annual_return / volatility if volatility else 0.0,
            "max_drawdown": max_drawdown,
            "annual_turnover": sum(float(row["turnover"]) for row in selected) * 252 / len(selected),
        })
    return output


def block_bootstrap_increment(
    rows: list[dict[str, Any]],
    block_size: int = 63,
    samples: int = 2000,
    *,
    split: str = "oos",
    enhanced_strategy: str = "rift_enhanced_trend",
) -> dict[str, Any]:
    trend = {row["trade_date"]: float(row["net_return"]) for row in rows if row["strategy"] == "pure_trend" and row["split"] == split}
    enhanced = {row["trade_date"]: float(row["net_return"]) for row in rows if row["strategy"] == enhanced_strategy and row["split"] == split}
    days = sorted(set(trend) & set(enhanced))
    differences = [enhanced[day] - trend[day] for day in days]
    observed = statistics.mean(differences) * 252 if differences else 0.0
    if len(differences) < block_size:
        return {
            "method": "moving_block_bootstrap_3_months",
            "block_size_trading_days": block_size,
            "observations": len(differences),
            "samples": 0,
            "annualized_net_return_difference": observed,
            "ci_lower_95": 0.0,
            "ci_upper_95": 0.0,
            "conclusion": "insufficient_oos_history",
        }
    blocks = [differences[index:index + block_size] for index in range(len(differences) - block_size + 1)]
    rng = random.Random(20260813)
    estimates: list[float] = []
    for _ in range(samples):
        sample: list[float] = []
        while len(sample) < len(differences):
            sample.extend(rng.choice(blocks))
        estimates.append(statistics.mean(sample[:len(differences)]) * 252)
    estimates.sort()
    lower = estimates[int(0.025 * (len(estimates) - 1))]
    upper = estimates[int(0.975 * (len(estimates) - 1))]
    conclusion = "positive_increment_established" if observed > 0 and lower > 0 else "trading_increment_not_established"
    return {
        "method": "moving_block_bootstrap_3_months",
        "block_size_trading_days": block_size,
        "observations": len(differences),
        "samples": samples,
        "annualized_net_return_difference": observed,
        "ci_lower_95": lower,
        "ci_upper_95": upper,
        "conclusion": conclusion,
    }


def prospective_candidate_report(
    rows: list[dict[str, Any]],
    contracts: list[dict[str, str]],
) -> dict[str, Any]:
    validation_metrics = _metrics(
        rows,
        "validation",
        strategies=("pure_trend", PROSPECTIVE_STRATEGY),
    )
    validation_bootstrap = block_bootstrap_increment(
        rows,
        split="validation",
        enhanced_strategy=PROSPECTIVE_STRATEGY,
    )
    validation_by_day = {
        strategy: {
            row["trade_date"]: float(row["net_return"])
            for row in rows
            if row["split"] == "validation" and row["strategy"] == strategy
        }
        for strategy in ("pure_trend", PROSPECTIVE_STRATEGY)
    }
    validation_quarters = []
    for quarter in range(1, 5):
        start_month = quarter * 3 - 2
        quarter_days = [
            day for day in sorted(set(validation_by_day["pure_trend"]) & set(validation_by_day[PROSPECTIVE_STRATEGY]))
            if start_month <= int(day[5:7]) <= start_month + 2
        ]
        differences = [
            validation_by_day[PROSPECTIVE_STRATEGY][day]
            - validation_by_day["pure_trend"][day]
            for day in quarter_days
        ]
        validation_quarters.append({
            "quarter": f"2025Q{quarter}",
            "observations": len(differences),
            "annualized_net_return_difference": (
                statistics.mean(differences) * 252 if differences else 0.0
            ),
        })
    historical_stress_rows = [
        row for row in rows
        if row["split"] == "oos"
        and row["strategy"] in {"pure_trend", PROSPECTIVE_STRATEGY}
        and str(row["trade_date"]) < PROSPECTIVE_START.isoformat()
    ]
    historical_stress_bootstrap = block_bootstrap_increment(
        historical_stress_rows,
        split="oos",
        enhanced_strategy=PROSPECTIVE_STRATEGY,
    )
    prospective_rows, decision_ledger = evaluate_prospective_decisions(
        _read_csv(PROSPECTIVE_DECISION_FILE),
        contracts,
    )
    prospective_metrics = _metrics(
        prospective_rows,
        "prospective_oos",
        strategies=("pure_trend", PROSPECTIVE_STRATEGY),
    )
    prospective_bootstrap = block_bootstrap_increment(
        prospective_rows,
        split="prospective_oos",
        enhanced_strategy=PROSPECTIVE_STRATEGY,
    )
    established = prospective_bootstrap["conclusion"] == "positive_increment_established"
    if established:
        status = "positive_increment_established"
        conclusion = "前瞻交易增量成立"
    elif prospective_bootstrap["conclusion"] == "insufficient_oos_history":
        status = "awaiting_new_oos_data"
        conclusion = "前瞻交易增量待检验"
    else:
        status = "trading_increment_not_established"
        conclusion = "前瞻交易增量未建立"
    return {
        "version": "lithium-prospective-v2",
        "status": status,
        "conclusion": conclusion,
        "increment_established": established,
        "frozen_at": PROSPECTIVE_FREEZE_DATE.isoformat(),
        "prospective_start": PROSPECTIVE_START.isoformat(),
        "selection_split": "validation",
        "selection_grid": {"mode": "trend_agreement_overlay", "text_weights": [0.25, 0.50, 0.75, 1.0]},
        "selected_parameters": {"mode": "trend_agreement_overlay", "text_weight": PROSPECTIVE_TEXT_WEIGHT, "cost_bps": 5},
        "strategy_formula": "trend_score；仅当 trend_score * active_text_score > 0 时 clip(trend_score + active_text_score, -1, 1)",
        "validation_metrics": validation_metrics,
        "validation_bootstrap": validation_bootstrap,
        "validation_quarterly_increment": validation_quarters,
        "historical_oos_stress_bootstrap": historical_stress_bootstrap,
        "historical_oos_stress_boundary": "仅作冻结后的压力诊断，不参与参数选择或交易增量主结论",
        "prospective_metrics": prospective_metrics,
        "prospective_bootstrap": prospective_bootstrap,
        "prospective_nav": prospective_rows,
        "decision_ledger": decision_ledger,
        "research_boundary": "Validation 通过不等于交易增量成立；只有冻结日后的新 OOS 满足同一 Bootstrap 门槛才可宣称成立",
    }


def run_backtest(
    continuous: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    contracts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    primary_rows = _strategy_rows(continuous, signals, 5.0, contracts)
    bootstrap = block_bootstrap_increment(primary_rows)
    sensitivities = []
    for cost in (2.0, 5.0, 10.0):
        rows = _strategy_rows(continuous, signals, cost, contracts)
        metrics = _metrics(rows)
        trend = next(row for row in metrics if row["strategy"] == "pure_trend")
        enhanced = next(row for row in metrics if row["strategy"] == "rift_enhanced_trend")
        sensitivities.append({"cost_bps": cost, "enhanced_annual_return": enhanced["annual_return"], "trend_annual_return": trend["annual_return"], "annual_return_difference": enhanced["annual_return"] - trend["annual_return"]})
    return {
        "engine_version": BACKTEST_ENGINE_VERSION,
        "status": "evaluated" if primary_rows else "insufficient_market_history",
        "target": "碳酸锂主力连续合约未来5个交易日价格方向",
        "primary_cost_bps": 5,
        "rebalance_timing": "收盘后形成信号，下一交易日开盘执行",
        "position_range": [-1, 1],
        "strategy_formula": "clip(0.70 * trend_score + 0.30 * active_text_score, -1, 1)",
        "metrics": _metrics(primary_rows),
        "bootstrap": bootstrap,
        "cost_sensitivity": sensitivities,
        "nav": [row for row in primary_rows if row["split"] == "oos" and row["strategy"] in PRIMARY_STRATEGIES],
        "prospective_candidate": prospective_candidate_report(primary_rows, contracts or []),
        "increment_established": bootstrap["conclusion"] == "positive_increment_established",
        "conclusion": "交易增量成立" if bootstrap["conclusion"] == "positive_increment_established" else "交易增量未建立",
        "disclaimer": DISCLAIMER,
        "research_boundary": RESEARCH_BOUNDARY,
    }


def _load_rulebook() -> list[dict[str, Any]]:
    rows = _read_csv("lithium_rulebook.csv")
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "qualified":
            continue
        result.append({
            "rule_id": row["rule_id"],
            "target_label": row["target_label"],
            "conditions": [part for part in row["conditions"].split(" AND ") if part],
            "score": float(row["score"]),
            "coverage_positive": float(row["coverage_positive"]),
            "coverage_negative": float(row["coverage_negative"]),
            "support_documents": int(row["support_documents"]),
            "support_dates": int(row["support_dates"]),
            "status": row["status"],
        })
    return result


def _load_signals() -> list[dict[str, Any]]:
    rows = _read_csv("lithium_text_signals.csv")
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({**row, "direction_score": float(row.get("direction_score") or 0), "zero_shot_score": float(row.get("zero_shot_score") or 0), "confidence": float(row.get("confidence") or 0)})
    return result


def _induction_records(
    texts: list[dict[str, str]],
    signals: list[dict[str, Any]],
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    texts_by_id = {row["doc_id"]: row for row in texts if row.get("review_status") == "accepted"}
    records: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for signal in signals:
        document = texts_by_id.get(str(signal.get("doc_id", "")))
        if document is None:
            continue
        label = forward_label(document["publish_time"], continuous, contracts=contracts)
        if label is None:
            continue
        auxiliary = forward_label(document["publish_time"], continuous, horizon=10, contracts=contracts)
        raw_consensus = signal.get("predicate_consensus", [])
        if isinstance(raw_consensus, str):
            try:
                raw_consensus = json.loads(raw_consensus)
            except json.JSONDecodeError:
                raw_consensus = []
        status_map = {
            str(row.get("name", "")): str(row.get("status", ""))
            for row in raw_consensus if isinstance(row, dict)
        }
        if set(status_map) != set(PREDICATE_DEFINITIONS):
            continue
        records.append({
            **document,
            "direction_label": label["direction_label"],
            "predicate_status": status_map,
        })
        labels.append({
            "doc_id": document["doc_id"],
            "publish_time": document["publish_time"],
            **label,
            "aux_exit_trade_date_10d": auxiliary["exit_trade_date"] if auxiliary else "",
            "aux_forward_open_return_10d": auxiliary["forward_open_return"] if auxiliary else "",
            "aux_direction_label_10d": auxiliary["direction_label"] if auxiliary else "",
        })
    return records, labels


def load_status() -> dict[str, Any]:
    texts = _read_csv("lithium_texts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    warehouse = _read_csv("lithium_warehouse_receipts.csv")
    provenance = text_provenance_report(texts, _read_csv("lithium_text_fetch_audit.csv"))
    errors = validate_controlled_data(texts, contracts, warehouse) + provenance["errors"]
    continuous = build_main_continuous(contracts) if not errors else []
    signals = _load_signals()
    decisions = _read_csv(PROSPECTIVE_DECISION_FILE)
    rulebook = _load_rulebook()
    split_counts = defaultdict(int)
    for row in texts:
        try:
            split_counts[_split_for_day(_parse_day(row["publish_time"], "publish_time"))] += 1
        except ValueError:
            pass
    ready = bool(continuous) and bool(warehouse) and bool(signals) and bool(rulebook) and not errors
    return {
        "version": "lithium-rift-v1",
        "target_name": "碳酸锂主力连续合约未来5个交易日价格方向",
        "horizon_days": 5,
        "auxiliary_horizon_days": 10,
        "data_mode": "controlled_csv",
        "counts": {"texts": len(texts), "contract_rows": len(contracts), "warehouse_rows": len(warehouse), "continuous_days": len(continuous), "signals": len(signals), "qualified_rules": len(rulebook), "prospective_decisions": len(decisions)},
        "split_counts": dict(split_counts),
        "data_errors": errors,
        "data_ready": ready,
        "text_provenance": provenance,
        "rulebook": rulebook,
        "predicate_schema": PREDICATE_DEFINITIONS,
        "sample_boundaries": {"discovery": "2023-07-21/2024-12-31", "validation": "2025-01-01/2025-12-31", "oos": "2026-01-01/latest"},
        "official_sources": {
            "contract": "https://www.gfex.com.cn/gfex/tsl/sspz.shtml",
            "daily_market": "https://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml",
            "warehouse_receipt": "https://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml",
        },
        "status": "ready" if ready else "awaiting_controlled_data",
        "disclaimer": DISCLAIMER,
        "research_boundary": RESEARCH_BOUNDARY,
    }


def load_forecast() -> dict[str, Any]:
    signals = _load_signals()
    latest = max(signals, key=lambda row: row.get("publish_time", "")) if signals else None
    return {
        "status": "available" if latest else "no_validated_signal",
        "latest": latest,
        "signal_count": len(signals),
        "forecast_definition": "文本公开后下一交易日开盘至第5个交易日开盘；±1%阈值定义方向标签",
        "disclaimer": DISCLAIMER,
        "research_boundary": RESEARCH_BOUNDARY,
    }


def load_backtest() -> dict[str, Any]:
    texts = _read_csv("lithium_texts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    warehouse = _read_csv("lithium_warehouse_receipts.csv")
    provenance = text_provenance_report(texts, _read_csv("lithium_text_fetch_audit.csv"))
    errors = validate_controlled_data(texts, contracts, warehouse) + provenance["errors"]
    if errors:
        return {"status": "invalid_controlled_data", "data_errors": errors, "metrics": [], "nav": [], "bootstrap": {"conclusion": "not_evaluated"}, "increment_established": False, "conclusion": "交易增量未建立", "disclaimer": DISCLAIMER, "research_boundary": RESEARCH_BOUNDARY}
    artifact_path = SAMPLE_DIR / "lithium_backtest.json"
    input_paths = [
        SAMPLE_DIR / name for name in (
            "lithium_texts.csv",
            "lithium_contract_daily.csv",
            "lithium_warehouse_receipts.csv",
            "lithium_text_signals.csv",
            "lithium_text_fetch_audit.csv",
        )
    ]
    decision_path = SAMPLE_DIR / PROSPECTIVE_DECISION_FILE
    if decision_path.exists():
        input_paths.append(decision_path)
    input_paths.append(Path(__file__))
    if artifact_path.exists() and all(path.exists() for path in input_paths):
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            artifact = {}
        newest_input = max(path.stat().st_mtime_ns for path in input_paths)
        if artifact.get("engine_version") == BACKTEST_ENGINE_VERSION and artifact_path.stat().st_mtime_ns >= newest_input:
            return artifact
    return run_backtest(build_main_continuous(contracts), _load_signals(), contracts)


def build_lithium_outputs() -> dict[str, Any]:
    """Validate controlled inputs and rebuild deterministic derived outputs."""
    texts = _read_csv("lithium_texts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    warehouse = _read_csv("lithium_warehouse_receipts.csv")
    provenance = text_provenance_report(texts, _read_csv("lithium_text_fetch_audit.csv"))
    errors = validate_controlled_data(texts, contracts, warehouse) + provenance["errors"]
    if errors:
        raise ValueError("\n".join(errors))
    continuous = build_main_continuous(contracts)
    _write_csv(
        "lithium_main_continuous.csv",
        ["trade_date", "contract", "open", "high", "low", "close", "settlement", "volume", "open_interest", "selection_basis", "rolled", "source_name", "source_url"],
        continuous,
    )
    signals = _load_signals()
    induction_records, labels = _induction_records(texts, signals, continuous, contracts)
    rulebook = induce_rulebook(induction_records)
    _write_csv(
        "lithium_text_labels.csv",
        ["doc_id", "publish_time", "entry_trade_date", "exit_trade_date", "forward_open_return", "direction_label", "horizon_days", "future_info_ok", "aux_exit_trade_date_10d", "aux_forward_open_return_10d", "aux_direction_label_10d"],
        labels,
    )
    _write_csv(
        "lithium_rulebook.csv",
        RULE_FIELDS,
        [
            {
                **rule,
                "conditions": " AND ".join(rule["conditions"]),
            }
            for rule in rulebook
        ],
    )
    backtest = run_backtest(continuous, signals, contracts)
    _write_csv(
        "lithium_strategy_nav.csv",
        ["trade_date", "signal_date", "split", "strategy", "position", "market_open_return", "turnover", "cost_bps", "net_return", "nav", "trend_score", "active_text_score", "validation_std"],
        backtest["nav"],
    )
    (SAMPLE_DIR / "lithium_backtest.json").write_text(json.dumps(backtest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "continuous_days": len(continuous),
        "signal_count": len(signals),
        "labeled_texts": len(labels),
        "qualified_rules": len(rulebook),
        "backtest_status": backtest["status"],
        "conclusion": backtest["conclusion"],
    }
