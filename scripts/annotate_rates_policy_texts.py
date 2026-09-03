"""Build resumable, evidence-validated LLM annotations for official policy texts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.gateway import AIServiceError, AISettings  # noqa: E402
from src.rates.llm import extract_with_llm  # noqa: E402


TEXT_PATH = ROOT / "data" / "sample" / "rates_policy_texts.csv"
OUT = ROOT / "data" / "sample" / "rates_llm_annotations.jsonl"


def _existing() -> dict[tuple[str, str], dict[str, Any]]:
    if not OUT.exists():
        return {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[(str(row["doc_id"]), str(row["source_sha256"]))] = row
    return rows


def _annotate(document: dict[str, str]) -> dict[str, Any]:
    result = extract_with_llm(document)
    return {
        "doc_id": document["doc_id"], "source_sha256": document["source_sha256"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "used": bool(result.get("used")), "reason": result.get("reason", ""),
        "summary": result.get("summary", ""), "events": result.get("events", []),
        "predicates": result.get("predicates", []), "metadata": result.get("metadata", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--source-name", default="", help="只标注指定来源，便于增量补跑")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    settings = AISettings.from_environment()
    if not settings.enabled:
        raise SystemExit("未配置可用的大模型API，拒绝生成伪LLM缓存")
    with TEXT_PATH.open(encoding="utf-8", newline="") as handle:
        documents = list(csv.DictReader(handle))
    existing = {} if args.refresh else _existing()
    pending = [
        row for row in documents
        if (row["doc_id"], row["source_sha256"]) not in existing
        and (not args.source_name or row["source_name"] == args.source_name)
    ]
    if args.limit > 0:
        pending = pending[:args.limit]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    with OUT.open("a", encoding="utf-8") as output, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_annotate, row): row["doc_id"] for row in pending}
        for future in as_completed(futures):
            try:
                annotation = future.result()
            except (AIServiceError, OSError, ValueError) as exc:
                print(f"warning: {futures[future]}: {exc}", file=sys.stderr)
                continue
            output.write(json.dumps(annotation, ensure_ascii=False) + "\n")
            output.flush()
            completed += 1
            if completed % 25 == 0:
                print(f"annotated {completed}/{len(pending)}")
    print(f"wrote {completed} new annotations; {len(existing)} reusable cache entries")


if __name__ == "__main__":
    main()
