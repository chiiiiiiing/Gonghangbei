# AlphaLens

AlphaLens 将政策、公告、财经新闻和互动问答等非结构化金融文本，转化为可解释、可回测、可复用的另类因子研究素材。系统定位是量化研究助手，不预测股票价格，不提供投资建议。

本报告仅供研究参考，不构成投资建议

## 从这里开始

比赛 Demo、完整使用说明、测试案例、配置示例和验收截图统一放在 [`可演示成果/`](可演示成果/README.md)。

最快启动方式：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python 可演示成果/启动演示.py
```

浏览器打开 [http://127.0.0.1:8701](http://127.0.0.1:8701)。

## 仓库结构

```text
├── 可演示成果/       # Demo 统一入口、完整说明、案例、配置和截图
├── app/              # Flask API、单页界面和本地 Plotly
├── src/              # AI、文本流水线、回测和报告代码
├── data/sample/      # Git 跟踪的正式样例 CSV
├── tests/            # Python 与 Playwright 测试
├── scripts/          # 数据获取、审计、校验和材料生成脚本
├── 查看材料/         # 研究报告、审计、人工表和团队联调材料
└── 参考文档/         # 赛题、字段契约、Schema、Prompt 和方法资料
```

源码和正式数据只保留一份。`可演示成果/` 通过统一入口复用 `app/`、`src/` 和 `data/sample/`，不维护第二套 Demo 数据。

## AI 模式

模式一严格执行：

```text
新文本
→ Embedding 检索相似冻结规则
→ deepseek-v4-flash 返回结构化 JSON
→ 程序校验股票、事件、证据和谓词
→ 确定性谓词与 AI 判断对照
→ 冻结规则匹配
→ 候选因子
```

模式一缺少 Key、API 失败或模型输出不合法时直接报错，不回退、不生成候选因子。模式二是独立的纯规则复现模式，页面会明确标记未调用 AI。

## 数据链路

```text
raw_documents.csv
→ entity_links.csv
→ events.csv
→ predicates.csv
→ predicate_matrix.csv
→ event_forward_returns.csv
→ rules.csv
→ factors.csv / factor_snapshot.csv
→ backtest_metrics.csv
→ 查看材料/因子研究报告.md
```

新文本只生成当下候选因子，不写回历史 CSV，也不获得尚未发生的未来收益。

## 安全复跑

真实文本已经写入 `data/sample/raw_documents.csv`，只能使用保留输入模式：

```bash
.venv/bin/python run_pipeline.py --preserve-inputs
```

只重算 B 线：

```bash
.venv/bin/python run_b_pipeline.py --skip-sample-generation
```

不要使用 `--force-sample-generation`，否则可能覆盖人工核验文本。

## 验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest -v tests/test_ai_research_layer.py
.venv/bin/python scripts/validate_input_preservation.py
.venv/bin/python scripts/validate_b_data.py
.venv/bin/python scripts/validate_research_outputs.py
.venv/bin/python scripts/validate_delivery_package.py
```

```bash
cd tests
npm ci
npx playwright install chromium
ALPHALENS_PYTHON=../.venv/bin/python npm test
```

## 重要边界

- 不改变 `参考文档/数据格式规范.md` 锁定的 CSV 字段名。
- 股票代码使用 6 位字符串，不带交易所后缀。
- 日期使用 `YYYY-MM-DD`。
- CSV 布尔值使用小写 `true` / `false`。
- 回测必须满足 `event_time < entry_trade_date`。
- `data/raw/`、`data/processed/`、`data/external/` 不提交 Git。
- 当前 `adj_factor=1` 只是字段占位，不是真实复权因子序列。
- AI 候选规则仍需历史统计和人工金融口径审核。

本报告仅供研究参考，不构成投资建议
