# AlphaLens

AlphaLens 将政策、公告、财经新闻和互动问答等非结构化金融文本，转化为可解释、可回测、可复用的另类因子研究素材。系统定位是量化研究助手，不预测股票价格，不提供投资建议。

本报告仅供研究参考，不构成投资建议

## 当前演示

演示端使用 Flask 提供本地 API，浏览器负责交互与 Plotly 图表展示。新文本分析和历史回测采用两条清晰边界：

1. 新输入文本在内存中完成实体链接、事件抽取、谓词落地、冻结规则匹配和候选因子计算。
2. 历史回测只读取正式流水线生成的 `backtest_metrics.csv`、`group_returns.csv` 和 `rank_ic_timeseries.csv`。
3. 新文本不会被伪装成已经拥有未来收益的单次回测样本，也不会写回历史 CSV。

页面提供两个工作区：

- **新文本分析**：使用核验库中的真实详情页案例，展示来源、实体、事件、19 个谓词、冻结规则和候选因子公式，可在关联股票之间切换追溯。
- **历史研究概览**：独立展示正式回测指标、分组收益、Rank IC、30 只股票的最新因子截面和 12 条冻结规则。

新文本页已接入可选的 AI 研究层。页面提供 `DeepSeek API Key（单次使用）` 密码框，填写后由后端调用 `deepseek-v4-flash`，让模型按结构化 JSON 提出事件、实体、19 个谓词和候选规则。Key 仅随本次分析请求使用，响应后自动清空，不写入浏览器存储、CSV 或仓库。程序会校验股票池、枚举、数值和原文证据；AI 候选不直接覆盖冻结规则，也不直接产生回测指标。

当前跟踪样例包含 30 只新能源股票、130 条文本、210 个事件、12 条合格规则和 167 条历史因子样本。具体数量以演示页右侧的数据版本状态为准。

## 本地启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app/server.py
```

浏览器打开 [http://127.0.0.1:8701](http://127.0.0.1:8701)。Plotly 已放在 `app/vendor/`，页面运行不依赖外部 CDN。

## 大模型 API

配置说明见 `查看材料/大模型API接入与演示手册.md`。最短使用方式：

```bash
.venv/bin/python app/server.py
```

打开页面，在左侧填写 DeepSeek API Key，选择“模式一：大模型候选 + 规则校验”并点击“开始分析”。默认调用地址为 `https://api.deepseek.com`，模型为 `deepseek-v4-flash`。模式一固定执行本地字符 n-gram Embedding 检索，再调用大模型返回结构化 JSON；未填写 Key、模型超时或返回非法结构时直接报错，不生成候选因子。需要离线复现时，明确切换到“模式二：仅规则复现”。

## 安全复跑流水线

真实文本已经写入 `data/sample/raw_documents.csv`，只能使用保留输入模式：

```bash
.venv/bin/python run_pipeline.py --preserve-inputs
```

也可以分段运行：

```bash
.venv/bin/python run_b_pipeline.py --skip-sample-generation
.venv/bin/python -m src.backtest.demo_engine
.venv/bin/python -m src.report.generate_research_report
```

禁止直接运行会重新生成样例输入的旧命令，以免覆盖人工核验成果。

## 验证

```bash
.venv/bin/python -m unittest -v tests/test_ai_research_layer.py
.venv/bin/python scripts/validate_input_preservation.py
.venv/bin/python scripts/validate_b_data.py
.venv/bin/python scripts/validate_research_outputs.py
.venv/bin/python scripts/validate_delivery_package.py

cd tests
npm ci
npx playwright install chromium
ALPHALENS_PYTHON=../.venv/bin/python npm test
```

## 数据链路

```text
raw_documents.csv
-> entity_links.csv
-> events.csv
-> predicates.csv
-> predicate_matrix.csv
-> rules.csv
-> factors.csv / factor_snapshot.csv
-> backtest_metrics.csv
-> 查看材料/因子研究报告.md
```

在线接口 `src/pipeline/live_analysis.py` 复用批处理的实体别名、事件类型判断和 `ground_event_predicates()`，避免维护第二套演示规则。

AI 运行时位于 `src/ai/`：`gateway.py` 负责 OpenAI 兼容 HTTP 请求，`prompts.py` 维护版本化 Prompt 与 JSON Schema，`research_layer.py` 负责本地 Embedding 检索、输出校验和候选规则状态管理。`app/server.py` 为页面 Key 创建请求级 DeepSeek 客户端，不保存或回传凭证。

## 文档入口

- 项目规范与分工：`参考文档/工作推进与分工文档.md`
- CSV 字段契约：`参考文档/数据格式规范.md`
- 当前材料导航：`查看材料/材料索引.md`
- 团队对接步骤：`查看材料/团队对接手册.md`
- Demo 运行方式：`查看材料/C联调运行手册.md`
- AI 配置与演示：`查看材料/大模型API接入与演示手册.md`

## 已知限制

- 当前行情为东方财富 `fqt=1` 前复权价格候选版。
- `adj_factor=1` 是已接受的字段占位，不是真实复权因子序列，答辩和报告必须明确披露。
- 当前历史样本、规则和股票池规模有限，统计结果只用于研究链路验证。
- 当前演示回测未完整计入交易成本、流动性和做空约束。
- AI 候选规则仍需历史统计和人工金融口径审核，不能直接进入正式因子。
- `data/raw/`、`data/processed/`、`data/external/` 不提交 Git。
