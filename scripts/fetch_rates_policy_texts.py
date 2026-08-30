"""Fetch a small, source-hashed PBOC policy-text fixture for the MVP."""

from __future__ import annotations

import csv
import hashlib
import html
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import TEXT_FIELDS  # noqa: E402


URLS = [
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2025122408552117668/index.html",
]
OUT = ROOT / "data" / "sample" / "rates_policy_texts.csv"


def _meta(source: str, name: str) -> str:
    match = re.search(
        rf'<meta\s+(?:name|Name)=["\']{re.escape(name)}["\']\s+content=["\'](.*?)["\']\s*/?>',
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return html.unescape(match.group(1).strip()) if match else ""


def _policy_paragraph(source: str) -> str:
    for raw in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", source, flags=re.IGNORECASE | re.DOTALL):
        text = html.unescape(re.sub(r"<[^>]+>", "", raw))
        text = re.sub(r"\s+", "", text).strip()
        if "中国人民银行" in text and "逆回购操作" in text:
            return text
    return ""


def main() -> None:
    rows: list[dict[str, str]] = []
    for index, url in enumerate(URLS, 1):
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/1.0"})
        with urlopen(request, timeout=45) as response:
            body = response.read()
        source = body.decode("utf-8", errors="replace")
        title = _meta(source, "ArticleTitle")
        publish_date = _meta(source, "PubDate")
        content = _policy_paragraph(source) or _meta(source, "Description")
        created = _meta(source, "createDate")
        if not title or not publish_date or not content:
            raise ValueError(f"央行页面元数据不完整：{url}")
        publish_time = created if created.startswith(publish_date) else publish_date + " 09:00:00"
        rows.append({
            "doc_id": f"PBC-OMO-{publish_date.replace('-', '')}-{index:02d}",
            "publish_time": publish_time,
            "title": title,
            "content": content,
            "source_name": "中国人民银行",
            "source_url": url,
            "source_sha256": hashlib.sha256(body).hexdigest(),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEXT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} official policy text(s) to {OUT}")


if __name__ == "__main__":
    main()
