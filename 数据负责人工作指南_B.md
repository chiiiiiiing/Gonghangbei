# AlphaLens 数据负责人（B 角色）工作指南

版本：v1.0
日期：2026-07-12
截止：2026-08-15

## 你是 B 角色

你的核心工作是**把原始金融文本变成结构化的数据和事件**。你的产出是整个项目的"原料"——没有你的数据，A 没有逻辑可讲，C 没有代码可跑。

你的交付物清单：
1. `stock_pool.csv` —— 股票池
2. `raw_documents.csv` —— 原始文本库
3. `entity_links.csv` —— 实体链接结果
4. `events.csv` —— 事件抽取结果
5. `predicates.csv` —— 谓词判断结果
6. `market_data.csv` —— 行情数据
7. 每阶段的数据质量报告
8. 至少 5 个解释案例（含文本原文、事件、收益路径）

---

## 第一阶段：方向冻结与基建（07/12 — 07/14）

### 今天（07/12）就要做的事

- [ ] 确认自己是 B 角色，理解这份指南
- [ ] 把这份文档和 `工作推进与分工文档.md` 发给 C，跟他确认 CSV 字段名、日期格式、股票代码格式

### 明天（07/13）要做的事

- [ ] 确定数据来源清单

| 类型 | 推荐来源 | 搜索关键词 |
|------|----------|-----------|
| 政策 | 财政部、工信部、发改委官网 | 新能源汽车、光伏、锂电、储能、风电 |
| 公司公告 | 巨潮资讯网 cninfo.com.cn | 各公司公告检索 |
| 财经新闻 | 财联社、证券时报、21 世纪经济报道 | 新能源 / 光伏 / 锂电 相关新闻 |
| 互动问答 | 上证 e 互动、深交所互动易 | 各公司投资者问答 |

- [ ] 确定行业细分结构

```
新能源
├── 光伏（隆基绿能、通威股份、阳光电源、TCL中环、晶澳科技）
├── 锂电/动力电池（宁德时代、亿纬锂能、国轩高科、欣旺达、鹏辉能源）
├── 风电（金风科技、明阳智能、运达股份、三一重能）
├── 储能（阳光电源、派能科技、固德威、锦浪科技）
└── 整车/新能源车（比亚迪、赛力斯、长安汽车、长城汽车、上汽集团）
```

- [ ] 提前收集 5 条**高质量政策/公告文本**，用于 Demo 页面展示：

示例要求：
- 文本内容完整（不少于 200 字）
- 来源明确（财政部、公司公告等）
- 时间在 2024-2026 年间
- 直接涉及主营业务

收集后按这个模板整理：

```csv
doc_id,source_type,title,content,publish_time,source_name,url
S001,policy,财政部延续新能源汽车购置税减免政策,...

```

### 后天（07/14）要做的事

- [ ] 与 A 确认事件类型列表

MVP 先做 2 种事件类型，但整个 schema 需要 8 种。先定义好全部，代码按 MVP 实现。

| 事件类型 | 含义 | MVP |
|----------|------|-----|
| policy_support | 政策利好：税收优惠、补贴、产业支持 | ✅ |
| regulatory_penalty | 监管处罚：罚款、立案、通报批评 | |
| inquiry_letter_pressure | 问询函压力：交易所问询 | |
| earnings_quality_anomaly | 盈利质量异常：业绩变脸、减值 | |
| product_price_increase | 产品涨价：原材料涨价、需求拉动 | |
| supply_chain_disruption | 供应链中断：缺芯、断供 | |
| capacity_expansion | 产能扩张：新建产线、投产 | |
| investor_question_pressure | 投资者追问压力：互动问答密集 | |

- [ ] 与 A 确认谓词 schema

| 谓词 | 含义 | 值类型 | MVP |
|------|------|--------|-----|
| has_policy_support | 是否存在明确政策支持 | boolean | ✅ |
| policy_directly_related_to_business | 政策是否直接相关主营业务 | boolean | ✅ |
| event_mentions_core_product | 是否提及核心产品 | boolean | |
| evidence_from_authoritative_source | 证据来自权威来源 | boolean | ✅ |
| social_attention_spikes | 关注度短期上升 | boolean | ✅ |
| institutional_attention_increases | 机构关注增加 | boolean | |
| investor_questions_increase | 投资者提问增加 | boolean | |
| management_response_vague | 管理层回应模糊 | boolean | |
| announcement_contains_uncertainty | 公告包含不确定性表述 | boolean | |
| event_has_short_term_price_impact | 类似事件历史上是否有短期影响 | score 0-1 | ✅ |
| event_evidence_strength | 事件的证据强度 | score 0-1 | ✅ |

