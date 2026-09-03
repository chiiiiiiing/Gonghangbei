"""Add official NBS and MOF documents to the rates text corpus.

The existing PBOC crawler is intentionally kept separate because its list
pages have different pagination and rate limits. This augmentation retains
verified NBS documents already present in the rates corpus, optionally imports
the legacy NBS manifest when available, and refreshes a small official MOF
seed list. Raw MOF responses are hashed before their normalized text is merged.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.schema import TEXT_FIELDS, validate_text_row  # noqa: E402
DATA_DIR = ROOT / "data" / "sample"
RATES_TEXTS = DATA_DIR / "rates_policy_texts.csv"
HISTORICAL = DATA_DIR / "macro_historical_documents.csv"
AUDIT = DATA_DIR / "rates_policy_source_audit.json"

MOF_SEEDS = (
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201501/t20150128_2511170.htm", "关于2015年记账式附息（三期）国债发行工作有关事宜的通知", "2015-01-28 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201506/t20150624_2511307.htm", "中华人民共和国财政部公告2015年第42号", "2015-06-24 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201506/t20150626_2511310.htm", "中华人民共和国财政部公告2015年第43号", "2015-06-26 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201507/t20150731_2511335.htm", "关于2015年记账式附息（十八期）国债发行工作有关事宜的通知", "2015-07-31 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201508/t20150803_2511339.htm", "关于2015年第七期和第八期储蓄国债发行工作有关事宜的通知", "2015-07-30 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/yss/201603/t20160325_2510802.htm", "2015年和2016年中央财政国债余额情况表", "2016-03-25 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201612/t20161227_2511920.htm", "关于公布2017年国债发行计划的通知", "2016-12-27 09:00:00"),
    ("https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/201706/t20170613_2621544.htm", "2017年财政部将在境外发行人民币国债和美元主权债券", "2017-06-13 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201712/t20171208_2770145.htm", "中华人民共和国财政部公告2017年第163号", "2017-12-08 09:00:00"),
    ("https://www.mof.gov.cn/gp/xxgkml/gks/201712/t20171213_2776284.htm", "2017年记账式附息（二十三期）国债第二次续发行通知", "2017-12-06 09:00:00"),
)


def fetch(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 AlphaLensResearch/2.0"})
    with urlopen(request, timeout=45) as response:
        raw = response.read()
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup(("script", "style", "noscript")):
        node.decompose()
    text = re.sub(r"\s+", "", soup.get_text("。", strip=True))
    return raw, text[:18000]


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    existing = _read(RATES_TEXTS)
    allowed_mof_urls = {row[0] for row in MOF_SEEDS}
    by_url = {
        row["source_url"]: row for row in existing
        if row.get("source_url") and (row.get("source_name") != "财政部" or row["source_url"] in allowed_mof_urls)
    }
    historical = [
        row for row in _read(HISTORICAL)
        if row.get("source_name") == "国家统计局" and row.get("url", "").startswith("https://www.stats.gov.cn/")
    ]
    added: list[dict[str, str]] = []
    for row in historical:
        if row["url"] in by_url:
            continue
        content = row.get("content", "").strip()
        if len(content) < 120:
            continue
        published = row["publish_time"]
        if len(published) == 10:
            published += " 23:59:59"
        normalized = {
            "doc_id": row["doc_id"], "publish_time": published, "title": row["title"],
            "content": content, "source_name": "国家统计局", "source_url": row["url"],
            # The historical corpus predates raw-response retention.  Hash the
            # persisted verified text and state that distinction in the audit.
            "source_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        validate_text_row(normalized)
        by_url[row["url"]] = normalized
        added.append(normalized)
    targets: list[tuple[str, str, str, str, str]] = []
    for index, (url, title, publish_time) in enumerate(MOF_SEEDS, 1):
        doc_id = f"MOF-BOND-SEED-{index:02d}"
        targets.append((url, doc_id, title, publish_time, "财政部"))
        if url in by_url:
            # Older runs used a shorter seed list. Canonicalize identifiers and
            # metadata so the expanded list cannot create duplicate doc_ids.
            by_url[url].update({
                "doc_id": doc_id, "title": title, "publish_time": publish_time,
                "source_name": "财政部",
            })

    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, target[0]): target for target in targets if target[0] not in by_url}
        for future in as_completed(futures):
            url, doc_id, title, publish_time, source_name = futures[future]
            try:
                raw, fetched_text = future.result()
            except Exception as exc:
                failures.append({"url": url, "error": str(exc)[:300]})
                continue
            if len(fetched_text) < 120:
                failures.append({"url": url, "error": "正文过短"})
                continue
            if not publish_time:
                match = re.search(r"(20\d{2})[^0-9]?年[^0-9]?(\d{1,2})[^0-9]?月[^0-9]?(\d{1,2})", fetched_text[:1000])
                publish_time = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d} 09:00:00" if match else "2020-01-01 09:00:00"
            row = {
                "doc_id": doc_id, "publish_time": publish_time, "title": title or fetched_text[:80],
                "content": fetched_text, "source_name": source_name, "source_url": url,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            }
            try:
                validate_text_row(row)
            except (ValueError, TypeError) as exc:
                failures.append({"url": url, "error": str(exc)[:300]})
                continue
            by_url[url] = row
            added.append(row)

    rows = sorted(by_url.values(), key=lambda row: (row["publish_time"], row["doc_id"]))
    with RATES_TEXTS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEXT_FIELDS, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    previous = json.loads(AUDIT.read_text(encoding="utf-8")) if AUDIT.exists() else {}
    for legacy_field in ("nbs_historical_documents_added", "mof_documents_added"):
        previous.pop(legacy_field, None)
    previous.update({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "documents": len(rows), "sources": sorted({row["source_name"] for row in rows}),
        "source_counts": dict(sorted(Counter(row["source_name"] for row in rows).items())),
        "nbs_historical_documents": sum(row["source_name"] == "国家统计局" for row in rows),
        "mof_government_bond_announcements": sum(row["source_name"] == "财政部" for row in rows),
        "nbs_historical_documents_added_this_run": sum(row["source_name"] == "国家统计局" for row in added),
        "mof_documents_added_this_run": sum(row["source_name"] == "财政部" for row in added),
        "augmentation_failures": failures,
        "augmentation_method": "NBS复用已核验历史语料并记录持久化正文SHA-256；MOF重新抓取官方响应并记录SHA-256；按URL去重",
        "optional_source_warning": "财政部清单为可审计官方URL种子；抓取失败会记录且不会用标题或摘要替代正文。",
    })
    AUDIT.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": len(rows), "added": len(added), "failures": len(failures), "sources": previous["sources"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
