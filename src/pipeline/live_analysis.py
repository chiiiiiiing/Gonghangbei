"""In-memory analysis for new text using the official AlphaLens rule pipeline."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from src.backtest.demo_engine import factor_name_for_labels
from src.pipeline.extract_events_rule_based import (
    CORE_OBJECT_BY_SECTOR,
    IMPACT_PATH_BY_SECTOR,
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
from src.research.scoring import evidence_score_breakdown, load_impact_priors


SOURCE_TYPES = {"policy", "announcement", "news", "ir_qa"}
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample"


def build_event_consensus(
    deterministic_type: str | None,
    ai_event: dict[str, Any],
) -> dict[str, Any]:
    """Compare the event type before allowing predicates or rules to run."""
    ai_type = str(ai_event.get("event_type", "")).strip()
    grounded = bool(ai_event.get("evidence_grounded"))
    if not ai_type or not grounded:
        status = "invalid"
        accepted_type = None
    elif deterministic_type is None:
        status = "ai_validated"
        accepted_type = ai_type
    elif deterministic_type == ai_type:
        status = "agreed"
        accepted_type = deterministic_type
    else:
        status = "disputed"
        accepted_type = None
    return {
        "deterministic_event_type": deterministic_type or "",
        "ai_event_type": ai_type,
        "ai_evidence_grounded": grounded,
        "status": status,
        "accepted": accepted_type is not None,
        "accepted_event_type": accepted_type or "",
    }


def build_entity_consensus(
    entity: dict[str, Any],
    ai_stock_analysis: dict[str, Any] | None,
    *,
    legacy_document_predicates: bool = False,
) -> dict[str, Any]:
    """Require a grounded AI relationship for live v2 stock-level analysis."""
    if ai_stock_analysis is None:
        status = "legacy_validated" if legacy_document_predicates else "invalid"
        return {
            "stock_code": entity["stock_code"],
            "status": status,
            "accepted": legacy_document_predicates,
            "deterministic_evidence": entity["evidence"],
            "ai_evidence": "",
        }
    grounded = bool(ai_stock_analysis.get("relationship_grounded"))
    confidence = float(ai_stock_analysis.get("relationship_confidence", 0.0))
    accepted = grounded and confidence >= 0.50
    return {
        "stock_code": entity["stock_code"],
        "status": "agreed" if accepted else "invalid",
        "accepted": accepted,
        "deterministic_evidence": entity["evidence"],
        "ai_evidence": ai_stock_analysis.get("relationship_evidence", ""),
        "ai_confidence": confidence,
    }


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


def rule_matches(
    rule: dict[str, str],
    predicate_map: dict[str, str] | dict[str, float],
    event_type: str,
    threshold: float = 0.5,
) -> bool:
    """Match a frozen/AI rule against a predicate map.

    predicate_map values may be 'true'/'false'/numeric strings (legacy) or the
    0-1 fused scores from fuse_predicate_values(); a term is satisfied when its
    value is >= threshold.
    """
    for term in (part.strip() for part in rule["condition"].split("AND")):
        if term.startswith("event_type") and "=" in term:
            if event_type != term.split("=", 1)[1].strip():
                return False
        elif predicate_value_to_float(predicate_map.get(term, 0.0)) < threshold:
            return False
    return True


def evaluate_ai_candidate_rules(
    ai_result: dict[str, Any] | None,
    fused_map: dict[str, float],
    gate_open: bool,
) -> tuple[list[dict[str, Any]], float]:
    """Score AI-proposed candidate rules against the fused predicate values.

    Each candidate rule gets a tentative live score
    `0.8 * AI_confidence * (命中谓词数 / 条件数)` — bounded and explainable.
    These are labelled "AI 实时候选，未历史统计验证" and only count when the
    event/entity gates are open.
    """
    rows: list[dict[str, Any]] = []
    total = 0.0
    if not gate_open or not isinstance(ai_result, dict):
        return rows, total
    for item in ai_result.get("candidate_rules", [])[:3]:
        if not isinstance(item, dict):
            continue
        conditions = [str(c) for c in item.get("conditions", []) if isinstance(c, str)]
        if not conditions:
            continue
        hit = sum(
            1
            for condition in conditions
            if predicate_value_to_float(fused_map.get(condition, 0.0)) >= 0.5
        )
        confidence = _bounded(item.get("confidence"), 0.5)
        tentative = 0.8 * confidence * (hit / len(conditions))
        rows.append(
            {
                "id": str(item.get("name", "AI 候选规则")).strip()[:60],
                "name": str(item.get("name", "AI 候选规则")).strip()[:100],
                "condition": " AND ".join(conditions),
                "target_label": str(item.get("target_label", "research_candidate")).strip()[:80],
                "confidence": round(confidence, 4),
                "hit_ratio": f"{hit}/{len(conditions)}",
                "ai_candidate_score": round(tentative, 6),
                "evidence_snippet": str(item.get("evidence_snippet", "")).strip()[:120],
                "rationale": str(item.get("rationale", "")).strip()[:240],
            }
        )
        total += tentative
    return rows, total


def build_rule_explainability(
    condition: str,
    target_label: str,
    fused: dict[str, dict[str, Any]],
    consensus: list[dict[str, Any]],
    doc_text: str,
    qualified_rules: list[dict[str, str]],
    ai_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain a triggered rule: predicate grounds, AI rationale, complexity,
    traceability of evidence, and similarity to historically frozen rules."""
    from src.ai.research_layer import cosine_similarity, local_text_embedding

    terms = [part.strip() for part in condition.split("AND") if part.strip()]
    consensus_by_name = {str(row["name"]): row for row in consensus}
    predicate_rows: list[dict[str, Any]] = []
    for term in terms:
        if term.startswith("event_type") or "=" in term:
            continue
        info = fused.get(term, {})
        cons = consensus_by_name.get(term, {})
        predicate_rows.append(
            {
                "name": term,
                "fused": round(float(info.get("fused", 0.0)), 4),
                "source": info.get("source", "rule_only"),
                "ai_confidence": round(float(info.get("ai_confidence", 0.0)), 2),
                "rationale": str(cons.get("rationale", info.get("rationale", "")))[:120],
            }
        )
    snippet = str((ai_candidate or {}).get("evidence_snippet", "")).strip()
    if ai_candidate:
        traceable = bool(snippet) and snippet in doc_text
    else:
        traceable = True  # frozen rules always match on grounded predicates/evidence
    condition_vector = local_text_embedding(condition)
    similar = []
    for rule in qualified_rules:
        similar.append(
            {
                "rule_id": rule["rule_id"],
                "similarity": round(
                    cosine_similarity(condition_vector, local_text_embedding(rule["condition"])),
                    4,
                ),
            }
        )
    similar.sort(key=lambda item: item["similarity"], reverse=True)
    return {
        "source": "ai_candidate" if ai_candidate else "frozen",
        "target_label": target_label,
        "complexity": len(predicate_rows),
        "predicates": predicate_rows,
        "evidence_snippet": snippet[:120],
        "traceable": traceable,
        "similar_to_frozen": similar[:2],
    }


