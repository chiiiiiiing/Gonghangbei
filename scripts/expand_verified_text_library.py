"""Append verified AlphaLens source documents without changing CSV schemas.

The script uses the existing source-fetching utilities, filters out URLs that
are already present in raw_documents.csv, appends a balanced batch of new rows,
and writes a B-role curation report for manual follow-up.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fetch_verified_text_sources import (  # noqa: E402
    NEWS_DOMAINS,
    OFFICIAL_DOMAINS,
    SOURCE_FIELDS,
    announcement_document,
    announcement_score,
    build_search_document,
    clean_text,
    cninfo_org_map,
    deduplicate_sources,
    domain_allowed,
    existing_search_sources,
    extract_pdf_summary,
    fetch_ir_questions,
    is_search_detail_url,
    search_source,
    source_name_from_url,
)


SAMPLE_DIR = ROOT / "data" / "sample"
RAW_PATH = SAMPLE_DIR / "raw_documents.csv"
VIEW_DIR = ROOT / "查看材料"
REPORT_PATH = VIEW_DIR / "数据负责人本轮自动处理报告.md"
DEMO_CASE_PATH = VIEW_DIR / "新输入文本Demo测试案例.csv"
DEMO_CASE_MD_PATH = VIEW_DIR / "新输入文本Demo测试案例说明.md"
DISCLAIMER = "本报告仅供研究参考，不构成投资建议"
MVP_LOW = "2024-01-01"
MVP_HIGH = "2026-06-30"

EXTRA_POLICY_SEARCH_TERMS = [
    "2024 新能源汽车下乡 车企 充换电 政策 中国政府网",
    "2024 绿色低碳先进技术示范 新能源装备 国家发展改革委",
    "2024 配电网高质量发展行动实施方案 新能源消纳 中国政府网",
    "2024 新型储能 并网 调度运用 国家能源局",
    "2025 汽车以旧换新 工作通知 商务部 中国政府网",
    "2025 促进大功率充电设施科学规划建设 中国政府网",
    "2025 新能源上网电价市场化改革 国家发展改革委",
    "2025 车网互动规模化应用试点 国家发展改革委",
    "2025 促进可再生能源绿色电力证书市场高质量发展 国家发展改革委",
    "2025 新型储能规模化建设专项行动方案 国家能源局",
    "2026 减免车辆购置税 新能源汽车产品技术要求 工业和信息化部",
    "2026 新型能源体系建设 十五五 规划 国家发展改革委",
]

EXTRA_NEWS_SEARCH_TERMS = [
    "2024 全国电力工业统计数据 新能源 国家能源局",
    "2024 可再生能源并网运行情况 国家能源局",
    "2024 新能源汽车产销情况 中国汽车工业协会",
    "2024 锂离子电池行业运行情况 工业和信息化部",
    "2024 光伏制造行业运行情况 工业和信息化部",
    "2025 全国电力统计数据 新能源 国家能源局",
    "2025 新型储能发展情况 国家能源局 新闻发布会",
    "2025 新能源汽车出口 中国证券报",
    "2025 光伏行业自律 证券时报",
    "2025 动力电池装车量 财联社",
    "2026 光伏组件价格 证券日报",
    "2026 海上风电中标 证券日报",
    "2026 新能源汽车产销 中国汽车工业协会",
    "2026 储能招标 大储 证券时报",
]

DEMO_CASES = [
    {
        "case_id": "N001",
        "case_type": "policy_positive_multi_stock",
        "input_text": (
            "国家部门延续新能源汽车以旧换新政策，明确补贴申领、车型目录和充换电服务保障，"
            "整车销售与动力电池配套需求受到市场关注。"
        ),
        "expected_related_stocks": "002594|000625|601633|600104|300750",
        "expected_signal": "政策需求侧正向研究信号；每只相关股票分别生成股票-事件和因子值",
        "manual_check_focus": "政策来源、发布日期、是否明确指向汽车消费和动力电池链条",
    },
    {
        "case_id": "N002",
        "case_type": "announcement_risk_single_stock",
        "input_text": (
            "某动力电池材料公司公告称计提资产减值准备，并提示下游需求、产品价格和客户验收节奏存在不确定性。"
        ),
        "expected_related_stocks": "300073|002812|603659",
        "expected_signal": "风险或不确定性研究信号；应触发风险披露类谓词，因子方向需要单独建模",
        "manual_check_focus": "是否真实公告、是否公告主体明确、是否只关联公告主体而非泛化到全行业",
    },
    {
        "case_id": "N003",
        "case_type": "industry_attention_multi_stock",
        "input_text": (
            "多地海上风电项目核准和招标节奏改善，风机整机、叶片和运维服务订单受到主流财经媒体关注。"
        ),
        "expected_related_stocks": "002202|601615|300772|688349|002080",
        "expected_signal": "行业关注扩散研究信号；适合验证媒体来源和核心产品谓词",
        "manual_check_focus": "新闻来源是否权威、是否有招标或核准事实、是否覆盖风电链条",
    },
    {
        "case_id": "N004",
        "case_type": "neutral_ir_question",
        "input_text": (
            "投资者询问公司股东人数和股东户数变化，公司回复请以定期报告披露信息为准。"
        ),
        "expected_related_stocks": "002129|002202",
        "expected_signal": "中性或弱信号；不应仅凭单条问答生成 investor_question_pressure 合格事件",
        "manual_check_focus": "单条问答不能代表提问压力增加，适合做负样本",
    },
    {
        "case_id": "N005",
        "case_type": "mixed_policy_uncertainty",
        "input_text": (
            "新型储能并网政策继续推进独立储能参与调峰调频，同时文件要求强化安全管理和项目运行风险监测。"
        ),
        "expected_related_stocks": "688063|688390|300763|605117|002335",
        "expected_signal": "政策支持叠加风险约束的复合研究信号；应同时检查政策和风险谓词",
        "manual_check_focus": "不能只保留利好表述，要保留安全和运行风险约束",
    },
]

LOW_RELEVANCE_APPEND_ANNOUNCEMENT_KEYWORDS = [
    "年度报告",
    "半年度报告",
    "季度报告",
    "可持续发展报告",
    "社会责任报告",
    "网上说明会",
    "募集说明书",
    "公司债券",
    "跟踪评级",
    "法律意见书",
    "股东大会",
    "权益分派",
    "董事会决议",
    "监事会决议",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def next_doc_id(existing_docs: list[dict[str, str]]) -> int:
    max_id = 0
    for row in existing_docs:
        doc_id = row.get("doc_id", "")
        if len(doc_id) == 4 and doc_id.startswith("S") and doc_id[1:].isdigit():
            max_id = max(max_id, int(doc_id[1:]))
    return max_id + 1


def source_is_appendable(source: dict[str, str], existing_urls: set[str]) -> bool:
    url = source.get("url", "")
    publish_time = source.get("publish_time", "")
    summary = source.get("summary", "")
    return (
        bool(url)
        and url not in existing_urls
        and MVP_LOW <= publish_time <= MVP_HIGH
        and bool(source.get("title"))
        and bool(summary)
    )


def is_low_relevance_append_announcement(row: dict[str, str]) -> bool:
    if row.get("source_type") != "announcement":
        return False
    title = row.get("title", "")
    return any(keyword in title for keyword in LOW_RELEVANCE_APPEND_ANNOUNCEMENT_KEYWORDS)


def row_is_generated_append(row: dict[str, str]) -> bool:
    doc_id = row.get("doc_id", "")
    return doc_id.startswith("S") and doc_id[1:].isdigit() and int(doc_id[1:]) >= 121


def clean_generated_low_relevance_announcements() -> list[dict[str, str]]:
    docs = read_csv(RAW_PATH)
    removed = [
        row
        for row in docs
        if row_is_generated_append(row) and is_low_relevance_append_announcement(row)
    ]
    if removed:
        kept = [
            row
            for row in docs
            if not (row_is_generated_append(row) and is_low_relevance_append_announcement(row))
        ]
        write_csv(RAW_PATH, SOURCE_FIELDS, kept)
    return removed


def collect_search_sources(
    docs: list[dict[str, str]],
    source_type: str,
    domains: tuple[str, ...],
    terms: list[str],
    existing_urls: set[str],
) -> list[dict[str, str]]:
    source_pool = existing_search_sources(docs, source_type, domains)
    fallback_name = "政府/部委网站" if source_type == "policy" else "财经媒体/行业网站"
    for index, term in enumerate(terms, start=1):
        print(f"[AlphaLens] Search {source_type} append source {index}/{len(terms)} ...", flush=True)
        source_pool.append(search_source(term, domains, fallback_date="", fallback_name=fallback_name))
        time.sleep(0.15)
    source_pool = deduplicate_sources(source_pool)
    return [source for source in source_pool if source_is_appendable(source, existing_urls)]


def cninfo_announcement_candidates(
    stock_codes: list[str],
    stock_names: dict[str, str],
    existing_urls: set[str],
    *,
    per_stock: int,
) -> list[dict[str, str]]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    }
    org_map = cninfo_org_map()
    rows: list[dict[str, str]] = []
    seen_urls = set(existing_urls)
    for stock_code in stock_codes:
        print(f"[AlphaLens] Search announcement append source {stock_code} ...", flush=True)
        params = {
            "pageNum": 1,
            "pageSize": 30,
            "column": "sse" if stock_code.startswith("6") else "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{stock_code},{org_map[stock_code]}" if stock_code in org_map else "",
            "searchkey": stock_code if stock_code.startswith("6") else "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": "2024-01-01~2026-06-30",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        candidates: list[tuple[int, dict[str, object], str]] = []
        for page_num in range(1, 5):
            params["pageNum"] = page_num
            response = requests.post(
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                headers=headers,
                data=params,
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("announcements") or []
            for item in items:
                title = clean_text(str(item.get("announcementTitle", "")))
                if not title or "取消" in title:
                    continue
                if any(keyword in title for keyword in LOW_RELEVANCE_APPEND_ANNOUNCEMENT_KEYWORDS):
                    continue
                score = announcement_score(title)
                if score <= 0:
                    continue
                timestamp = int(item.get("announcementTime", 0)) / 1000
                publish_time = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                url = "http://static.cninfo.com.cn/" + str(item.get("adjunctUrl", ""))
                if url in seen_urls or not (MVP_LOW <= publish_time <= MVP_HIGH):
                    continue
                candidates.append((score, item, url))
            if len(candidates) >= per_stock:
                break
            time.sleep(0.2)
        for score, item, url in sorted(
            candidates,
            key=lambda pair: (pair[0], int(pair[1].get("announcementTime", 0))),
            reverse=True,
        )[:per_stock]:
            title = clean_text(str(item["announcementTitle"]))
            publish_time = datetime.fromtimestamp(int(item["announcementTime"]) / 1000).strftime("%Y-%m-%d")
            rows.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_names.get(stock_code, str(item.get("secName", ""))),
                    "title": title,
                    "publish_time": publish_time,
                    "source_name": "巨潮资讯网",
                    "url": url,
                    "summary": extract_pdf_summary(url),
                    "score": str(score),
                }
            )
            seen_urls.add(url)
        time.sleep(0.25)
    return [row for row in rows if source_is_appendable(row, existing_urls)]


def diversify_by_sector(
    rows: list[dict[str, str]],
    stock_sector: dict[str, str],
    limit: int,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    by_sector: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_sector[stock_sector.get(row.get("stock_code", ""), "")].append(row)
    while len(selected) < limit and any(by_sector.values()):
        for sector in sorted(by_sector):
            if by_sector[sector] and len(selected) < limit:
                selected.append(by_sector[sector].pop(0))
    return selected


def append_documents(per_type: int) -> tuple[list[dict[str, str]], Counter[str], list[str]]:
    docs = read_csv(RAW_PATH)
    stock_rows = read_csv(SAMPLE_DIR / "stock_pool.csv")
    stock_codes = [row["stock_code"] for row in stock_rows]
    stock_names = {row["stock_code"]: row["stock_name"] for row in stock_rows}
    stock_sector = {row["stock_code"]: row["industry_sector"] for row in stock_rows}
    existing_urls = {row["url"] for row in docs}
    next_id = next_doc_id(docs)
    appended: list[dict[str, str]] = []
    shortages: list[str] = []

    policy_sources = collect_search_sources(
        docs,
        "policy",
        OFFICIAL_DOMAINS,
        EXTRA_POLICY_SEARCH_TERMS,
        existing_urls,
    )[:per_type]
    news_sources = collect_search_sources(
        docs,
        "news",
        NEWS_DOMAINS,
        EXTRA_NEWS_SEARCH_TERMS,
        existing_urls | {source["url"] for source in policy_sources},
    )[:per_type]
    announcement_sources = diversify_by_sector(
        cninfo_announcement_candidates(stock_codes, stock_names, existing_urls, per_stock=3),
        stock_sector,
        per_type,
    )
    ir_sources = [
        row
        for row in fetch_ir_questions([code for code in stock_codes if not code.startswith("6")], stock_names, target_count=90)
        if source_is_appendable(row, existing_urls)
    ][:per_type]

    source_batches = {
        "policy": policy_sources,
        "news": news_sources,
        "announcement": announcement_sources,
        "ir_qa": ir_sources,
    }

    for source_type, sources in source_batches.items():
        if len(sources) < per_type:
            shortages.append(f"{source_type}: {len(sources)}/{per_type}")
        for source in sources:
            row_stub = {
                "doc_id": f"S{next_id:03d}",
                "source_type": source_type,
                "title": source.get("title", ""),
                "content": "",
                "publish_time": source.get("publish_time", ""),
                "source_name": source.get("source_name", ""),
                "url": source.get("url", ""),
            }
            if source_type == "policy":
                doc = build_search_document(row_stub, source, kind="policy")
            elif source_type == "news":
                doc = build_search_document(row_stub, source, kind="news")
            elif source_type == "announcement":
                doc = announcement_document(row_stub, source, stock_sector.get(source.get("stock_code", ""), ""))
            else:
                stock_code = source.get("stock_code", "")
                doc = {
                    "doc_id": row_stub["doc_id"],
                    "source_type": "ir_qa",
                    "title": f"投资者问答：{source['stock_name']}回应“{source['question'][:36]}”",
                    "content": (
                        f"原文摘要：投资者在深交所互动易向{source['stock_name']}提问，"
                        f"问题摘要为“{source['question'][:80]}”。公司回复摘要为“{source['reply'][:100]}”。"
                        f"项目关联：{source['stock_name']}属于新能源股票池的{stock_sector.get(stock_code, '')}板块。"
                        "单条问答只作为证据文本，不直接代表提问压力增加；不包含收益判断或股价方向判断。"
                    ),
                    "publish_time": source["publish_time"],
                    "source_name": source["source_name"],
                    "url": source["url"],
                }
            appended.append(doc)
            existing_urls.add(doc["url"])
            next_id += 1

    if appended:
        write_csv(RAW_PATH, SOURCE_FIELDS, [*docs, *appended])
    return appended, Counter(row["source_type"] for row in appended), shortages


def write_demo_cases() -> None:
    write_csv(
        DEMO_CASE_PATH,
        [
            "case_id",
            "case_type",
            "input_text",
            "expected_related_stocks",
            "expected_signal",
            "manual_check_focus",
        ],
        DEMO_CASES,
    )
    lines = [
        "# AlphaLens 新输入文本 Demo 测试案例",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        DISCLAIMER,
        "",
        "## 使用方法",
        "",
        "这些案例不写入历史训练库，用于演示“新输入文本 → 股票关联 → 事件 → 谓词 → 触发规则 → 每只相关股票因子值”的在线推理路径。",
        "新文本当下只能生成因子值，不能马上验证未来收益；5 个交易日后才可以把真实收益补入样本外验证。",
        "",
        "## 案例清单",
        "",
        "| case_id | 类型 | 预期关联股票 | 预期信号 | 人工检查重点 |",
        "|---------|------|--------------|----------|--------------|",
    ]
    for row in DEMO_CASES:
        lines.append(
            f"| {row['case_id']} | {row['case_type']} | {row['expected_related_stocks']} | "
            f"{row['expected_signal']} | {row['manual_check_focus']} |"
        )
    DEMO_CASE_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(
    appended: list[dict[str, str]],
    counts: Counter[str],
    shortages: list[str],
    removed: list[dict[str, str]] | None = None,
    *,
    demo_cases_only: bool = False,
) -> None:
    removed = removed or []
    after_total = len(read_csv(RAW_PATH))
    before_total = max(0, after_total - len(appended) + len(removed))
    valid_generated_count = sum(1 for row in read_csv(RAW_PATH) if row_is_generated_append(row))
    lines = [
        "# AlphaLens 数据负责人本轮自动处理报告",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        DISCLAIMER,
        "",
        "## 自动处理结论",
        "",
        f"- 历史文本库本次变化：{before_total} → {after_total} 条，新增 {len(appended)} 条",
        f"- 当前有效自动增量文本：{valid_generated_count} 条",
        f"- 新增来源分布：policy={counts['policy']}，announcement={counts['announcement']}，news={counts['news']}，ir_qa={counts['ir_qa']}",
        f"- 自动清理低相关新增公告：{len(removed)} 条",
        f"- 新输入 Demo 测试案例：{len(DEMO_CASES)} 条，已写入 `查看材料/新输入文本Demo测试案例.csv`",
        "- CSV 字段名保持不变；没有写入 data/raw、data/processed 或 data/external。",
        "",
        "## 新增文本明细",
        "",
        "| doc_id | source_type | publish_time | source_name | title |",
        "|--------|-------------|--------------|-------------|-------|",
    ]
    for row in appended:
        title = row["title"].replace("|", "\\|")[:80]
        lines.append(
            f"| {row['doc_id']} | {row['source_type']} | {row['publish_time']} | {row['source_name']} | {title} |"
        )
    lines.extend(["", "## 已清理的低相关新增公告", ""])
    if removed:
        lines.extend(
            [
                "| doc_id | title | 清理原因 |",
                "|--------|-------|----------|",
            ]
        )
        for row in removed:
            title = row["title"].replace("|", "\\|")[:80]
            lines.append(f"| {row['doc_id']} | {title} | 例行披露或证据库文本，不适合进入事件规则发现主样本 |")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 自动清洗规则",
            "",
            "- 只追加 URL 不重复、日期位于 2024-01-01 至 2026-06-30、正文摘要不为空的文本。",
            "- 政策和新闻只接受详情页 URL，并保留来源名称、首次公开日期和简短事实摘要。",
            "- 公告只接受巨潮资讯网 PDF，并优先保留项目、订单、处罚、问询、减值、停复产等事件相关披露。",
            "- 互动问答只作为证据文本；单条问答不直接生成 `investor_question_pressure`。",
            "",
            "## 待人工处理",
            "",
        ]
    )
    if demo_cases_only:
        lines.append("- 本次仅刷新 Demo 案例/清理低相关新增公告，没有执行新增来源抓取。")
    elif shortages:
        lines.append("- 自动来源池存在缺口：" + "；".join(shortages))
    else:
        lines.append("- 本轮各来源类型均达到自动追加目标。")
    lines.extend(
        [
            "- 你需要抽查新增文本的标题、日期和摘要是否与原网页/PDF一致。",
            "- 对 Demo 案例，需要确认预期关联股票是否过宽或漏掉关键链条。",
            "",
            "## 下一步建议",
            "",
            "1. 运行全量流水线，检查实体链接、事件、谓词、规则、因子和未来函数审计。",
            "2. 对新增文本中触发规则最多和完全不触发规则的样本各抽 5 条人工复核。",
            "3. 如果风险类规则仍偏少，继续补充公告问询、处罚、减值、停复产和需求下滑类样本。",
            "4. Flask 新输入文本 Demo 必须使用冻结规则库，不把新文本的未来收益写入在线因子值。",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append verified AlphaLens source documents.")
    parser.add_argument("--per-type", type=int, default=5, help="Target number of new documents per source type.")
    parser.add_argument("--demo-cases-only", action="store_true", help="Only write new-input demo cases.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or [])
    write_demo_cases()
    removed = clean_generated_low_relevance_announcements()
    if args.demo_cases_only:
        write_report([], Counter(), [], removed, demo_cases_only=True)
        print(f"Demo cases written to {DEMO_CASE_PATH}")
        return 0
    appended, counts, shortages = append_documents(args.per_type)
    write_report(appended, counts, shortages, removed)
    print(f"verified_documents_appended={len(appended)}")
    print(f"low_relevance_generated_announcements_removed={len(removed)}")
    print("append_counts=" + ",".join(f"{key}:{counts[key]}" for key in sorted(counts)))
    if shortages:
        print("append_shortages=" + "；".join(shortages))
    print(f"data_curation_report={REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
