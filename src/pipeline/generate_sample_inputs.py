"""Generate B-side sample inputs for local demo and schema integration."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "data" / "sample"
INPUT_FILES = {
    "stock_pool": SAMPLE_DIR / "stock_pool.csv",
    "raw_documents": SAMPLE_DIR / "raw_documents.csv",
    "market_data": SAMPLE_DIR / "market_data.csv",
}


STOCK_POOL = [
    ("601012", "隆基绿能", "光伏", 1450.0),
    ("600438", "通威股份", "光伏", 980.0),
    ("002129", "TCL中环", "光伏", 520.0),
    ("002459", "晶澳科技", "光伏", 610.0),
    ("688599", "天合光能", "光伏", 690.0),
    ("300274", "阳光电源", "光伏", 1260.0),
    ("300750", "宁德时代", "锂电", 8900.0),
    ("300014", "亿纬锂能", "锂电", 960.0),
    ("002074", "国轩高科", "锂电", 520.0),
    ("300207", "欣旺达", "锂电", 360.0),
    ("300438", "鹏辉能源", "锂电", 230.0),
    ("603659", "璞泰来", "锂电", 420.0),
    ("300073", "当升科技", "锂电", 260.0),
    ("002812", "恩捷股份", "锂电", 380.0),
    ("300450", "先导智能", "锂电", 390.0),
    ("002202", "金风科技", "风电", 410.0),
    ("601615", "明阳智能", "风电", 330.0),
    ("300772", "运达股份", "风电", 120.0),
    ("688349", "三一重能", "风电", 310.0),
    ("002080", "中材科技", "风电", 260.0),
    ("688063", "派能科技", "储能", 170.0),
    ("688390", "固德威", "储能", 210.0),
    ("300763", "锦浪科技", "储能", 240.0),
    ("605117", "德业股份", "储能", 360.0),
    ("002335", "科华数据", "储能", 210.0),
    ("002594", "比亚迪", "整车", 7800.0),
    ("601127", "赛力斯", "整车", 1450.0),
    ("000625", "长安汽车", "整车", 1320.0),
    ("601633", "长城汽车", "整车", 1550.0),
    ("600104", "上汽集团", "整车", 1650.0),
]


RAW_DOCUMENTS = [
    {
        "doc_id": "S001",
        "source_type": "policy",
        "title": "新能源汽车车辆购置税减免政策延续后的产业链影响",
        "content": (
            "财政部、税务总局、工信部延续和优化新能源汽车车辆购置税减免安排，政策继续围绕购车成本、车型目录和技术要求形成支持。"
            "样本文本用于 Demo 展示：比亚迪、长安汽车、上汽集团等整车企业可能受益于终端需求稳定，宁德时代、亿纬锂能等动力电池企业也与整车放量存在产业链关联。"
        ),
        "publish_time": "2024-01-02",
        "source_name": "财政部/税务总局/工信部",
        "url": "https://www.gov.cn/zhengce/",
    },
    {
        "doc_id": "S002",
        "source_type": "policy",
        "title": "2024 年新能源汽车下乡活动启动",
        "content": (
            "工信部等部门组织开展新能源汽车下乡活动，鼓励适销车型进入县乡市场，并完善充换电服务体系。"
            "活动强调车型供给、售后服务和基础设施协同，样本文本关联比亚迪、长安汽车、长城汽车、赛力斯等整车企业，以及宁德时代、国轩高科等动力电池供应链公司。"
        ),
        "publish_time": "2024-05-15",
        "source_name": "工业和信息化部",
        "url": "https://www.miit.gov.cn/",
    },
    {
        "doc_id": "S003",
        "source_type": "policy",
        "title": "节能降碳行动方案提出提升非化石能源消费比重",
        "content": (
            "节能降碳行动方案提出推进非化石能源消费、提升可再生能源消纳能力，并推动重点行业绿色低碳转型。"
            "该政策线索与光伏、风电、储能产业链直接相关，样本文本提及隆基绿能、通威股份、TCL中环、金风科技、明阳智能、固德威、锦浪科技等代表公司。"
        ),
        "publish_time": "2024-05-29",
        "source_name": "中国政府网",
        "url": "https://www.gov.cn/zhengce/",
    },
    {
        "doc_id": "S004",
        "source_type": "policy",
        "title": "新型储能应用场景扩围与电力系统调节能力建设",
        "content": (
            "国家层面持续推动新型储能参与电力系统调节，强调独立储能、源网荷储协同和新能源消纳。"
            "样本文本用于捕捉储能政策支持事件，关联派能科技、固德威、锦浪科技、德业股份、科华数据等储能系统、逆变器和数据中心电源企业。"
        ),
        "publish_time": "2025-02-17",
        "source_name": "国家发展改革委/国家能源局",
        "url": "https://www.ndrc.gov.cn/",
    },
    {
        "doc_id": "S005",
        "source_type": "policy",
        "title": "制造业绿色化发展意见强调新能源装备与循环利用",
        "content": (
            "制造业绿色化发展相关意见强调绿色低碳技术、先进制造和资源循环利用，鼓励新能源装备、动力电池回收和高效光伏产品应用。"
            "样本文本关联晶澳科技、天合光能、阳光电源、当升科技、恩捷股份、先导智能等光伏和锂电材料设备公司。"
        ),
        "publish_time": "2024-02-29",
        "source_name": "工业和信息化部等部门",
        "url": "https://www.miit.gov.cn/",
    },
    {
        "doc_id": "S006",
        "source_type": "announcement",
        "title": "宁德时代披露动力电池与储能业务产能规划进展",
        "content": (
            "公司公告摘要显示，宁德时代继续围绕动力电池、储能电池和新技术产品推进产能与客户交付能力建设。"
            "公告同时提示项目建设、客户需求和原材料价格存在不确定性。该样本文本用于演示产能扩张事件，不代表对公司未来业绩或股价的判断。"
        ),
        "publish_time": "2025-04-15",
        "source_name": "巨潮资讯网",
        "url": "http://www.cninfo.com.cn/",
    },
    {
        "doc_id": "S007",
        "source_type": "announcement",
        "title": "比亚迪发布新能源汽车销量与车型结构更新",
        "content": (
            "比亚迪公告摘要显示，公司新能源汽车销量、车型结构和出口业务继续受到市场关注。"
            "样本文本重点记录整车销量、动力电池自供和海外市场线索，用于演示 attention_spread 与主营业务直接相关的事件抽取。"
        ),
        "publish_time": "2025-01-02",
        "source_name": "巨潮资讯网",
        "url": "http://www.cninfo.com.cn/",
    },
    {
        "doc_id": "S008",
        "source_type": "announcement",
        "title": "隆基绿能披露高效电池组件技术改造项目进展",
        "content": (
            "隆基绿能公告摘要显示，公司围绕高效电池、组件和产线技术改造推进项目建设，目标是提升产品转换效率和交付能力。"
            "公告提示项目投产节奏、市场价格和海外需求仍有不确定性。样本文本用于演示光伏产能扩张事件。"
        ),
        "publish_time": "2024-10-30",
        "source_name": "巨潮资讯网",
        "url": "http://www.cninfo.com.cn/",
    },
    {
        "doc_id": "S009",
        "source_type": "announcement",
        "title": "阳光电源储能系统海外订单与交付能力受到关注",
        "content": (
            "阳光电源公告摘要显示，公司储能系统、逆变器和电站业务持续推进海外认证、订单交付和服务网络建设。"
            "该样本文本关注储能系统订单与交付能力，适合演示 capacity_expansion 与 evidence_from_authoritative_source 的联动。"
        ),
        "publish_time": "2025-08-20",
        "source_name": "巨潮资讯网",
        "url": "http://www.cninfo.com.cn/",
    },
    {
        "doc_id": "S010",
        "source_type": "announcement",
        "title": "金风科技披露风电机组订单和交付安排",
        "content": (
            "金风科技公告摘要显示，公司围绕陆上和海上风电机组订单、交付排产和运维服务持续推进。"
            "公告同时提醒招标节奏、原材料成本和项目并网进度可能影响交付。该样本文本用于演示风电订单事件。"
        ),
        "publish_time": "2024-09-12",
        "source_name": "巨潮资讯网",
        "url": "http://www.cninfo.com.cn/",
    },
    {
        "doc_id": "S011",
        "source_type": "news",
        "title": "光伏产业链价格出现阶段性企稳迹象",
        "content": (
            "财经新闻摘要称，硅料、硅片和组件价格经历调整后出现阶段性企稳迹象，市场关注产能出清和需求修复节奏。"
            "样本文本提及隆基绿能、通威股份、TCL中环、晶澳科技、天合光能、阳光电源，用于演示行业价格事件和关注度扩散。"
        ),
        "publish_time": "2025-03-10",
        "source_name": "证券时报",
        "url": "https://www.stcn.com/",
    },
    {
        "doc_id": "S012",
        "source_type": "news",
        "title": "动力电池装车量增长带动产业链关注",
        "content": (
            "财经新闻摘要称，新能源汽车产销和动力电池装车量保持增长，市场对电池企业产能利用、材料价格和客户结构的讨论升温。"
            "样本文本关联宁德时代、亿纬锂能、国轩高科、欣旺达、鹏辉能源、当升科技和恩捷股份。"
        ),
        "publish_time": "2025-07-15",
        "source_name": "财联社",
        "url": "https://www.cls.cn/",
    },
    {
        "doc_id": "S013",
        "source_type": "news",
        "title": "新型储能招标规模提升引发系统集成商关注",
        "content": (
            "财经新闻摘要称，多地新型储能项目招标节奏加快，大储、工商业储能和逆变器需求受到关注。"
            "样本文本提及派能科技、固德威、锦浪科技、德业股份、科华数据，用于演示储能主题 attention_spread。"
        ),
        "publish_time": "2026-03-20",
        "source_name": "21 世纪经济报道",
        "url": "https://www.21jingji.com/",
    },
    {
        "doc_id": "S014",
        "source_type": "news",
        "title": "海上风电项目核准与交付节奏成为市场焦点",
        "content": (
            "财经新闻摘要称，沿海地区海上风电项目核准和招标节奏改善，风机整机、叶片和海缆配套企业受到关注。"
            "样本文本关联金风科技、明阳智能、运达股份、三一重能和中材科技，用于演示风电主题关注度扩散。"
        ),
        "publish_time": "2025-11-18",
        "source_name": "上海证券报",
        "url": "https://www.cnstock.com/",
    },
    {
        "doc_id": "S015",
        "source_type": "news",
        "title": "新能源汽车渗透率提升带来整车与电池链条讨论",
        "content": (
            "财经新闻摘要称，新能源车渗透率继续提升，市场关注整车价格带、出口结构和动力电池配套关系。"
            "样本文本提及比亚迪、赛力斯、长安汽车、长城汽车、上汽集团和宁德时代，用于演示跨整车和锂电链条的主题扩散。"
        ),
        "publish_time": "2026-04-12",
        "source_name": "中国证券报",
        "url": "https://www.cs.com.cn/",
    },
    {
        "doc_id": "S016",
        "source_type": "ir_qa",
        "title": "投资者追问宁德时代钠离子电池和储能进展",
        "content": (
            "互动问答摘要显示，投资者关注宁德时代钠离子电池、储能产品和客户验证进展。"
            "公司回复强调相关技术和产品按照规划推进，具体商业化节奏需结合客户需求、认证和项目落地情况。该样本文本用于演示 investor_question_pressure。"
        ),
        "publish_time": "2025-06-20",
        "source_name": "深交所互动易",
        "url": "https://irm.cninfo.com.cn/",
    },
    {
        "doc_id": "S017",
        "source_type": "ir_qa",
        "title": "投资者关注比亚迪出口和新车型交付",
        "content": (
            "互动问答摘要显示，投资者关注比亚迪海外出口、新车型交付和插混车型竞争力。"
            "公司回复表示会持续推进产品矩阵和海外渠道建设，具体销量和订单情况以公司公告和定期报告为准。该样本文本用于演示投资者追问压力。"
        ),
        "publish_time": "2025-08-18",
        "source_name": "深交所互动易",
        "url": "https://irm.cninfo.com.cn/",
    },
    {
        "doc_id": "S018",
        "source_type": "ir_qa",
        "title": "投资者追问派能科技储能订单和海外认证",
        "content": (
            "互动问答摘要显示，投资者关注派能科技户储和大储订单、海外认证以及渠道恢复节奏。"
            "公司回复称会根据客户需求和市场情况推进产品交付，并提示不同区域政策和需求变化可能影响订单节奏。"
        ),
        "publish_time": "2026-05-14",
        "source_name": "上证 e 互动",
        "url": "https://sns.sseinfo.com/",
    },
    {
        "doc_id": "S019",
        "source_type": "ir_qa",
        "title": "投资者询问晶澳科技 N 型组件产能利用率",
        "content": (
            "互动问答摘要显示，投资者询问晶澳科技 N 型电池组件产能、海外订单和价格压力。"
            "公司回复称将根据市场需求推进排产和交付，并继续提升高效产品占比。该样本文本用于演示光伏核心产品谓词。"
        ),
        "publish_time": "2025-03-06",
        "source_name": "深交所互动易",
        "url": "https://irm.cninfo.com.cn/",
    },
    {
        "doc_id": "S020",
        "source_type": "ir_qa",
        "title": "投资者关注明阳智能海上风电项目交付",
        "content": (
            "互动问答摘要显示，投资者关注明阳智能海上风电项目交付、风机大型化和订单执行节奏。"
            "公司回复称项目交付受业主排期、海况和并网安排影响，将按合同和项目计划推进。该样本文本用于演示不确定性提示。"
        ),
        "publish_time": "2026-06-10",
        "source_name": "上证 e 互动",
        "url": "https://sns.sseinfo.com/",
    },
]


SECTOR_TOPICS = {
    "光伏": {
        "policy_object": "光伏装机、组件效率和可再生能源消纳",
        "core_product": "光伏组件、硅片、电池片和逆变器",
        "policy_keywords": "可再生能源、分布式光伏、绿电消纳、制造业绿色化",
        "announcement_topic": "高效组件产能、海外订单和技术改造",
        "news_topic": "产业链价格、装机需求和产能出清",
        "qa_topic": "N 型组件、海外出货、订单交付和价格压力",
    },
    "锂电": {
        "policy_object": "动力电池、储能电池和电池材料循环利用",
        "core_product": "动力电池、锂电材料、隔膜和电池设备",
        "policy_keywords": "新能源汽车、动力电池回收、储能电池、绿色制造",
        "announcement_topic": "动力电池产能、客户验证和材料供应",
        "news_topic": "装车量、材料价格和客户结构",
        "qa_topic": "新技术路线、客户订单、产能利用率和材料成本",
    },
    "风电": {
        "policy_object": "陆上风电、海上风电和可再生能源基地建设",
        "core_product": "风机整机、叶片、塔筒和运维服务",
        "policy_keywords": "海上风电、风电基地、设备更新、并网消纳",
        "announcement_topic": "风机订单、项目交付和运维服务",
        "news_topic": "海风项目核准、招标节奏和设备交付",
        "qa_topic": "海上风电订单、大兆瓦机型、业主排期和并网节奏",
    },
    "储能": {
        "policy_object": "新型储能、源网荷储协同和电力系统调节能力",
        "core_product": "储能系统、逆变器、电池簇和数据中心电源",
        "policy_keywords": "新型储能、独立储能、源网荷储、调峰调频",
        "announcement_topic": "储能订单、海外认证和系统交付",
        "news_topic": "储能招标、大储需求和工商业储能",
        "qa_topic": "户储订单、大储交付、海外认证和渠道恢复",
    },
    "整车": {
        "policy_object": "新能源汽车消费、车型供给和充换电服务",
        "core_product": "新能源汽车、插混车型、纯电车型和出口业务",
        "policy_keywords": "新能源汽车下乡、以旧换新、充换电、智能网联",
        "announcement_topic": "车型交付、销量结构和海外渠道",
        "news_topic": "新能源车渗透率、出口结构和价格带竞争",
        "qa_topic": "新车型交付、出口、补贴影响和订单节奏",
    },
}


SOURCE_INFO = {
    "policy": [
        ("财政部", "https://www.mof.gov.cn/"),
        ("工业和信息化部", "https://www.miit.gov.cn/"),
        ("国家发展改革委", "https://www.ndrc.gov.cn/"),
        ("国家能源局", "https://www.nea.gov.cn/"),
        ("中国政府网", "https://www.gov.cn/zhengce/"),
    ],
    "announcement": [("巨潮资讯网", "http://www.cninfo.com.cn/")],
    "news": [
        ("证券时报", "https://www.stcn.com/"),
        ("中国证券报", "https://www.cs.com.cn/"),
        ("上海证券报", "https://www.cnstock.com/"),
        ("21 世纪经济报道", "https://www.21jingji.com/"),
        ("财联社", "https://www.cls.cn/"),
    ],
    "ir_qa": [
        ("深交所互动易", "https://irm.cninfo.com.cn/"),
        ("上证 e 互动", "https://sns.sseinfo.com/"),
    ],
}


def date_for_index(index: int, source_type: str) -> str:
    start_by_type = {
        "policy": date(2024, 1, 8),
        "announcement": date(2024, 2, 5),
        "news": date(2024, 3, 4),
        "ir_qa": date(2024, 4, 1),
    }
    current = start_by_type[source_type] + timedelta(days=index * 23)
    end = date(2026, 6, 24)
    if current > end:
        current = date(2024, 1, 8) + timedelta(days=(index * 17) % ((end - date(2024, 1, 8)).days))
    return current.isoformat()


def build_auto_document(doc_id: str, source_type: str, stock: tuple[str, str, str, float], index: int) -> dict[str, str]:
    _code, stock_name, sector, _market_cap = stock
    topic = SECTOR_TOPICS[sector]
    source_name, url = SOURCE_INFO[source_type][index % len(SOURCE_INFO[source_type])]
    publish_time = date_for_index(index, source_type)

    if source_type == "policy":
        title = f"{sector}产业支持政策候选摘要：{topic['policy_object']}"
        content = (
            f"待人工核验摘要样本：{source_name}相关政策线索围绕{topic['policy_keywords']}展开，"
            f"强调{topic['policy_object']}。该候选文本用于扩充 AlphaLens Demo 的政策样本库，"
            f"并显式关联{stock_name}所在的{sector}主业链条。B 角色后续需要核验政策原文、发布日期、适用范围和 URL，"
            "再将本摘要替换为已确认版本。"
        )
    elif source_type == "announcement":
        title = f"{stock_name}{topic['announcement_topic']}公告候选摘要"
        content = (
            f"待人工核验摘要样本：{stock_name}公告线索关注{topic['announcement_topic']}，"
            f"核心产品包括{topic['core_product']}。该候选文本用于演示实体链接、capacity_expansion、"
            "evidence_from_authoritative_source 和 announcement_contains_uncertainty 等字段的自动落表。"
            "后续需由 B 从巨潮资讯网或交易所公告系统核验原公告。"
        )
    elif source_type == "news":
        title = f"{sector}行业{topic['news_topic']}新闻候选摘要"
        content = (
            f"待人工核验摘要样本：财经新闻线索关注{sector}行业的{topic['news_topic']}，"
            f"并提及{stock_name}及其{topic['core_product']}相关业务。该候选文本用于演示主题关注度扩散、"
            "行业价格或需求变化如何进入事件抽取链路。正式版本需保留新闻来源、发布日期和可复核 URL。"
        )
    else:
        title = f"投资者关注{stock_name}{topic['qa_topic']}候选摘要"
        content = (
            f"待人工核验摘要样本：互动平台投资者关注{stock_name}的{topic['qa_topic']}，"
            f"公司回复需结合客户需求、项目进度和市场环境判断。该候选文本用于演示 investor_question_pressure、"
            "management_response_vague 和主营业务相关谓词。正式版本需回到互动易或上证 e 互动核验问答原文。"
        )

    return {
        "doc_id": doc_id,
        "source_type": source_type,
        "title": title,
        "content": content,
        "publish_time": publish_time,
        "source_name": source_name,
        "url": url,
    }


def build_raw_documents() -> list[dict[str, str]]:
    documents = [dict(row) for row in RAW_DOCUMENTS]
    next_id = len(documents) + 1
    source_plan = ["policy", "announcement", "news", "ir_qa"]
    target_count = 120
    generated_index = 0
    while len(documents) < target_count:
        stock = STOCK_POOL[generated_index % len(STOCK_POOL)]
        source_type = source_plan[generated_index % len(source_plan)]
        documents.append(build_auto_document(f"S{next_id:03d}", source_type, stock, generated_index))
        next_id += 1
        generated_index += 1
    return documents


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def iter_business_days(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def generate_market_data() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    business_days = list(iter_business_days(date(2024, 1, 2), date(2026, 6, 30)))
    sector_bias = {"光伏": -0.00005, "锂电": 0.00010, "风电": 0.00004, "储能": 0.00012, "整车": 0.00008}

    for stock_idx, (code, _name, sector, market_cap) in enumerate(STOCK_POOL):
        base_price = max(8.0, min(260.0, market_cap / 38.0 + 12.0 + stock_idx * 0.45))
        close = base_price
        volume_base = int(max(800_000, market_cap * 1_850_000))
        for day_idx, current_day in enumerate(business_days):
            wave = math.sin((day_idx + stock_idx * 3) / 17.0) * 0.010
            cycle = math.cos((day_idx + stock_idx) / 43.0) * 0.004
            drift = 0.00015 + sector_bias[sector]
            daily_return = drift + wave + cycle
            open_price = close * (1 + math.sin((day_idx + stock_idx) / 11.0) * 0.006)
            close = max(2.0, open_price * (1 + daily_return))
            high = max(open_price, close) * (1 + 0.008 + abs(math.sin(day_idx / 13.0)) * 0.005)
            low = min(open_price, close) * (1 - 0.008 - abs(math.cos(day_idx / 19.0)) * 0.004)
            volume = int(volume_base * (1 + 0.18 * math.sin((day_idx + stock_idx) / 23.0)))
            rows.append(
                {
                    "trade_date": current_day.isoformat(),
                    "stock_code": code,
                    "open": f"{open_price:.2f}",
                    "high": f"{high:.2f}",
                    "low": f"{low:.2f}",
                    "close": f"{close:.2f}",
                    "volume": str(max(volume, 0)),
                    "adj_factor": "1.0",
                }
            )
    return rows


def should_write_input(path: Path, *, force_sample_generation: bool) -> bool:
    if force_sample_generation:
        return True
    if path.exists():
        print(
            f"[AlphaLens] Preserve existing {path.relative_to(ROOT)}; "
            "use --force-sample-generation only when you intentionally want to rebuild demo inputs."
        )
        return False
    return True


def generate_sample_inputs(*, force_sample_generation: bool = False) -> None:
    stock_rows = [
        {
            "stock_code": code,
            "stock_name": name,
            "industry_sector": sector,
            "market_cap": f"{market_cap:.1f}",
        }
        for code, name, sector, market_cap in STOCK_POOL
    ]
    if should_write_input(INPUT_FILES["stock_pool"], force_sample_generation=force_sample_generation):
        write_csv(
            INPUT_FILES["stock_pool"],
            ["stock_code", "stock_name", "industry_sector", "market_cap"],
            stock_rows,
        )
    if should_write_input(INPUT_FILES["raw_documents"], force_sample_generation=force_sample_generation):
        write_csv(
            INPUT_FILES["raw_documents"],
            ["doc_id", "source_type", "title", "content", "publish_time", "source_name", "url"],
            build_raw_documents(),
        )
    if should_write_input(INPUT_FILES["market_data"], force_sample_generation=force_sample_generation):
        write_csv(
            INPUT_FILES["market_data"],
            ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "adj_factor"],
            generate_market_data(),
        )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate AlphaLens demo input CSV files.")
    parser.add_argument(
        "--force-sample-generation",
        action="store_true",
        help="Overwrite stock_pool.csv, raw_documents.csv and market_data.csv with demo inputs.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv or [])
    generate_sample_inputs(force_sample_generation=args.force_sample_generation)


if __name__ == "__main__":
    main(sys.argv[1:])
