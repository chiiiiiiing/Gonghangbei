"""Generate frozen lithium annotations with a local instruction-tuned LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lithium.engine import (  # noqa: E402
    DISCOVERY_END,
    PREDICATE_DEFINITIONS,
    PROSPECTIVE_START,
    SIGNAL_FIELDS,
    _induction_records,
    _read_csv,
    _write_csv,
    analyze_document,
    build_lithium_outputs,
    build_main_continuous,
    forward_label,
    induce_rulebook,
    validate_controlled_data,
)


CACHE_DIR = ROOT / "data" / "raw" / "lithium_local_llm"
PROMPT_VERSION = "local-qwen-two-pass-v5-balanced-warehouse"
AUDIT_FIELDS = [
    "doc_id", "publish_time", "status", "model", "request_id", "error",
    "annotation_input_sha256", "prompt_version", "annotated_at",
]
REUSABLE_REJECTION_STATUSES = {"rejected", "recovered_prior_rejection"}


def annotation_fingerprint(
    document: dict[str, str],
    rules: list[dict[str, Any]],
    model_name: str,
) -> str:
    source_text = f"{document.get('title', '')}\n{document.get('content', '')}"[:6000]
    identity = json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "source_text": source_text,
            "rules": rules,
            "model": model_name,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def eligible_for_annotation(
    document: dict[str, str],
    continuous: list[dict[str, Any]],
    contracts: list[dict[str, str]],
) -> bool:
    if document.get("review_status") != "accepted":
        return False
    if document.get("publish_time", "") >= PROSPECTIVE_START.isoformat():
        return True
    return forward_label(document["publish_time"], continuous, contracts=contracts) is not None


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("本地模型未返回 JSON object")
    payload, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("本地模型 JSON 顶层必须是 object")
    return payload


class LocalQwenGateway:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.settings = SimpleNamespace(chat_model=model_name)
        self.device = None
        self.tokenizer = None
        self.model = None

    def _ensure_loaded(self) -> None:
        if self.model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        dtype = torch.float16 if self.device.type == "mps" else "auto"
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, dtype=dtype).to(self.device)
        self.model.eval()

    def _generate(self, prompt: str, max_new_tokens: int) -> str:
        import torch

        self._ensure_loaded()
        assert self.tokenizer is not None and self.model is not None and self.device is not None

        messages = [
            {"role": "system", "content": "你是审慎的碳酸锂产业文本标注模型，只输出一个JSON对象。"},
            {"role": "user", "content": prompt},
        ]
        rendered = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(rendered, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                repetition_penalty=1.03,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = output[0, inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(generated, skip_special_tokens=True)

    def chat_json(
        self,
        messages: list[dict[str, str]],
        _schema: dict[str, Any],
        _schema_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        task = json.loads(messages[-1]["content"])
        document = task["document"]
        source_text = f"{document.get('title', '')}\n{document.get('content', '')}"[:6000]
        rules = task.get("frozen_rulebook") or []
        cache_identity = json.dumps(
            {"prompt_version": PROMPT_VERSION, "source_text": source_text, "rules": rules},
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()
        cache_path = CACHE_DIR / f"{digest}.json"
        if cache_path.exists():
            compact = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            definitions = "\n".join(f"- {name}: {definition}" for name, definition in PREDICATE_DEFINITIONS.items())
            predicate_prompt = f"""只做产业谓词证据抽取，不预测方向。只能使用输入文本。
true_predicates 必须是数组且最多5项；每项 name 只能选下列英文谓词名，evidence_text 必须逐字复制连续原文。只选最明确、与碳酸锂最相关的证据，没有明确证据的谓词不要输出。
仓单增加与仓单减少是互斥方向，不能混用：增加只对应 warehouse_receipt_increase，减少只对应 warehouse_receipt_decline。
示例输入“广州期货交易所仓单增加100手”的输出是：
{{"true_predicates":[{{"name":"warehouse_receipt_increase","evidence_text":"仓单增加100手"}},{{"name":"authoritative_source","evidence_text":"广州期货交易所"}},{{"name":"quantitative_evidence","evidence_text":"100手"}}]}}
示例输入“广州期货交易所仓单减少100手”的输出是：
{{"true_predicates":[{{"name":"warehouse_receipt_decline","evidence_text":"仓单减少100手"}},{{"name":"authoritative_source","evidence_text":"广州期货交易所"}},{{"name":"quantitative_evidence","evidence_text":"100手"}}]}}

谓词定义：
{definitions}

输入文本：
{source_text}

