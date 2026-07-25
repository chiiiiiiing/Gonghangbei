"""Rule-based entity linker for AlphaLens B-side sample documents."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"


EXTRA_ALIASES = {
    "300750": ["宁德", "CATL", "动力电池龙头"],
    "002594": ["BYD", "比亚迪汽车"],
    "601012": ["隆基", "隆基股份"],
    "600438": ["通威"],
    "002129": ["TCL", "中环"],
    "002459": ["晶澳"],
    "688599": ["天合"],
    "300274": ["阳光", "Sungrow"],
    "300014": ["亿纬"],
    "002074": ["国轩"],
    "300207": ["欣旺达"],
    "300438": ["鹏辉"],
    "603659": ["璞泰来"],
    "300073": ["当升"],
    "002812": ["恩捷"],
    "300450": ["先导"],
    "002202": ["金风"],
    "601615": ["明阳"],
    "300772": ["运达"],
    "688349": ["三一"],
    "002080": ["中材"],
    "688063": ["派能"],
    "688390": ["固德威"],
    "300763": ["锦浪"],
    "605117": ["德业"],
    "002335": ["科华"],
    "601127": ["问界", "赛力斯汽车"],
    "000625": ["长安"],
    "601633": ["长城"],
    "600104": ["上汽"],
}


SECTOR_KEYWORDS = {
    "光伏": ["光伏", "太阳能组件", "硅片", "晶硅"],
    "锂电": ["动力电池", "锂离子电池", "锂电", "电池回收"],
    "风电": ["海上风电", "风电", "风机"],
    "储能": ["新型储能", "储能系统", "储能项目", "储能装机"],
    "整车": ["新能源汽车", "汽车以旧换新", "充换电", "车网互动"],
}


BROAD_THEME_MAPPINGS = [
    ("新能源汽车", ["整车", "锂电"], "新能源汽车产业链"),
    ("电动汽车", ["整车", "锂电", "储能"], "电动汽车与充换电链条"),
    ("充电设施", ["整车", "储能"], "充电设施服务能力"),
    ("车网互动", ["整车", "储能"], "车网互动"),
    ("消费品以旧换新", ["整车"], "消费品以旧换新中的汽车消费"),
    ("汽车标准化", ["整车"], "汽车标准化"),
    ("汽车产业", ["整车"], "汽车产业政策"),
    ("节能降碳", ["光伏", "风电", "储能"], "节能降碳行动"),
    ("非化石能源", ["光伏", "风电"], "非化石能源消费"),
    ("能源工作指导意见", ["光伏", "风电", "储能"], "能源工作指导意见"),
    ("新能源消纳", ["光伏", "风电", "储能"], "新能源消纳"),
    ("可再生能源", ["光伏", "风电"], "可再生能源"),
    ("绿证", ["光伏", "风电"], "绿色电力证书"),
    ("绿色电力", ["光伏", "风电"], "绿色电力"),
    ("新能源上网电价", ["光伏", "风电"], "新能源上网电价"),
    ("新型电力系统", ["光伏", "风电", "储能"], "新型电力系统"),
    ("新型能源体系", ["光伏", "风电", "储能"], "新型能源体系"),
    ("配电网", ["光伏", "储能"], "配电网高质量发展"),
    ("电力系统调节", ["储能", "光伏", "风电"], "电力系统调节能力"),
    ("调节能力", ["储能"], "电力系统调节能力"),
    ("全国电力工业统计", ["光伏", "风电"], "电力工业统计中的新能源装机"),
    ("全国电力统计数据", ["光伏", "风电"], "电力统计中的新能源装机"),
    ("并网运行情况", ["光伏", "风电"], "可再生能源并网运行"),
    ("绿色低碳", ["光伏", "锂电", "储能"], "绿色低碳先进技术"),
    ("制造业绿色化", ["光伏", "锂电", "储能"], "制造业绿色化"),
]


BROAD_LINKS_PER_SECTOR = 2


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = ["doc_id", "stock_code", "stock_name", "industry", "confidence", "evidence"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_alias_rows(stock_pool: list[dict[str, str]]) -> list[dict[str, str]]:
    alias_rows: list[dict[str, str]] = []
    for stock in stock_pool:
        aliases = [stock["stock_name"], *EXTRA_ALIASES.get(stock["stock_code"], [])]
        for alias in aliases:
            alias_rows.append(
                {
                    "stock_code": stock["stock_code"],
                    "stock_name": stock["stock_name"],
                    "industry": stock["industry_sector"],
                    "alias": alias,
                }
            )
    return sorted(alias_rows, key=lambda item: len(item["alias"]), reverse=True)


def confidence_for_match(title: str, content: str, stock_name: str, alias: str) -> float:
    if stock_name in title:
        return 0.98
    if alias in title:
        return 0.95
    if stock_name in content:
        return 0.91
    return 0.84


def link_entities() -> list[dict[str, object]]:
    stock_pool = read_csv(SAMPLE_DIR / "stock_pool.csv")
    documents = read_csv(SAMPLE_DIR / "raw_documents.csv")
    alias_rows = build_alias_rows(stock_pool)
    stocks_by_sector: dict[str, list[dict[str, str]]] = {}
    for stock in stock_pool:
        stocks_by_sector.setdefault(stock["industry_sector"], []).append(stock)
    for stocks in stocks_by_sector.values():
        stocks.sort(key=lambda item: float(item["market_cap"]), reverse=True)
    results: list[dict[str, object]] = []

    for doc in documents:
        title = doc["title"]
        content = doc["content"].split("项目关联：", 1)[0]
        text = f"{title}\n{content}"
        seen_codes: set[str] = set()
        for alias_row in alias_rows:
            alias = alias_row["alias"]
            code = alias_row["stock_code"]
            if alias not in text or code in seen_codes:
                continue
            seen_codes.add(code)
            location = "标题含" if alias in title else "正文提及"
            confidence = confidence_for_match(title, content, alias_row["stock_name"], alias)
            results.append(
                {
                    "doc_id": doc["doc_id"],
                    "stock_code": code,
                    "stock_name": alias_row["stock_name"],
                    "industry": alias_row["industry"],
                    "confidence": f"{confidence:.2f}",
                    "evidence": f'{location}"{alias}"',
                }
            )
        if seen_codes or doc["source_type"] not in {"policy", "news"}:
            continue
        for sector, keywords in SECTOR_KEYWORDS.items():
            matched_keyword = next((keyword for keyword in keywords if keyword in text), "")
            if not matched_keyword:
                continue
            confidence = 0.78 if doc["source_type"] == "policy" else 0.70
            for stock in stocks_by_sector.get(sector, []):
                results.append(
                    {
                        "doc_id": doc["doc_id"],
                        "stock_code": stock["stock_code"],
                        "stock_name": stock["stock_name"],
                        "industry": sector,
                        "confidence": f"{confidence:.2f}",
                        "evidence": f'产业主题映射"{matched_keyword}"→{sector}',
                    }
                )
                seen_codes.add(stock["stock_code"])
        if seen_codes or doc["source_type"] not in {"policy", "news"}:
            continue
        matched_theme = next((item for item in BROAD_THEME_MAPPINGS if item[0] in text), None)
        if not matched_theme:
            continue
        keyword, sectors, theme = matched_theme
        confidence = 0.64 if doc["source_type"] == "policy" else 0.58
        for sector in sectors:
            for stock in stocks_by_sector.get(sector, [])[:BROAD_LINKS_PER_SECTOR]:
                results.append(
                    {
                        "doc_id": doc["doc_id"],
                        "stock_code": stock["stock_code"],
                        "stock_name": stock["stock_name"],
                        "industry": sector,
                        "confidence": f"{confidence:.2f}",
                        "evidence": f'宽主题映射"{keyword}"→{theme}→{sector}代表股票',
                    }
                )
    return results


def main() -> None:
    write_csv(SAMPLE_DIR / "entity_links.csv", link_entities())


if __name__ == "__main__":
    main()
