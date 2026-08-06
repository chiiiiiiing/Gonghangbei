"""对采集清单做只读预演：预测实体链接、事件、覆盖缺口与重复项。

在把清单交给 `导入真实文本.py --apply` 之前，先在本工具里跑一遍预测，
把「盲采」变成「定向采」——确认每篇文本真的能链接到池内股票并产生事件，
避免采集工时浪费在无事件文本上。

- 复用 src/pipeline/link_entities.link_documents / extract_events_rule_based.build_events
  的同一套规则，但只读：不写 data/sample/ 下任何文件。
- 复用 src/ingestion.text_import.validate_manifest / merge_documents 的校验与合并口径。
- 预演报告写入 data/external/文本导入暂存/预演报告.json（git 忽略区），stdout 输出摘要。

用法：
    .venv/bin/python 预演导入.py 采集清单.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from src.ingestion.common import sha256
from src.ingestion.text_import import merge_documents, validate_manifest
from src.pipeline.extract_events_rule_based import build_events
from src.pipeline.link_entities import link_documents


ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "data" / "sample"
STAGING_DIR = ROOT / "data" / "external" / "文本导入暂存"
DISCOVERY_END = "2025-12-31"
TARGET_PER_TYPE = 25
SOURCE_TYPES = ["policy", "announcement", "news", "ir_qa"]
EVENT_TYPES = [
    "policy_support",
    "regulatory_penalty",
    "inquiry_letter_pressure",
    "earnings_quality_anomaly",
    "product_price_increase",
    "supply_chain_disruption",
    "capacity_expansion",
    "investor_question_pressure",
    "attention_spread",
]
SOURCE_LABELS = {"policy": "政策", "announcement": "公告", "news": "新闻", "ir_qa": "互动问答"}
SPLIT_LABELS = {"discovery": "Discovery(2024—2025)", "oos": "OOS(2026H1)"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def split_for(publish_time: str) -> str:
    return "discovery" if publish_time <= DISCOVERY_END else "oos"


def coverage_counts(documents: list[dict[str, str]]) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {"discovery": Counter(), "oos": Counter()}
    for row in documents:
        counts[split_for(row["publish_time"])][row["source_type"]] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="AlphaLens 采集清单只读预演")
    parser.add_argument("manifest", type=Path, help="锁定字段格式的采集清单 CSV")
    args = parser.parse_args()

    manifest = args.manifest.resolve()
    try:
        rows, errors = validate_manifest(manifest)
    except ValueError as exc:
        print(f"清单字段结构不合法：{exc}", file=sys.stderr)
        return 1
    if errors:
        print("# 采集清单校验失败（不会进入预演）")
        for error in errors:
            print(f"- {error}")
        return 1

    existing = read_csv(SAMPLE_DIR / "raw_documents.csv")
    live_ids = {row["doc_id"] for row in existing}
    live_urls = {row["url"] for row in existing}
    incoming_ids = [row["doc_id"] for row in rows]
    incoming_urls = [row["url"] for row in rows]

    duplicates = {
        "doc_id_in_manifest_repeated": sorted(
            {doc_id for doc_id in incoming_ids if incoming_ids.count(doc_id) > 1}
        ),
        "url_in_manifest_repeated": sorted(
            {url for url in incoming_urls if incoming_urls.count(url) > 1}
        ),
        "doc_id_already_in_live": sorted({doc_id for doc_id in incoming_ids if doc_id in live_ids}),
        "url_already_in_live": sorted({url for url in incoming_urls if url in live_urls}),
    }

    try:
        merged = merge_documents(existing, rows)
    except ValueError as exc:
        print(f"# 预演中止：{exc}", file=sys.stderr)
        return 1

    links = link_documents(merged)
    links_by_doc: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in links:
        links_by_doc[link["doc_id"]].append(link)
    docs_map = {row["doc_id"]: row for row in merged}
    events = build_events(docs_map, links_by_doc)
    events_by_doc: dict[str, list[str]] = defaultdict(list)
    for event in events:
        events_by_doc[event["doc_id"]].append(event["event_type"])
    live_events = [event for event in events if event["doc_id"] in live_ids]

    current_counts = coverage_counts(existing)
    projected_counts = coverage_counts(merged)
    live_event_types = event_type_counter(live_events)
    projected_event_types = event_type_counter(events)

    per_doc: list[dict[str, str]] = []
    no_link: list[str] = []
    no_event: list[str] = []
    for row in rows:
        doc_id = row["doc_id"]
        linked = sorted({link["stock_code"] for link in links_by_doc.get(doc_id, [])})
        event_types = sorted(set(events_by_doc.get(doc_id, [])))
        split = split_for(row["publish_time"])
        gap_was_open = current_counts[split][row["source_type"]] < TARGET_PER_TYPE
        per_doc.append(
            {
                "doc_id": doc_id,
                "source_type": row["source_type"],
                "split": split,
                "publish_time": row["publish_time"],
                "linked_stock_count": len(linked),
                "linked_stocks": ",".join(linked[:8]),
                "predicted_event_types": "|".join(event_types) or "(无事件)",
                "gap_filled": "是" if gap_was_open else "否(该格已达标)",
            }
        )
        if not linked:
            no_link.append(doc_id)
        if not event_types:
            no_event.append(doc_id)

    gaps_closed: list[str] = []
    gaps_remaining: list[str] = []
    for split in ("discovery", "oos"):
        for source_type in SOURCE_TYPES:
            before = current_counts[split][source_type]
            after = projected_counts[split][source_type]
            key = f"{split}/{source_type}"
            if before < TARGET_PER_TYPE <= after:
                gaps_closed.append(f"{key} {before}→{after}")
            elif after < TARGET_PER_TYPE:
                gaps_remaining.append(f"{key} {after}/{TARGET_PER_TYPE}")

    zero_before = sorted(t for t in EVENT_TYPES if live_event_types[t] == 0)
    zero_after = sorted(t for t in EVENT_TYPES if projected_event_types[t] == 0)

    report = {
        "manifest": str(manifest),
        "manifest_sha256": sha256(manifest),
        "row_count": len(rows),
        "source_type_counts": dict(sorted(Counter(row["source_type"] for row in rows).items())),
        "duplicates": duplicates,
        "per_doc": per_doc,
        "summary": {
            "linked_docs": len(rows) - len(no_link),
            "no_link_docs": no_link,
            "docs_with_events": len(rows) - len(no_event),
            "no_event_docs": no_event,
            "projected_event_count": len(events),
            "projected_event_types": dict(sorted(projected_event_types.items())),
            "investor_question_pressure_count": projected_event_types["investor_question_pressure"],
            "coverage_gaps_closed": gaps_closed,
            "coverage_gaps_remaining": gaps_remaining,
            "zero_sample_event_types_before": zero_before,
            "zero_sample_event_types_after": zero_after,
        },
    }
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    (STAGING_DIR / "预演报告.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("# AlphaLens 采集清单预演")
    print(f"\n清单：{manifest}（{len(rows)} 篇）SHA-256 {report['manifest_sha256'][:12]}")
    print(f"来源分布：{report['source_type_counts']}\n")

    print("## 逐篇预测（链接 / 事件 / 缺口）")
    for item in per_doc:
        print(
            f"- {item['doc_id']} [{item['split']}/{SOURCE_LABELS[item['source_type']]}] "
            f"{item['publish_time']} 链接{item['linked_stock_count']}只 "
            f"事件[{item['predicted_event_types']}] 补缺:{item['gap_filled']}"
        )

    print("\n## 重复检查")
    for key in (
        "doc_id_in_manifest_repeated",
        "url_in_manifest_repeated",
        "doc_id_already_in_live",
        "url_already_in_live",
    ):
        items = duplicates[key]
        print(f"- {key}: {'无' if not items else ', '.join(items)}")

    print("\n## 覆盖缺口预测")
    print("- 本批可闭合：" + ("无" if not gaps_closed else "; ".join(gaps_closed)))
    print("- 仍待补：" + ("无" if not gaps_remaining else "; ".join(gaps_remaining)))
    print(f"- 事件类型零样本：前 {zero_before} → 后 {zero_after}")

    print("\n## 事件预测")
    print(
        f"- 预计产生 {len(events)} 条事件；其中互动问答压力事件 "
        f"{report['summary']['investor_question_pressure_count']} 条"
    )
    if no_link:
        print(f"- ⚠️ {len(no_link)} 篇未链接到池内股票（不会产生事件）：{', '.join(no_link[:10])}")
    if no_event:
        print(f"- ⚠️ {len(no_event)} 篇无预测事件：{', '.join(no_event[:10])}")

    print(f"\n预演报告已写入 {STAGING_DIR / '预演报告.json'}")
    return 0


def event_type_counter(events: list[dict[str, str]]) -> Counter[str]:
    return Counter(row["event_type"] for row in events)


if __name__ == "__main__":
    sys.exit(main())