---

## 第二阶段：数据收集与 Schema 定稿（07/15 — 07/21）

这是你最忙的一周。目标：所有数据到位，事件抽取和谓词判断的 prompt 写好并跑出第一条结果。

### 日计划

#### 07/15（周一）

**任务：股票池 + 第一批政策文本**

上午：
- [ ] 整理 30 只新能源股票清单
  - 光伏 6 只：隆基绿能(601012)、通威股份(600438)、阳光电源(300274)、TCL中环(002129)、晶澳科技(002459)、天合光能(688599)
  - 锂电 6 只：宁德时代(300750)、亿纬锂能(300014)、国轩高科(002074)、欣旺达(300207)、鹏辉能源(300438)、璞泰来(603659)
  - 风电 5 只：金风科技(002202)、明阳智能(601615)、运达股份(300772)、三一重能(688349)、中材科技(002080)
  - 储能 5 只：派能科技(688063)、固德威(688390)、锦浪科技(300763)、德业股份(605117)、科华数据(002335)
  - 整车 5 只：比亚迪(002594)、赛力斯(601127)、长安汽车(000625)、长城汽车(601633)、上汽集团(600104)
  - 其他 3 只：当升科技、恩捷股份、先导智能（锂电材料/设备）
- [ ] 保存为 `data/sample/stock_pool.csv`
- [ ] 收集 10 条政策文本（财政部/工信部新能源相关政策）

下午：
- [ ] 把 10 条政策文本录入 `data/sample/raw_documents.csv`
- [ ] 找 C 确认行情数据格式（他需要什么样的日期/代码格式）

#### 07/16（周二）

**任务：公司公告 + 新闻**

上午：
- [ ] 从巨潮资讯网收集 10 条公司公告（新能源公司 2024-2026 年）
  - 内容类型：产能扩张、订单公告、业绩预告、重大合同
- [ ] 录入 `raw_documents.csv`

下午：
- [ ] 从财联社/证券时报收集 10 条新能源财经新闻
  - 内容类型：价格变动、行业数据、政策解读、市场分析
- [ ] 录入 `raw_documents.csv`

#### 07/17（周三）

**任务：互动问答 + 行情数据**

上午：
- [ ] 从上证 e 互动 / 深交所互动易收集 5-10 条互动问答
  - 找关于：产能、订单、新技术、政策影响的投资者提问
- [ ] 录入 `raw_documents.csv`

下午：
- [ ] 获取 30 只股票行情数据（2024-01-01 至 2026-06-30）
  - 字段：`trade_date, stock_code, open, high, low, close, volume, adj_factor`
  - 格式确认：等 C 确认统一格式
- [ ] 保存为 `data/sample/market_data.csv`

#### 07/18（周四）

**任务：实体链接脚本 + 事件抽取 prompt**

上午：
- [ ] 编写实体链接脚本 `link_entities.py`

```python
# 你能通过这个脚本或手动方式完成
# 输入：raw_documents.csv
# 输出：entity_links.csv
# 方法：公司名称映射表 + LLM 辅助识别

# 先建立 company_name_map：
{
    "宁德时代": "300750",
    "宁德": "300750",       # 简称映射
    "CATL": "300750",
    "比亚迪": "002594",
    "BYD": "002594",
    "隆基绿能": "601012",
    "隆基": "601012",
    ...
}
```

- [ ] 运行脚本，产出 `entity_links.csv`
- [ ] 人工检查 10 条结果，准确率 > 80%

下午：
- [ ] 编写事件抽取 prompt `extract_events_prompt.txt`

```text
# extract_events_prompt.txt 内容框架

你是一个金融事件抽取专家。给定一篇金融文本，请按以下 JSON schema 抽取事件。

- event_type: policy_support | regulatory_penalty | ...（按定义的事件类型列表）
- related_stocks: [{"code": "300750", "name": "宁德时代", "confidence": 0-1}]
- event_time: 事件发生日期 (YYYY-MM-DD)
- subject: 事件主体
- object: 事件客体
- impact_path: 影响路径描述
- evidence_text: 原文证据片段
- evidence_strength: 0-1 之间

示例输入：
[文本内容]

示例输出：
{
  "events": [
    {
      "event_type": "policy_support",
      ...
    }
  ]
}
```