def persist_ai_candidate_rules(
    doc: dict[str, str],
    candidate_rules: list[dict[str, Any]],
) -> None:
    """Append AI-proposed rules to the AI candidate rule database.

    Separate from rules.csv (which only holds historically validated frozen
    rules); every row is labelled status=ai_candidate so it is never mistaken
    for a validated rule. Failures never break the analysis.
    """
    if not candidate_rules:
        return
    path = SAMPLE_DIR / "ai_candidate_rules.csv"
    fields = [
        "doc_id",
        "rule_name",
        "conditions",
        "target_label",
        "evidence_snippet",
        "rationale",
        "ai_confidence",
        "status",
        "created_time",
    ]
    try:
        fresh = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            if fresh:
                writer.writeheader()
            for item in candidate_rules[:3]:
                writer.writerow(
                    {
                        "doc_id": doc.get("doc_id", ""),
                        "rule_name": str(item.get("name", "")).strip()[:100],
                        "conditions": " AND ".join(str(c) for c in item.get("conditions", [])),
                        "target_label": str(item.get("target_label", "")).strip()[:80],
                        "evidence_snippet": str(item.get("evidence_snippet", "")).strip()[:120],
                        "rationale": str(item.get("rationale", "")).strip()[:240],
                        "ai_confidence": f"{_bounded(item.get('confidence'), 0.5):.2f}",
                        "status": "ai_candidate",
                        "created_time": str(doc.get("publish_time", "")),
                    }
                )
    except OSError:
        pass


def predicate_value_to_float(value: object) -> float:
    """Normalize a predicate value ('true'/'false'/numeric string) to 0-1."""
    text = str(value).strip().lower()
    if text in {"true", "false"}:
        return 1.0 if text == "true" else 0.0
    try:
        return min(max(float(text), 0.0), 1.0)
    except ValueError:
        return 0.0