只输出同结构 JSON。"""
            legacy_identity = json.dumps(
                {"prompt_version": "local-qwen-two-pass-v2", "source_text": source_text, "rules": rules},
                ensure_ascii=False,
                sort_keys=True,
            )
            legacy_path = CACHE_DIR / f"{hashlib.sha256(legacy_identity.encode('utf-8')).hexdigest()}.json"
            if legacy_path.exists() and document.get("source_name") != "广州期货交易所":
                legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                predicate_payload = {"true_predicates": legacy.get("true_predicates")}
            else:
                predicate_payload = extract_json(self._generate(predicate_prompt, 360))
            if not isinstance(predicate_payload.get("true_predicates"), list):
                raise ValueError("本地模型缺少 true_predicates 数组")
            concise_rules = [
                {
                    "target_label": rule.get("target_label"),
                    "conditions": rule.get("conditions"),
                    "score": rule.get("score"),
                }
                for rule in rules
            ]
            extracted_names = {
                str(item.get("name", ""))
                for item in predicate_payload["true_predicates"]
                if isinstance(item, dict)
            }
            candidate_rules = [
                rule for rule in concise_rules
                if set(rule.get("conditions") or []).issubset(extracted_names)
            ]
            direction_prompt = f"""预测以下文本公开后碳酸锂期货未来5个交易日方向。只用当时文本；不提供交易建议。
先给不看规则的 zero_shot_label/zero_shot_score，再结合冻结规则给 direction_label/direction_score。
标签只能是 bullish、bearish、neutral；bullish 分数为正，bearish 为负，neutral 为0；confidence 在0到1；evidence_text 必须逐字复制连续原文。
若候选冻结规则非空，规则增强方向必须遵循 score 最高规则的 target_label；若为空，规则增强方向等于零样本方向。
示例输出：{{"zero_shot_label":"bearish","zero_shot_score":-0.6,"direction_label":"bearish","direction_score":-0.8,"confidence":0.8,"evidence_text":"仓单增加100手"}}

LLM已抽取为真的谓词：
{json.dumps(sorted(extracted_names), ensure_ascii=False)}

候选冻结规则：
{json.dumps(candidate_rules, ensure_ascii=False, separators=(',', ':'))}

输入文本：
{source_text}

只输出同结构 JSON。"""
            direction_payload = extract_json(self._generate(direction_prompt, 180))
            if candidate_rules:
                required_rule = max(candidate_rules, key=lambda item: float(item.get("score") or 0))
                required_label = str(required_rule["target_label"])
                if direction_payload.get("direction_label") != required_label:
                    evidence_example = next(
                        (
                            str(item.get("evidence_text", ""))
                            for item in predicate_payload["true_predicates"]
                            if isinstance(item, dict) and item.get("evidence_text")
                        ),
                        document.get("title", ""),
                    )
                    score_example = -0.6 if required_label == "bearish" else 0.6
                    repair_prompt = f"""上一回答违反冻结规则约束。最高分候选规则要求 direction_label 必须等于 {required_label}。
这是格式修复，不重新选择规则。direction_score 必须与标签同号且非0，evidence_text 必须逐字复制输入文本。
只输出：{{"zero_shot_label":"neutral","zero_shot_score":0,"direction_label":"{required_label}","direction_score":{score_example},"confidence":0.8,"evidence_text":{json.dumps(evidence_example, ensure_ascii=False)}}}