- [ ] 用之前整理的 5 条高质量文本测试 prompt 效果

#### 07/19（周五）

**任务：谓词判断 prompt + 第一批全量抽取**

上午：
- [ ] 编写谓词判断 prompt `ground_predicates_prompt.txt`

```text
# ground_predicates_prompt.txt 内容框架

给定一个已抽取的金融事件，判断以下谓词的取值。

每个谓词输出 true/false 或 0-1 score，并附理由。

谓词列表：
1. has_policy_support（boolean）：...
2. policy_directly_related_to_business（boolean）：...
...

示例输入：
{事件 JSON}

示例输出：
{
  "predicates": {
    "has_policy_support": {"value": true, "rationale": "..."},
    ...
  }
}
```

下午：
- [ ] 对 30 条文本运行事件抽取 → `events.csv`
- [ ] 对这 30 条事件运行谓词判断 → `predicates.csv`
- [ ] 输出给 C 检查和联调

#### 07/20-21（周六日）

**任务：补漏 + 质量检查**

- [ ] 补齐所有 100+ 条文本
- [ ] 检查是否存在数据缺失
- [ ] 随机 10 条检查事件类型准确率
- [ ] 随机 10 条检查谓词赋值合理性
- [ ] 主动找 C 联调一次（约 30 分钟），确认格式兼容

---

## 第三阶段：算法闭环协同（07/22 — 07/28）

你的数据已经到位，这个阶段 C 跑代码，你配合做三件事：

1. **质量把关**：检查事件抽取和谓词判断结果
2. **案例整理**：找出 3 个最清晰的成功案例
3. **反馈迭代**：根据回测结果调整事件/谓词定义

### 具体任务

- [ ] 检查全部 100 条文本的事件抽取结果
- [ ] 检查全部事件的谓词判断结果（是否有明显错误）
- [ ] 输出最终版 `events.csv` 和 `predicates.csv`
- [ ] 整理 3 个典型案例

案例模板：

```markdown
## 案例 1：新能源汽车购置税减免政策延续

### 原文
财政部发布《关于延续新能源汽车免征车辆购置税政策的公告》...

### 抽取结果
- 事件类型：policy_support
- 关联股票：比亚迪(0.95)、宁德时代(0.88)
- 证据强度：0.92

### 谓词结果
- has_policy_support: true
- policy_directly_related_to_business: true
- evidence_from_authoritative_source: true
- social_attention_spikes: true

### 规则触发
规则 1（评分 0.83）：has_policy_support AND ... → short_term_theme_momentum

### 未来收益（5 日后）
比亚迪：+6.2%
宁德时代：+4.8%

### 金融逻辑
新能源汽车购置税减免直接降低购车成本...
对主营业务为整车制造的比亚迪形成直接利好...
```

---

## 第四阶段：回测可信化协助（07/29 — 08/04）

这个阶段 A 在写文档，C 在加回测指标。你需要：

- [ ] 检查所有文本和事件的时间戳是否在行情数据范围内
- [ ] 补充遗漏的数据
- [ ] 整理更多案例（至少 5 个，含成功和失败案例）
- [ ] 为 A 提供 PPT 可用的案例素材

---

## 第五阶段：Demo 与最终数据（08/05 — 08/10）

- [ ] 最终版数据集：确保每列都有数据，无空值
- [ ] 整理解释案例报告（图文对应，可直接粘贴到 PPT）
- [ ] 协助 C 测试 Demo 在离线环境能否正常运行
- [ ] 检查和修正事件抽取中的错误

---

## 第六阶段：定稿提交（08/11 — 08/15）

- [ ] 检查所有数据附件（CSV 文件）的完整性和一致性
- [ ] 整理解释案例合集（5 个案例，用于答辩备用）
- [ ] 准备 1 分钟电梯演讲版本
- [ ] 协助 A 检查 PPT 中的数据和图表

---

## 数据文件规范（与 C 的接口约定）

不要改这些字段名，C 的代码全部依赖这些名字。

### `stock_pool.csv`

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| stock_code | str | 股票代码（6 位数字） | 300750 |
| stock_name | str | 股票简称 | 宁德时代 |
| industry_sector | str | 行业细分 | 锂电 |
| market_cap | float | 总市值（亿元） | 12000 |

### `raw_documents.csv`

