"""In-memory analysis for new text using the official AlphaLens rule pipeline."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from src.backtest.demo_engine import factor_name_for_labels
from src.pipeline.extract_events_rule_based import (
    CORE_OBJECT_BY_SECTOR,
    IMPACT_PATH_BY_SECTOR,
    SOURCE_STRENGTH,
    evidence_sentence,
    infer_event_type,
    infer_subject,
)
from src.pipeline.ground_predicates_rule_based import ground_event_predicates
from src.pipeline.link_entities import (
    BROAD_LINKS_PER_SECTOR,
    BROAD_THEME_MAPPINGS,
    SECTOR_KEYWORDS,
    build_alias_rows,
    confidence_for_match,
)


SOURCE_TYPES = {"policy", "announcement", "news", "ir_qa"}


def infer_source_type(title: str, content: str, source_name: str) -> str:
    text = f"{title} {content} {source_name}"
    if any(word in text for word in ["投资者提问", "互动易", "上证e互动", "公司回答"]):
        return "ir_qa"
    if any(word in source_name for word in ["国务院", "发改委", "工信部", "财政部", "能源局"]):
        return "policy"
    if any(word in text for word in ["公告", "公司披露", "董事会"]):
        return "announcement"
    if any(word in text for word in ["行动方案", "实施方案", "指导意见", "补贴政策"]):
        return "policy"
    return "news"


def link_document(
    doc: dict[str, str], stock_pool: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Apply the same exact-name, sector and broad-theme linking order as batch mode."""
    title = doc["title"]
    content = doc["content"].split("项目关联：", 1)[0]
    text = f"{title}\n{content}"
    alias_rows = build_alias_rows(stock_pool)
    stocks_by_sector: dict[str, list[dict[str, str]]] = defaultdict(list)
    for stock in stock_pool:
        stocks_by_sector[stock["industry_sector"]].append(stock)
    for stocks in stocks_by_sector.values():
        stocks.sort(key=lambda item: float(item["market_cap"]), reverse=True)

    results: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for alias_row in alias_rows:
        alias = alias_row["alias"]
        code = alias_row["stock_code"]
        if alias not in text or code in seen_codes:
            continue
        seen_codes.add(code)
        location = "标题含" if alias in title else "正文提及"
        results.append(
            {
                "stock_code": code,
                "stock_name": alias_row["stock_name"],
                "industry": alias_row["industry"],
                "confidence": confidence_for_match(title, content, alias_row["stock_name"], alias),
                "evidence": f'{location}"{alias}"',
            }
        )

    if seen_codes or doc["source_type"] not in {"policy", "news"}:
        return results

    for sector, keywords in SECTOR_KEYWORDS.items():
        matched_keyword = next((keyword for keyword in keywords if keyword in text), "")
        if not matched_keyword:
            continue
        confidence = 0.78 if doc["source_type"] == "policy" else 0.70
        for stock in stocks_by_sector.get(sector, []):
            seen_codes.add(stock["stock_code"])
            results.append(
                {
                    "stock_code": stock["stock_code"],
                    "stock_name": stock["stock_name"],
                    "industry": sector,
                    "confidence": confidence,
                    "evidence": f'产业主题映射"{matched_keyword}"→{sector}',
                }
            )
    if seen_codes:
        return results

    matched_theme = next((item for item in BROAD_THEME_MAPPINGS if item[0] in text), None)
    if not matched_theme:
        return results
    keyword, sectors, theme = matched_theme
    confidence = 0.64 if doc["source_type"] == "policy" else 0.58
    for sector in sectors:
        for stock in stocks_by_sector.get(sector, [])[:BROAD_LINKS_PER_SECTOR]:
            results.append(
                {
                    "stock_code": stock["stock_code"],
                    "stock_name": stock["stock_name"],
                    "industry": sector,
                    "confidence": confidence,
                    "evidence": f'宽主题映射"{keyword}"→{theme}→{sector}代表股票',
                }
            )
    return results


def rule_matches(rule: dict[str, str], predicate_map: dict[str, str], event_type: str) -> bool:
    for term in (part.strip() for part in rule["condition"].split("AND")):
        if term.startswith("event_type") and "=" in term:
            if event_type != term.split("=", 1)[1].strip():
                return False
        elif predicate_map.get(term) != "true":
            return False
    return True


def serialize_predicates(rows: list[dict[str, object]]) -> list[dict[str, Any]]:
    serialized = []
    for row in rows:
        raw_value = str(row["value"])
        value: bool | float | str
        if raw_value in {"true", "false"}:
            value = raw_value == "true"
        else:
            try:
                value = float(raw_value)
            except ValueError:
                value = raw_value
        serialized.append(
            {
                "name": row["predicate_name"],
                "value": value,
                "confidence": float(str(row["confidence"])),
                "rationale": row["rationale"],
            }
        )
    return serialized


