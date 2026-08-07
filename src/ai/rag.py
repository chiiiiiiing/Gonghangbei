"""In-memory RAG index over the historical AI annotation cache.

Lazy-loads data/sample/ai_annotations.jsonl (produced by 批量生成AI标注.py) and
retrieves the most similar historical AI conclusions for a live query, using the
same deterministic character n-gram embedding as the rule retriever — no new
third-party dependency. Returns [] when the cache is missing or empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_PATH = ROOT / "data" / "sample" / "ai_annotations.jsonl"

_index: dict[str, Any] | None = None


def _record_text(record: dict[str, Any]) -> str:
    analysis = record.get("analysis") or {}
    event = analysis.get("event") or {}
    return json.dumps(
        {
            "doc_id": record.get("doc_id", ""),
            "summary": analysis.get("summary", ""),
            "event_type": event.get("event_type", ""),
            "event_object": event.get("object", ""),
        },
        ensure_ascii=False,
    )


def load_index(path: Path | None = None) -> dict[str, Any]:
    global _index
    if _index is not None:
        return _index
    target = path or ANNOTATION_PATH
    records: list[dict[str, Any]] = []
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") != "success" or not record.get("analysis"):
                continue
            records.append(record)
    _index = {"records": records, "vectors": None}
    return _index


def _build_vectors(index: dict[str, Any]) -> list[list[float]]:
    if index["vectors"] is not None:
        return index["vectors"]
    from src.ai.research_layer import local_text_embedding

    index["vectors"] = [local_text_embedding(_record_text(record)) for record in index["records"]]
    return index["vectors"]


def retrieve(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Return the top-k most similar historical AI conclusions for a query."""
    index = load_index()
    if not index["records"]:
        return []
    from src.ai.research_layer import cosine_similarity, local_text_embedding

    vectors = _build_vectors(index)
    query_vector = local_text_embedding(query)
    scored: list[dict[str, Any]] = []
    for record, vector in zip(index["records"], vectors):
        analysis = record.get("analysis") or {}
        event = analysis.get("event") or {}
        scored.append(
            {
                "doc_id": str(record.get("doc_id", "")),
                "similarity": round(cosine_similarity(query_vector, vector), 6),
                "summary": str(analysis.get("summary", "")),
                "event_type": str(event.get("event_type", "")),
                "event_object": str(event.get("object", "")),
            }
        )
    scored.sort(key=lambda item: item["similarity"], reverse=True)
    return scored[:top_k]


def reset() -> None:
    """Clear the cached index (mainly for tests)."""
    global _index
    _index = None
