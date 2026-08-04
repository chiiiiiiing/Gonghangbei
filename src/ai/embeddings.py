"""Optional local BGE embeddings with an explicit offline-safe load policy."""

from __future__ import annotations

import os
from typing import Any


BGE_MODEL = "BAAI/bge-small-zh-v1.5"
_MODEL: Any | None = None


def bge_embeddings(texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
    """Encode texts with BGE; model download requires an explicit environment flag."""
    global _MODEL
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("未安装 sentence-transformers") from exc

    allow_download = os.getenv("ALPHALENS_BGE_ALLOW_DOWNLOAD", "0") == "1"
    if _MODEL is None:
        try:
            _MODEL = SentenceTransformer(
                BGE_MODEL,
                local_files_only=not allow_download,
            )
        except Exception as exc:
            mode = "允许下载" if allow_download else "仅本地缓存"
            raise RuntimeError(f"BGE 模型加载失败（{mode}）：{exc}") from exc
    vectors = _MODEL.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [vector.tolist() for vector in vectors], {
        "model": BGE_MODEL,
        "backend": "sentence-transformers",
        "fallback": False,
        "reason": "",
    }
