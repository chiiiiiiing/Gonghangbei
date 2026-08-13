"""逐篇调用 DeepSeek，生成可恢复、可审计的历史 AI 标注缓存。"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ai.gateway import AISettings
from src.ai.prompts import PROMPT_VERSION
from src.ai.research_layer import AIResearchLayer


ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "data" / "sample"
CACHE_PATH = SAMPLE_DIR / "ai_annotations.jsonl"
MODEL = "deepseek-v4-flash"


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SAMPLE_DIR / filename).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def document_hash(document: dict[str, str]) -> str:
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_key(document: dict[str, str]) -> str:
    raw = f"{document['doc_id']}|{document_hash(document)}|{PROMPT_VERSION}|{MODEL}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        records[str(record["cache_key"])] = record
    return records


def latest_record_for_document(
    cache: dict[str, dict[str, Any]],
    document: dict[str, str],
    exact_key: str,
) -> dict[str, Any] | None:
    """Find the newest record for unchanged input, across prompt versions.

    R4.1 improves the retry contract.  Existing successful strict records are
    retained instead of duplicated solely because a repair prompt version
    changed; ``--retry-failed`` therefore targets only failed documents.
    """
    exact = cache.get(exact_key)
    if exact is not None:
        return exact
    expected_hash = document_hash(document)
    matching = [
        record
        for record in cache.values()
        if record.get("doc_id") == document["doc_id"]
        and record.get("document_hash") == expected_hash
    ]
    if not matching:
        return None
    return max(matching, key=lambda record: str(record.get("generated_at", "")))


def latest_records_by_document(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Deduplicate append-only retries for accurate cache coverage reporting."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = (str(record.get("doc_id", "")), str(record.get("document_hash", "")))
        if not key[0]:
            continue
        previous = latest.get(key)
        if previous is None or str(record.get("generated_at", "")) >= str(previous.get("generated_at", "")):
            latest[key] = record
    return latest


def append_record(record: dict[str, Any]) -> None:
    with CACHE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 AlphaLens 历史 DeepSeek 标注缓存")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理多少篇；0 表示全部")
    parser.add_argument("--retry-failed", action="store_true", help="重试此前失败的记录")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        api_key = getpass.getpass("DeepSeek API Key（仅驻留本进程，不写入文件）：").strip()
    if not api_key:
        raise SystemExit("未提供 DeepSeek API Key")
    settings = AISettings(
        mode="api",
        base_url="https://api.deepseek.com",
        api_key=api_key,
        chat_model=MODEL,
        embedding_model="",
        timeout_seconds=90,
        json_mode="object",
    )
    layer = AIResearchLayer(settings)
    stock_pool = read_csv("stock_pool.csv")
    rules = read_csv("rules.csv")
    cache = load_cache()
    processed = 0
    for document in read_csv("raw_documents.csv"):
        key = cache_key(document)
        existing = latest_record_for_document(cache, document, key)
        if existing and (existing.get("status") == "success" or not args.retry_failed):
            continue
        result = layer.analyze(document, stock_pool, rules)
        record = {
            "cache_key": key,
            "doc_id": document["doc_id"],
            "document_hash": document_hash(document),
            "prompt_version": PROMPT_VERSION,
            "requested_model": MODEL,
            "returned_model": result.get("returned_model", ""),
            "request_id": result.get("request_id", ""),
            "system_fingerprint": result.get("system_fingerprint", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "success" if result.get("used") else "failed",
            "reason": result.get("reason", ""),
            "analysis": result.get("result"),
            "validation": result.get("validation", {}),
        }
        append_record(record)
        cache[key] = record
        processed += 1
        print(f"{document['doc_id']}: {record['status']}")
        if args.limit and processed >= args.limit:
            break
    success_count = sum(
        record.get("status") == "success"
        for record in latest_records_by_document(list(cache.values())).values()
    )
    print(f"本次处理 {processed} 篇；当前有效缓存 {success_count} 篇。API Key 未写入磁盘。")


if __name__ == "__main__":
    main()