def build_predicate_consensus(
    predicate_rows: list[dict[str, object]],
    ai_predicates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Gate rule predicates through AI and deterministic agreement."""
    deterministic = {str(row["predicate_name"]): str(row["value"]).lower() for row in predicate_rows}
    ai_by_name = {str(row.get("name", "")): row for row in ai_predicates}
    consensus: list[dict[str, Any]] = []
    gated_values: dict[str, str] = {}
    for name, rule_value in deterministic.items():
        ai_row = ai_by_name.get(name)
        ai_value = str(ai_row.get("value", "")).lower() if ai_row else ""
        if not ai_row:
            status = "invalid"
        elif rule_value in {"true", "false"}:
            if ai_value not in {"true", "false"}:
                status = "invalid"
            elif ai_value == rule_value:
                status = "agreed_true" if rule_value == "true" else "agreed_false"
            else:
                status = "disputed"
        else:
            try:
                status = "agreed_true" if abs(float(ai_value) - float(rule_value)) <= 0.10 else "disputed"
            except ValueError:
                status = "invalid"
        gated_values[name] = "true" if status == "agreed_true" and rule_value == "true" else "false"
        consensus.append(
            {
                "name": name,
                "ai_value": ai_value,
                "rule_value": rule_value,
                "status": status,
                "accepted_for_rule": gated_values[name] == "true",
                "confidence": ai_row.get("confidence", 0.0) if ai_row else 0.0,
                "rationale": ai_row.get("rationale", "AI 未返回该谓词") if ai_row else "AI 未返回该谓词",
            }
        )
    return consensus, gated_values


def analyze_new_document(
    payload: dict[str, str],
    stock_pool: list[dict[str, str]],
    rules: list[dict[str, str]],
    ai_layer: Any | None = None,
    use_ai: bool = False,
) -> dict[str, Any]:
    title = payload.get("title", "").strip()
    content = payload.get("content", "").strip()
    source_name = payload.get("source_name", "").strip() or "未填写来源"
    source_type = payload.get("source_type", "auto").strip()
    if source_type == "auto":
        source_type = infer_source_type(title, content, source_name)
    if source_type not in SOURCE_TYPES:
        raise ValueError("source_type 必须是 policy、announcement、news、ir_qa 或 auto")
    event_date = payload.get("event_date", "").strip() or date.today().isoformat()

    doc = {
        "doc_id": "LIVE-DOC",
        "source_type": source_type,
        "title": title,
        "content": content,
        "publish_time": event_date,
        "source_name": source_name,
        "url": payload.get("source_url", "").strip(),
    }
    if use_ai and ai_layer is not None:
        ai_analysis = ai_layer.analyze(doc, stock_pool, rules)
        if not ai_analysis.get("used"):
            return {
                "error": f"模式一需要大模型成功参与：{ai_analysis.get('reason') or 'AI 未返回有效结果'}",
                "error_code": "ai_required",
                "ai_analysis": ai_analysis,
            }
        retrieval = ai_analysis.get("embedding_retrieval", {})
        if not retrieval.get("used"):
            return {
                "error": f"模式一需要先完成 Embedding 检索：{retrieval.get('reason') or '向量检索未执行'}",
                "error_code": "embedding_required",
                "ai_analysis": ai_analysis,
            }
    elif ai_layer is not None:
        ai_analysis = ai_layer.skipped("用户选择规则复现模式")
    else:
        ai_analysis = {
            "requested": use_ai,
            "used": False,
            "fallback": True,
            "reason": "AI 研究层不可用",
            "result": None,
        }
    event_type = infer_event_type(doc)
    entities = link_document(doc, stock_pool)
    ai_result = ai_analysis.get("result") if isinstance(ai_analysis, dict) else None
    ai_event = ai_result.get("event", {}) if isinstance(ai_result, dict) else {}
    if event_type is None and ai_event.get("evidence_grounded"):
        event_type = ai_event.get("event_type")
    if not entities and isinstance(ai_result, dict):
        stocks_by_code = {row["stock_code"]: row for row in stock_pool}
        for candidate in ai_result.get("related_stocks", []):
            if not candidate.get("text_grounded"):
                continue
            stock = stocks_by_code.get(candidate["code"])
            if stock is None:
                continue
            entities.append(
                {
                    "stock_code": stock["stock_code"],
                    "stock_name": stock["stock_name"],
                    "industry": stock["industry_sector"],
                    "confidence": candidate["confidence"],
                    "evidence": f"AI 候选经股票池校验：{candidate['rationale']}",
                }
            )
    if event_type is None:
        return {
            "error": "未检测到明确的金融事件",
            "entities": entities,
            "source_type": source_type,
            "ai_analysis": ai_analysis,
        }
    if not entities:
        return {
            "error": "检测到事件，但未关联到股票池实体",
            "entities": [],
            "source_type": source_type,
            "ai_analysis": ai_analysis,
        }

    evidence_strength = SOURCE_STRENGTH[source_type]
    if event_type in {"policy_support", "capacity_expansion"}:
        evidence_strength += 0.02
    evidence_strength = min(evidence_strength, 0.98)
    qualified_rules = [rule for rule in rules if rule["status"] == "qualified" and int(rule["support_count"]) >= 5]
    stock_results: list[dict[str, Any]] = []
    all_rules: dict[str, dict[str, Any]] = {}
    event_trace: list[dict[str, Any]] = []

    for index, entity in enumerate(entities, start=1):
        event = {
            "event_id": f"LIVE-E{index:03d}",
            "doc_id": doc["doc_id"],
            "stock_code": entity["stock_code"],
            "event_type": event_type,
            "event_time": event_date,
            "subject": infer_subject(doc),
            "object": CORE_OBJECT_BY_SECTOR[entity["industry"]],
            "impact_path": IMPACT_PATH_BY_SECTOR[entity["industry"]],
            "evidence_text": evidence_sentence(doc, entity["stock_name"]),
            "evidence_strength": f"{evidence_strength:.2f}",
        }
        predicate_rows = ground_event_predicates(event, doc, entity["industry"])
        deterministic_map = {str(row["predicate_name"]): str(row["value"]) for row in predicate_rows}
        if isinstance(ai_result, dict):
            consensus, predicate_map = build_predicate_consensus(
                predicate_rows,
                list(ai_result.get("predicates", [])),
            )
        else:
            consensus = []
            predicate_map = deterministic_map
        triggered = [rule for rule in qualified_rules if rule_matches(rule, predicate_map, event_type)]
        best_by_family: dict[str, dict[str, str]] = {}
        for rule in triggered:
            family = rule["target_label"]
            if family not in best_by_family or float(rule["score"]) > float(best_by_family[family]["score"]):
                best_by_family[family] = rule
        triggered = sorted(best_by_family.values(), key=lambda row: row["rule_id"])
        raw_score = sum(float(rule["score"]) for rule in triggered)
        impact_prior = float(deterministic_map["event_has_short_term_price_impact"])
        factor_multiplier = 0.7 * evidence_strength + 0.3 * impact_prior
        factor_value = raw_score * factor_multiplier
        labels = [rule["target_label"] for rule in triggered]
        rule_rows = [
            {
                "id": rule["rule_id"],
                "name": rule["rule_name"],
                "condition": rule["condition"],
                "target_label": rule["target_label"],
                "support": int(rule["support_count"]),
                "win_rate": float(rule["win_rate"]),
                "avg_return": float(rule["avg_forward_return_5d"]),
                "score": float(rule["score"]),
            }
            for rule in triggered
        ]
        for rule in rule_rows:
            all_rules[rule["id"]] = rule
        stock_results.append(
            {
                "code": entity["stock_code"],
                "name": entity["stock_name"],
                "sector": entity["industry"],
                "confidence": entity["confidence"],
                "link_evidence": entity["evidence"],
                "factor_name": factor_name_for_labels(labels),
                "candidate_factor": round(factor_value, 6),
                "raw_score": round(raw_score, 6),
                "factor_formula": {
                    "rule_score_sum": round(raw_score, 6),
                    "evidence_strength": round(evidence_strength, 2),
                    "evidence_weight": 0.7,
                    "impact_prior": round(impact_prior, 2),
                    "impact_weight": 0.3,
                    "multiplier": round(factor_multiplier, 6),
                    "result": round(factor_value, 6),
                    "consensus_mode": "ai_rule_agreement" if isinstance(ai_result, dict) else "rule_only",
                },
                "predicates": serialize_predicates(predicate_rows),
                "predicate_consensus": consensus,
                "triggered_rules": rule_rows,
                "event": event,
            }
        )
        event_trace.append(event)

    stock_results.sort(key=lambda item: (item["candidate_factor"], item["confidence"]), reverse=True)
    disputed = sorted(
        {
            row["name"]
            for stock in stock_results
            for row in stock.get("predicate_consensus", [])
            if row["status"] in {"disputed", "invalid"}
        }
    )
    return {
        "event_type": event_type,
        "event_time": event_date,
        "evidence_strength": round(evidence_strength, 2),
        "source_type": source_type,
        "source_name": source_name,
        "source_url": doc["url"],
        "entities": entities,
        "events": event_trace,
        "stock_results": stock_results,
        "triggered_rules": list(all_rules.values()),
        "analysis_mode": "hybrid_ai_rule_validation" if ai_analysis.get("used") else "deterministic_rule_only",
        "ai_analysis": ai_analysis,
        "predicate_consensus": stock_results[0].get("predicate_consensus", []) if stock_results else [],
        "disputed_predicates": disputed,
        "consensus_gate_passed": bool(ai_analysis.get("used")) and not disputed,
    }
