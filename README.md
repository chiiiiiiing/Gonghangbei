# AlphaLens

基于大模型规则归纳的舆情另类因子挖掘与量化研究智能体。

AlphaLens 将政策、公告、财经新闻和互动问答等非结构化金融文本转化为可解释、可回测、可复用的另类因子研究素材。项目定位是 AI 量化研究助手，不预测股票价格，不提供投资建议。

本报告仅供研究参考，不构成投资建议

## Live Demo

&#9654; **https://hechenyu.xin/s/GongHangBei/**

纯静态离线 Demo，包含三个视图：

| View | 功能 |
|---|---|
| **Live Run** | 输入金融文本 → 实体链接 → 事件抽取 → 谓词判断 → 规则触发 → 因子生成 → 回测参考 |
| **Backtest Dashboard** | 预计算回测指标（Rank IC 0.071、ICIR 1.84、多空年化+8.3%）、分组收益图、Rank IC 序列、因子排名 Top-10 |
| **About** | 项目定位、创新点、技术边界 |

浏览器端运行全部 NLP 管道（实体链接、事件抽取、谓词判断、规则匹配、因子计算），无需后端服务，完全离线可用。

## 当前状态

- 30 只新能源样例股票池已生成。
- 120 条摘要化文本已联网替换为可追溯来源候选版，其中 P0 待替换数量为 0，仍需人工抽查事实口径。
- 实体链接 → 事件抽取 → 谓词判断 → 规则归纳 → 因子生成 → 回测审计 → 研究报告已串通。
- 120 条文本已完成程序化联网来源核验，四类各 30 条且 URL 全局唯一；逐条结果见 `查看材料/源文本核验明细.csv`。
- 当前行情使用东方财富 `fqt=1` 前复权价格候选版；项目接受 `adj_factor=1` 作为字段占位，但它不是真实复权因子序列，答辩和报告必须披露该限制。

## 本地运行

### Web Demo（静态，推荐）

```bash
# 直接浏览器打开，无需任何依赖
open app/index.html
```

### Python 管道（完整数据）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_pipeline.py --preserve-inputs
```

## 数据流水线

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

## 文档入口

- 项目规范与分工：`参考文档/工作推进与分工文档.md`
- CSV 字段契约：`参考文档/数据格式规范.md`
- 当前材料导航：`查看材料/材料索引.md`
- 团队对接步骤：`查看材料/团队对接手册.md`
- 统一数据质量报告：`查看材料/数据质量报告.md`

## 关键命令

```bash
.venv/bin/python run_b_pipeline.py --skip-sample-generation
.venv/bin/python scripts/fetch_verified_text_sources.py
.venv/bin/python scripts/fetch_eastmoney_market_data.py --begin 20240101 --end 20260630
.venv/bin/python -m src.backtest.demo_engine
.venv/bin/python -m src.report.generate_research_report
.venv/bin/python scripts/validate_input_preservation.py
.venv/bin/python scripts/audit_text_sources.py
.venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv
.venv/bin/python scripts/validate_manual_review_results.py
.venv/bin/python scripts/validate_b_data.py
.venv/bin/python scripts/validate_research_outputs.py
```

## 重要边界

- 真实文本或真实行情开始写入后，只使用 `--preserve-inputs` / `--skip-sample-generation` 安全复跑；不要使用 `--force-sample-generation`。
- 不提交 `data/raw/`、`data/processed/`、`data/external/`。
- 不改变 `参考文档/数据格式规范.md` 中锁定的六张 CSV 字段名。
- forward return 只使用 `event_time` 之后的交易日计算。
- 当前输出用于量化研究链路验证，不作为真实投资结论。
