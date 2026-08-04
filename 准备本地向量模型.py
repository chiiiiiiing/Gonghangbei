"""显式下载并缓存 AlphaLens 使用的中文 BGE 模型。"""

from __future__ import annotations

import os

from src.ai.embeddings import BGE_MODEL, bge_embeddings


def main() -> None:
    os.environ["ALPHALENS_BGE_ALLOW_DOWNLOAD"] = "1"
    vectors, metadata = bge_embeddings(["AlphaLens 中文金融文本语义检索"])
    if not vectors or not vectors[0]:
        raise RuntimeError("BGE 模型未返回向量")
    print(f"本地向量模型已就绪：{metadata['model']}，维度 {len(vectors[0])}")


if __name__ == "__main__":
    main()