输入文本：
{source_text}"""
                    direction_payload = extract_json(self._generate(repair_prompt, 120))
                if direction_payload.get("direction_label") != required_label:
                    raise ValueError("本地模型规则增强方向违反最高分冻结规则")
            compact = {**direction_payload, "true_predicates": predicate_payload["true_predicates"]}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        label = str(compact.get("direction_label", "neutral"))
        if label not in {"bullish", "bearish", "neutral"}:
            raise ValueError("本地模型 direction_label 不合法")
        try:
            score = max(-1.0, min(1.0, float(compact.get("direction_score", 0))))
            zero_shot_score = max(-1.0, min(1.0, float(compact.get("zero_shot_score", score))))
            confidence = max(0.0, min(1.0, float(compact.get("confidence", 0))))
        except (TypeError, ValueError) as exc:
            raise ValueError("本地模型分数不是数值") from exc
        if label == "bullish":
            score = abs(score)
        elif label == "bearish":
            score = -abs(score)
        else:
            score = 0.0
        zero_shot_label = str(compact.get("zero_shot_label", label))
        if zero_shot_label == "bullish":
            zero_shot_score = abs(zero_shot_score)
        elif zero_shot_label == "bearish":
            zero_shot_score = -abs(zero_shot_score)
        else:
            zero_shot_score = 0.0
        true_evidence: dict[str, str] = {}
        for item in compact.get("true_predicates", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            evidence = str(item.get("evidence_text", "")).strip()
            if name in PREDICATE_DEFINITIONS and evidence and evidence in source_text:
                true_evidence[name] = evidence
        direction_evidence = str(compact.get("evidence_text", "")).strip()
        if direction_evidence not in source_text:
            direction_evidence = next(iter(true_evidence.values()), document.get("title", ""))
        expanded = {
            "direction_label": label,
            "direction_score": score,
            "zero_shot_score": zero_shot_score,
            "confidence": confidence,
            "horizon_days": 5,
            "evidence_text": direction_evidence,
            "predicates": [
                {
                    "name": name,
                    "value": name in true_evidence,
                    "confidence": confidence if name in true_evidence else max(0.5, confidence),
                    "evidence_text": true_evidence.get(name, ""),
                }
                for name in PREDICATE_DEFINITIONS
            ],
        }
        return expanded, {
            "model": self.model_name,
            "request_id": f"local-{digest[:16]}",
            "usage": {},
        }


def signal_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": result["doc_id"], "publish_time": result["publish_time"],
        "direction_label": result["direction_label"], "direction_score": result["direction_score"],
        "zero_shot_score": result["zero_shot_score"], "confidence": result["confidence"],
        "horizon_days": result["horizon_days"],
        "activated_rules": json.dumps(result["activated_rules"], ensure_ascii=False, separators=(",", ":")),
        "predicate_consensus": json.dumps(result["predicate_consensus"], ensure_ascii=False, separators=(",", ":")),
        "evidence_text": result["evidence_text"], "inference_mode": result["inference_mode"],
        "model": result["model"], "request_id": result["request_id"],
    }


def annotate_group(
    documents: list[dict[str, str]],
    gateway: LocalQwenGateway,
    rulebook: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    audit: dict[str, dict[str, Any]],
    retry_rejected: bool,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, document in enumerate(documents, 1):
        fingerprint = annotation_fingerprint(document, rulebook, gateway.model_name)
        if document["doc_id"] in existing:
            output.append(existing[document["doc_id"]])
            if document["doc_id"] in audit:
                audit[document["doc_id"]].update({
                    "annotation_input_sha256": fingerprint,
                    "prompt_version": PROMPT_VERSION,
                })
            continue
        prior = audit.get(document["doc_id"], {})
        if (
            not retry_rejected
            and prior.get("status") in REUSABLE_REJECTION_STATUSES
            and prior.get("annotation_input_sha256") == fingerprint
            and prior.get("prompt_version") == PROMPT_VERSION
        ):
            continue
        try:
            result = analyze_document(document, gateway, rulebook, contexts)
            row = signal_row(result)
            output.append(row)
            audit[document["doc_id"]] = {
                "doc_id": document["doc_id"], "publish_time": document["publish_time"],
                "status": "accepted", "model": result["model"], "request_id": result["request_id"],
                "error": "", "annotation_input_sha256": fingerprint, "prompt_version": PROMPT_VERSION,
                "annotated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        except (ValueError, RuntimeError) as exc:
            audit[document["doc_id"]] = {
                "doc_id": document["doc_id"], "publish_time": document["publish_time"],
                "status": "rejected", "model": gateway.model_name, "request_id": "",
                "error": str(exc), "annotation_input_sha256": fingerprint, "prompt_version": PROMPT_VERSION,
                "annotated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        merged = {**existing, **{row["doc_id"]: row for row in output}}
        _write_csv("lithium_text_signals.csv", SIGNAL_FIELDS, merged.values())
        _write_csv("lithium_local_llm_audit.csv", AUDIT_FIELDS, audit.values())
        if index % 10 == 0 or index == len(documents):
            print(f"annotated {index}/{len(documents)} documents", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retry-rejected", action="store_true")
    args = parser.parse_args()
    texts = _read_csv("lithium_texts.csv")
    contracts = _read_csv("lithium_contract_daily.csv")
    warehouse = _read_csv("lithium_warehouse_receipts.csv")
    errors = validate_controlled_data(texts, contracts, warehouse)
    if errors:
        raise SystemExit("受控数据校验失败：\n" + "\n".join(errors))
    continuous = build_main_continuous(contracts)
    accepted = [
        row for row in sorted(texts, key=lambda item: (item["publish_time"], item["doc_id"]))
        if eligible_for_annotation(row, continuous, contracts)
    ]
    if args.limit:
        accepted = accepted[:args.limit]
    gateway = LocalQwenGateway(args.model)
    existing_rows = _read_csv("lithium_text_signals.csv")
    existing = {row["doc_id"]: row for row in existing_rows if row.get("doc_id") in {item["doc_id"] for item in accepted}}
    prior_audit = _read_csv("lithium_local_llm_audit.csv")
    audit = {row["doc_id"]: row for row in prior_audit if row.get("doc_id")}
    contexts: list[dict[str, Any]] = []
    discovery_docs = [row for row in accepted if row["publish_time"] <= DISCOVERY_END.isoformat()]
    discovery = annotate_group(
        discovery_docs, gateway, [], contexts, existing, audit, args.retry_rejected
    )
    for document in discovery_docs:
        realized = forward_label(document["publish_time"], continuous, contracts=contracts)
        if realized:
            contexts.append({**document, "direction_label": realized["direction_label"]})
    induction, _ = _induction_records(texts, [*existing.values(), *discovery], continuous, contracts)
    rulebook = induce_rulebook(induction)
    later_docs = [row for row in accepted if row["publish_time"] > DISCOVERY_END.isoformat()]
    frozen_discovery = {row["doc_id"]: row for row in [*existing.values(), *discovery]}
    later = annotate_group(
        later_docs, gateway, rulebook, contexts, frozen_discovery, audit, args.retry_rejected
    )
    combined = {row["doc_id"]: row for row in [*existing.values(), *discovery, *later]}
    _write_csv("lithium_text_signals.csv", SIGNAL_FIELDS, combined.values())
    _write_csv("lithium_local_llm_audit.csv", AUDIT_FIELDS, audit.values())
    summary = build_lithium_outputs()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
