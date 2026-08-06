"""抓取真实网页正文并追加到 AlphaLens 采集清单。

给定一个「待采集清单」（锁定字段，content 可为空），逐条用 curl 抓取详情页，
清洗出正文摘要后回填 content 字段，产出可交给 `预演导入.py` 与
`导入真实文本.py` 的完整清单。

- content 格式与现有 raw_documents.csv 一致：`原文摘要：<正文摘录> 来源为<来源>，首次公开日期核验为<日期>。`
- 不添加「项目关联：」注释（代码 split 剥离逻辑对缺失的标记天然兼容）。
- 抓取失败的行保留空 content 并在报告中标出，便于人工补抓。

用法：
    .venv/bin/python 采集正文.py 待采集清单.csv --out 采集清单.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from src.ingestion.text_import import FIELDS

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT_SECONDS = 20
MAX_CONTENT = 800  # 正文摘要目标长度上限（字符）
NAV_MARKERS = (
    "首页", "无障碍", "网站标识码", "主办单位", "版权所有", "京ICP备",
    "链接：", "相关附件", "责任编辑：", "上一篇", "下一篇", "新浪微博",
    "微信公众号", "责任编辑", "Copyright", "备案号", "版权所有", "中国政府网 |",
)


def fetch(url: str) -> str:
    result = subprocess.run(
        ["curl", "-s", "-L", "-m", str(TIMEOUT_SECONDS), "-A", UA, url],
        capture_output=True,
        timeout=TIMEOUT_SECONDS + 10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl 失败：{result.stderr[:200]}")
    raw = result.stdout
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("无法识别网页编码")


def clean_text(html_text: str) -> tuple[str, str]:
    """Return (title, body) cleaned of scripts/styles/nav noise.

    body is the concatenation of the article's leading paragraphs (deduplicated
    and anchored at the block matching the title), so long documents keep their
    key clauses — which matters because policy/event keyword detection scans the
    whole content field.
    """
    import html as html_lib

    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.S | re.I)
    title_match = re.search(r"<title>(.*?)</title>", raw, flags=re.S | re.I)
    title = re.sub(r"\s+", " ", html_lib.unescape(title_match.group(1))).strip() if title_match else ""
    blocks = [
        re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        for _tag, inner in re.findall(r"<(p|div)[^>]*>(.*?)</\1>", raw, flags=re.S | re.I)
    ]
    blocks = [b for b in blocks if len(b) >= 20 and not any(marker in b for marker in NAV_MARKERS)]
    deduped: list[str] = []
    for block in blocks:
        if not deduped or block != deduped[-1]:
            deduped.append(block)
    if not deduped:
        return title, ""
    title_terms = [term for term in re.split(r"[《》\s（）,，]", title) if len(term) >= 4]
    anchor = 0
    for index, block in enumerate(deduped):
        if any(term in block for term in title_terms):
            anchor = index
            break
    return title, "".join(deduped[anchor:])


def truncate_at_boundary(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for boundary in ("。", "；", "；", "！", "？"):
        index = cut.rfind(boundary)
        if index >= max_len * 0.5:
            return cut[: index + 1]
    return cut


def main() -> int:
    parser = argparse.ArgumentParser(description="AlphaLens 网页正文抓取")
    parser.add_argument("input", type=Path, help="待采集清单 CSV（content 可空）")
    parser.add_argument("--out", type=Path, default=Path("采集清单.csv"), help="输出清单路径")
    args = parser.parse_args()

    with args.input.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            print(f"清单字段必须严格等于：{','.join(FIELDS)}", file=sys.stderr)
            return 1
        rows = list(reader)

    failed: list[str] = []
    fetched = 0
    for row in rows:
        if row["content"].strip():
            continue  # 已有正文，跳过
        url = row["url"].strip()
        try:
            title, body = clean_text(fetch(url))
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{row['doc_id']} {url}: {exc}")
            continue
        excerpt = truncate_at_boundary(body, MAX_CONTENT)
        if not excerpt:
            failed.append(f"{row['doc_id']} {url}: 未抽取到正文")
            continue
        date = row["publish_time"].strip()
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            date = ""
        row["content"] = (
            f"原文摘要：{excerpt} 来源为{row['source_name']}，首次公开日期核验为{date}。"
        )
        fetched += 1
        print(f"{row['doc_id']}: 已抓取 {len(excerpt)} 字")

    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n完成：抓取 {fetched} 篇，输出 {args.out}")
    if failed:
        print("以下条目抓取失败（content 留空，请人工补抓）：")
        for note in failed:
            print(f"- {note}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
