"""用 DeepSeek 生成可恢复、可审计的宏观谓词和动态规则缓存。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ai.gateway import AISettings
from src.macro.ai_rules import MACRO_PROMPT_VERSION, MacroAIRuleLayer
from src.macro.features import deduplicate_documents
from src.macro.schema import period_for_date


ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "data" / "sample"
CACHE_PATH = SAMPLE_DIR / "macro_ai_annotations.jsonl"


def read_documents() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for filename in ("macro_historical_documents.csv", "raw_documents.csv"):
        path = SAMPLE_DIR / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def document_hash(document: dict[str, str]) -> str:
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_key(document: dict[str, str], model: str) -> str:
    raw = f"{document['doc_id']}|{document_hash(document)}|{MACRO_PROMPT_VERSION}|{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        result[str(record["cache_key"])] = record
    return result


def append_record(record: dict[str, Any]) -> None:
    with CACHE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 AlphaLens 宏观 AI 标注缓存")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理篇数；0 表示全部")
    parser.add_argument("--retry-failed", action="store_true", help="重试此前失败记录")
    parser.add_argument("--workers", type=int, default=4, help="并发请求数，默认 4")
    args = parser.parse_args()

    settings = AISettings.from_environment()
    if not settings.enabled:
        raise SystemExit("未配置 DeepSeek API Key；请使用本地 .env 或环境变量")
    layer = MacroAIRuleLayer(settings)
    cache = load_cache()
    documents, dropped = deduplicate_documents(read_documents())
    pending: list[tuple[dict[str, str], str]] = []
    for document in documents:
        key = cache_key(document, settings.chat_model)
        existing = cache.get(key)
        if existing and (existing.get("status") == "success" or not args.retry_failed):
            continue
        pending.append((document, key))
        if args.limit and len(pending) >= args.limit:
            break

    def analyze_document(document: dict[str, str], key: str) -> dict[str, Any]:
        result = layer.analyze(document)
        period = period_for_date(document["publish_time"])
        return {
            "cache_key": key,
            "doc_id": document["doc_id"],
            "period_end": period["period_end"],
            "document_hash": document_hash(document),
            "prompt_version": MACRO_PROMPT_VERSION,
            "requested_model": settings.chat_model,
            "returned_model": result.get("returned_model", ""),
            "request_id": result.get("request_id", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "success" if result.get("used") else "failed",
            "reason": result.get("reason", ""),
            "analysis": result.get("result"),
        }

    processed = 0
    workers = max(1, min(args.workers, 8))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(analyze_document, document, key): (document, key)
            for document, key in pending
        }
        for future in as_completed(futures):
            document, key = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001 - record unexpected worker failure
                period = period_for_date(document["publish_time"])
                record = {
                    "cache_key": key,
                    "doc_id": document["doc_id"],
                    "period_end": period["period_end"],
                    "document_hash": document_hash(document),
                    "prompt_version": MACRO_PROMPT_VERSION,
                    "requested_model": settings.chat_model,
                    "returned_model": "",
                    "request_id": "",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "reason": f"工作线程异常：{exc}",
                    "analysis": None,
                }
            append_record(record)
            cache[key] = record
            processed += 1
            print(f"{document['doc_id']}: {record['status']}", flush=True)
    success = sum(record.get("status") == "success" for record in cache.values())
    print(f"本次处理 {processed} 篇；有效宏观 AI 缓存 {success} 篇；去重排除 {len(dropped)} 篇。")


if __name__ == "__main__":
    main()