def _bounded(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, 0.0), 1.0)


def fuse_predicate_values(
    deterministic_map: dict[str, str],
    ai_predicates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Fuse rule and AI predicate values into a 0-1 score that drives live factors.

    Each predicate keeps a transparent source label:
    - agreed_true / agreed_false: AI and rule agree -> adopt (conf = max).
    - disputed: conflict -> pull the rule value toward the AI value by
      min(AI_confidence, 0.5); the factor only partially counts the predicate.
    - invalid / missing: no usable AI value -> fall back to the rule value
      (rule-only), still auditable.

    Returns {predicate_name: {fused, source, ai_confidence, rule_value, ai_value}}.
    """
    ai_by_name = {str(row.get("name", "")): row for row in ai_predicates}
    result: dict[str, dict[str, Any]] = {}
    for name, rule_value in deterministic_map.items():
        rule_f = predicate_value_to_float(rule_value)
        ai_row = ai_by_name.get(name)
        if ai_row is None:
            result[name] = {
                "fused": rule_f,
                "source": "rule_only",
                "ai_confidence": 0.0,
                "rule_value": str(rule_value),
                "ai_value": "",
            }
            continue
        ai_raw = str(ai_row.get("value", "")).strip().lower()
        ai_confidence = _bounded(ai_row.get("confidence"), 0.0)
        ai_valid = ai_raw in {"true", "false"} or _is_numeric(ai_raw)
        if not ai_valid or not ai_raw:
            result[name] = {
                "fused": rule_f,
                "source": "invalid",
                "ai_confidence": ai_confidence,
                "rule_value": str(rule_value),
                "ai_value": ai_raw,
            }
            continue
        ai_f = predicate_value_to_float(ai_raw)
        if rule_value in {"true", "false"}:
            status = (
                "agreed_true"
                if ai_f == rule_f and rule_f == 1.0
                else "agreed_false"
                if ai_f == rule_f
                else "disputed"
            )
        else:
            status = "agreed_true" if abs(ai_f - rule_f) <= 0.10 else "disputed"
        fused = rule_f + (ai_f - rule_f) * min(ai_confidence, 0.5)
        result[name] = {
            "fused": round(min(max(fused, 0.0), 1.0), 4),
            "source": status,
            "ai_confidence": ai_confidence,
            "rule_value": str(rule_value),
            "ai_value": ai_raw,
        }
    return result


def _apply_confidence_calibration(
    ai_result: dict[str, Any],
    source_diagnostics: dict[str, Any],
) -> int:
    """按「来源与完整性」硬上限压低 AI 置信度，返回被调低的项数。

    只做向下校准（min），永不提高。覆盖逐股票谓词置信度、实体关系置信度与
    AI 候选规则置信度；replay 路径不传 source_diagnostics，天然跳过。
    """
    cap = float(source_diagnostics.get("confidence_cap", 1.0))
    if cap >= 1.0:
        return 0
    calibrated_count = 0
    for stock in ai_result.get("stock_analyses", []) or []:
        if not isinstance(stock, dict):
            continue
        relationship_confidence = stock.get("relationship_confidence")
        if isinstance(relationship_confidence, (int, float)) and not isinstance(relationship_confidence, bool):
            lowered = min(relationship_confidence, cap)
            if lowered < relationship_confidence:
                stock["relationship_confidence"] = lowered
                calibrated_count += 1
        for predicate in stock.get("predicates", []) or []:
            if not isinstance(predicate, dict):
                continue
            confidence = predicate.get("confidence")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                lowered = min(confidence, cap)
                if lowered < confidence:
                    predicate["confidence"] = lowered
                    calibrated_count += 1
    for rule in ai_result.get("candidate_rules", []) or []:
        if not isinstance(rule, dict):
            continue
        confidence = rule.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            lowered = min(confidence, cap)
            if lowered < confidence:
                rule["confidence"] = lowered
                calibrated_count += 1
    return calibrated_count


def _is_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


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
    *,
    persist_ai_candidates: bool = True,
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
        # 实时路径可选字段：正文链接抓取的全文 + 确定性「来源与完整性」评估。
        "fetched_content": payload.get("fetched_content", "").strip(),
        "source_diagnostics": payload.get("source_diagnostics"),
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
    deterministic_event_type = infer_event_type(doc)
    entities = link_document(doc, stock_pool)
    ai_result = ai_analysis.get("result") if isinstance(ai_analysis, dict) else None
    # 来源与完整性驱动：按确定性上限校准 AI 置信度（仅在实时路径、有评估时生效）。
    source_diagnostics = doc.get("source_diagnostics")
    calibrated_count = 0
    if isinstance(ai_result, dict) and isinstance(source_diagnostics, dict):
        calibrated_count = _apply_confidence_calibration(ai_result, source_diagnostics)
    ai_event = ai_result.get("event", {}) if isinstance(ai_result, dict) else {}
    event_consensus = build_event_consensus(deterministic_event_type, ai_event)
    event_type = deterministic_event_type
    if isinstance(ai_result, dict):
        event_type = event_consensus["accepted_event_type"] or None
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
            "error": "事件一致性门控未通过，未生成候选因子",
            "entities": entities,
            "source_type": source_type,
            "ai_analysis": ai_analysis,
            "event_consensus": event_consensus,
        }
    if not entities:
        return {
            "error": "检测到事件，但未关联到股票池实体",
            "entities": [],
            "source_type": source_type,
            "ai_analysis": ai_analysis,
        }

    qualified_rules = [rule for rule in rules if rule["status"] == "qualified" and int(rule["support_count"]) >= 5]
    stock_results: list[dict[str, Any]] = []
    all_rules: dict[str, dict[str, Any]] = {}
    event_trace: list[dict[str, Any]] = []

    ai_stock_by_code = {
        str(row.get("code", "")): row
        for row in (ai_result.get("stock_analyses", []) if isinstance(ai_result, dict) else [])
        if isinstance(row, dict)
    }
    legacy_ai_predicates = list(ai_result.get("predicates", [])) if isinstance(ai_result, dict) else []
    impact_priors = load_impact_priors()

    # 逐股票相关性：以市值规模在该批次关联股票中的相对排名作为「行业代表度」信号。
    cap_by_code = {
        row["stock_code"]: float(row["market_cap"])
        for row in stock_pool
        if str(row.get("market_cap", "")).strip()
    }
    cohort_caps = sorted((cap_by_code.get(entity["stock_code"], 0.0) for entity in entities), reverse=True)

    def size_rank(code: str) -> float:
        cap = cap_by_code.get(code)
        if cap is None or not cohort_caps:
            return 0.5
        lo, hi = cohort_caps[-1], cohort_caps[0]
        if hi <= lo:
            return 0.5
        return (cap - lo) / (hi - lo)

    for index, entity in enumerate(entities, start=1):
        ai_stock = ai_stock_by_code.get(entity["stock_code"])
        entity_consensus = build_entity_consensus(
            entity,
            ai_stock,
            legacy_document_predicates=bool(legacy_ai_predicates),
        )
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
        }
        score_breakdown = evidence_score_breakdown(
            doc,
            event,
            entity,
            ai_stock.get("score_components", {}) if ai_stock else {},
        )
        evidence_strength = float(score_breakdown["score"])
        event["evidence_strength"] = f"{evidence_strength:.2f}"
        predicate_rows = ground_event_predicates(
            event,
            doc,
            entity["industry"],
            impact_prior=impact_priors.get(event_type, 0.50),
        )
        deterministic_map = {str(row["predicate_name"]): str(row["value"]) for row in predicate_rows}
        if isinstance(ai_result, dict):
            ai_predicates = list(ai_stock.get("predicates", [])) if ai_stock else legacy_ai_predicates
            consensus, _gated = build_predicate_consensus(
                predicate_rows,
                ai_predicates,
            )
            fused = fuse_predicate_values(deterministic_map, ai_predicates)
        else:
            consensus = []
            fused = {
                name: {
                    "fused": predicate_value_to_float(value),
                    "source": "rule_only",
                    "ai_confidence": 0.0,
                    "rule_value": value,
                    "ai_value": "",
                }
                for name, value in deterministic_map.items()
            }
        fused_map = {name: item["fused"] for name, item in fused.items()}
        gate_open = event_consensus["accepted"] and entity_consensus["accepted"]
        triggered = (
            [rule for rule in qualified_rules if rule_matches(rule, fused_map, event_type)]
            if gate_open
            else []
        )
        best_by_family: dict[str, dict[str, str]] = {}
        for rule in triggered:
            family = rule["target_label"]
            if family not in best_by_family or float(rule["score"]) > float(best_by_family[family]["score"]):
                best_by_family[family] = rule
        triggered = sorted(best_by_family.values(), key=lambda row: row["rule_id"])
        raw_score = sum(float(rule["score"]) for rule in triggered)
        ai_candidate_rows, ai_candidate_score = evaluate_ai_candidate_rules(
            ai_result, fused_map, gate_open
        )
        rule_score_sum = raw_score + ai_candidate_score
        impact_prior = float(deterministic_map["event_has_short_term_price_impact"])
        factor_multiplier = 0.7 * evidence_strength + 0.3 * impact_prior
        # 逐股票相关性系数：关联方式置信度 × AI 业务相关性，再叠加行业代表度（市值排名）。
        ai_business_relevance = (
            _bounded(ai_stock.get("score_components", {}).get("business_relevance", 0.0), 0.0)
            if ai_stock
            else 0.0
        )
        relevance = max(float(entity["confidence"]), ai_business_relevance)
        relevance_size_rank = size_rank(entity["stock_code"])
        stock_relevance = round(0.5 + 0.5 * (0.7 * relevance + 0.3 * relevance_size_rank), 4)
        factor_value = rule_score_sum * factor_multiplier * stock_relevance
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
        doc_text = f"{title}\n{content}"
        rule_explainability = [
            build_rule_explainability(
                rule["condition"],
                rule["target_label"],
                fused,
                consensus,
                doc_text,
                qualified_rules,
            )
            for rule in rule_rows
        ]
        rule_explainability.extend(
            build_rule_explainability(
                row["condition"],
                row["target_label"],
                fused,
                consensus,
                doc_text,
                qualified_rules,
                row,
            )
            for row in ai_candidate_rows
            if row["ai_candidate_score"] > 0
        )
        stock_results.append(
            {
                "code": entity["stock_code"],
                "name": entity["stock_name"],
                "sector": entity["industry"],
                "confidence": entity["confidence"],
                "link_evidence": entity["evidence"],
                "entity_consensus": entity_consensus,
                "factor_name": factor_name_for_labels(labels),
                "candidate_factor": round(factor_value, 6),
                "raw_score": round(raw_score, 6),
                "ai_candidate_rules": ai_candidate_rows,
                "factor_formula": {
                    "frozen_rule_score_sum": round(raw_score, 6),
                    "ai_candidate_rule_score": round(ai_candidate_score, 6),
                    "rule_score_sum": round(rule_score_sum, 6),
                    "evidence_strength": round(evidence_strength, 2),
                    "evidence_weight": 0.7,
                    "impact_prior": round(impact_prior, 2),
                    "impact_weight": 0.3,
                    "multiplier": round(factor_multiplier, 6),
                    "stock_relevance": stock_relevance,
                    "result": round(factor_value, 6),
                    "consensus_mode": "ai_rule_agreement" if isinstance(ai_result, dict) else "rule_only",
                },
                "stock_relevance": stock_relevance,
                "relevance_signals": {
                    "match_confidence": round(float(entity["confidence"]), 4),
                    "ai_business_relevance": round(ai_business_relevance, 4),
                    "size_rank": round(relevance_size_rank, 4),
                },
                "evidence_score_breakdown": score_breakdown,
                "predicates": serialize_predicates(predicate_rows),
                "predicate_consensus": consensus,
                "predicate_fusion": fused,
                "triggered_rules": rule_rows,
                "rule_explainability": rule_explainability,
                "event": event,
            }
        )
        event_trace.append(event)

    if persist_ai_candidates and isinstance(ai_result, dict):
        persist_ai_candidate_rules(doc, ai_result.get("candidate_rules", []))

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
        "evidence_strength": round(
            max((float(stock["event"]["evidence_strength"]) for stock in stock_results), default=0.0),
            2,
        ),
        "source_type": source_type,
        "source_name": source_name,
        "source_url": doc["url"],
        "source_audit": source_diagnostics,
        "confidence_calibrated_count": calibrated_count,
        "entities": entities,
        "events": event_trace,
        "stock_results": stock_results,
        "triggered_rules": list(all_rules.values()),
        "analysis_mode": "hybrid_ai_rule_validation" if ai_analysis.get("used") else "deterministic_rule_only",
        "ai_analysis": ai_analysis,
        "event_consensus": event_consensus,
        "entity_consensus": [stock["entity_consensus"] for stock in stock_results],
        "predicate_consensus": stock_results[0].get("predicate_consensus", []) if stock_results else [],
        "disputed_predicates": disputed,
        "consensus_gate_passed": (
            bool(ai_analysis.get("used"))
            and event_consensus["accepted"]
            and all(stock["entity_consensus"]["accepted"] for stock in stock_results)
            and not disputed
        ),
    }