| 字段 | 类型 | 示例 |
|------|------|------|
| doc_id | str | S001 |
| source_type | str | policy / announcement / news / ir_qa |
| title | str | 财政部延续新能源汽车购置税减免政策 |
| content | str | 正文... |
| publish_time | str | 2025-03-15 |
| source_name | str | 财政部 |
| url | str | http://... |

### `entity_links.csv`

| 字段 | 类型 | 示例 |
|------|------|------|
| doc_id | str | S001 |
| stock_code | str | 300750 |
| stock_name | str | 宁德时代 |
| industry | str | 锂电 |
| confidence | float | 0.88 |
| evidence | str | 文本提及"宁德时代" |

### `events.csv`

| 字段 | 类型 | 示例 |
|------|------|------|
| event_id | str | E001 |
| doc_id | str | S001 |
| stock_code | str | 300750 |
| event_type | str | policy_support |
| event_time | str | 2025-03-15 |
| subject | str | 财政部 |
| object | str | 新能源汽车购置税 |
| impact_path | str | 税收减免→降低购车成本→需求提升 |
| evidence_text | str | "延续免征车辆购置税" |
| evidence_strength | float | 0.92 |

### `predicates.csv`

| 字段 | 类型 | 示例 |
|------|------|------|
| event_id | str | E001 |
| predicate_name | str | has_policy_support |
| value | str | true |
| confidence | float | 0.95 |
| rationale | str | 财政部明确发布减免政策 |

### `market_data.csv`

| 字段 | 类型 | 示例 |
|------|------|------|
| trade_date | str | 2024-01-02 |
| stock_code | str | 300750 |
| open | float | 150.0 |
| high | float | 155.0 |
| low | float | 148.0 |
| close | float | 153.0 |
| volume | float | 50000000 |
| adj_factor | float | 1.0 |

---

## 每日工作节奏

```
10:00 — 开始今天的数据工作
12:00 — 午休
14:00 — 继续工作
16:00 — 站会（15 分钟）
  - 报告今天的进展
  - 说出遇到的阻塞问题
  - 需要 C 协助什么
18:00 — 继续工作
21:00 — 可选晚间讨论
```

## 快速参考

### 工具
- 巨潮资讯网: http://www.cninfo.com.cn/
- 东方财富数据中心: https://data.eastmoney.com/
- Tushare / AkShare: Python 行情数据包
- 财政部政策发布: http://www.mof.gov.cn/
- 互动平台: http://sns.sseinfo.com/ / https://irm.cninfo.com.cn/

### 沟通
- 遇到拿不准的事件类型 → 在群里问 A
- 遇到数据格式不确定 → 直接找 C 确认
- 被阻塞超过 2 小时 → @全体 求助

---

## 附录：数据模板快速创建

以下 CSV 文件需要你在第一阶段创建。如果 C 已经创建了空模板，直接往里面填数据；如果 C 还没做，你按这个格式自己建。

### 1. `data/sample/stock_pool.csv`

```csv
stock_code,stock_name,industry_sector,market_cap
300750,宁德时代,锂电,12000
002594,比亚迪,整车,8500
601012,隆基绿能,光伏,3200
```

### 2. `data/sample/raw_documents.csv`

```csv
doc_id,source_type,title,content,publish_time,source_name,url
S001,policy,财政部延续新能源汽车购置税减免政策延续至2027年,"...",2025-03-15,财政部,http://...
```

### 3. `data/sample/entity_links.csv`

```csv
doc_id,stock_code,stock_name,industry,confidence,evidence
S001,300750,宁德时代,锂电,0.88,文本提及"宁德时代"
```

### 4. `data/sample/events.csv`

```csv
event_id,doc_id,stock_code,event_type,event_time,subject,object,impact_path,evidence_text,evidence_strength
E001,S001,300750,policy_support,2025-03-15,财政部,新能源汽车购置税,税收减免→降低购车成本→需求提升,延续免征车辆购置税,0.92
```

### 5. `data/sample/predicates.csv`

```csv
event_id,predicate_name,value,confidence,rationale
E001,has_policy_support,true,0.95,财政部明确发布减免政策
E001,policy_directly_related_to_business,true,0.90,新能源车业务为公司核心收入来源
```

### 6. `data/sample/market_data.csv`

```csv
trade_date,stock_code,open,high,low,close,volume,adj_factor
2024-01-02,300750,150.0,155.0,148.0,153.0,50000000,1.0
```
