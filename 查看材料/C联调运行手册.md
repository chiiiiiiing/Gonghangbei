# AlphaLens C 联调运行手册

生成日期：2026-08-02

本报告仅供研究参考，不构成投资建议

## 目标

帮助 C 在不改 B 线 CSV 字段契约的前提下接收真实文本、事件和谓词，复跑规则、因子、回测与 Demo，并给出可复现的联调结论。

## B 与 C 的边界

- B 负责 stock_pool、raw_documents、entity_links、events、predicates 的事实、字段和语义质量。
- C 负责规则搜索、因子计算、收益对齐、回测指标、Flask Demo 和运行环境。
- C 发现 B 数据问题时提交 doc_id/event_id、复现命令和期望结果，不直接改锁定字段或批量覆盖输入。
- B 修改输入或抽取规则后，必须通知 C 重新生成全部研究输出，不能只替换单个下游 CSV。

## B 线锁定输入

| 文件 | 当前行数 | 字段状态 |
|------|----------|----------|
| `data/sample/stock_pool.csv` | 30 | 通过 |
| `data/sample/raw_documents.csv` | 130 | 通过 |
| `data/sample/entity_links.csv` | 461 | 通过 |
| `data/sample/events.csv` | 210 | 通过 |
| `data/sample/predicates.csv` | 3990 | 通过 |
| `data/sample/market_data.csv` | 18002 | 通过 |

## C 线研究输出

| 文件 | 当前行数 | 字段状态 | 用途 |
|------|----------|----------|------|
| `data/sample/predicate_matrix.csv` | 210 | 通过 | 事件-谓词矩阵 |
| `data/sample/event_forward_returns.csv` | 209 | 通过 | 事件后收益与未来函数审计 |
| `data/sample/rules.csv` | 12 | 通过 | 候选规则和支持数 |
| `data/sample/factors.csv` | 167 | 通过 | 事件级因子值与触发路径 |
| `data/sample/factor_snapshot.csv` | 30 | 通过 | Demo 截面展示 |
| `data/sample/group_returns.csv` | 5 | 通过 | 分组收益展示 |
| `data/sample/rank_ic_timeseries.csv` | 23 | 通过 | Rank IC 时序 |
| `data/sample/backtest_metrics.csv` | 5 | 通过 | 报告和 Demo 指标 |

## 推荐运行顺序

```bash
git pull
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_pipeline.py --preserve-inputs
.venv/bin/python scripts/validate_input_preservation.py
.venv/bin/python scripts/audit_text_sources.py
.venv/bin/python scripts/validate_b_data.py
.venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv
.venv/bin/python scripts/validate_manual_review_results.py
.venv/bin/python scripts/validate_research_outputs.py
.venv/bin/python scripts/validate_delivery_package.py
.venv/bin/python 可演示成果/启动演示.py
# 另开终端：cd tests && npm ci && npx playwright install chromium && ALPHALENS_PYTHON=../.venv/bin/python npm test
```

真实文本或真实行情开始写入后，不要使用 `--force-sample-generation`。只重算 B 线时使用 `run_b_pipeline.py --skip-sample-generation`。

## 当前关键指标

| 指标 | 当前值 | 说明 |
|------|--------|------|
| `avg_rank_ic_5d` | 0.000000 | 按事件入场日计算的 Rank IC 均值 |
| `event_factor_sample_count` | 167 | 事件触发后的因子样本数 |
| `future_info_audit` | pass | 收益窗口均使用 event_time 之后的交易日 |
| `positive_forward_return_rate_5d` | 0.532934 | 事件样本 5 日收益为正的比例 |
| `top_bottom_group_spread_5d` | 0.027693 | G5 减 G1 的 5 日收益差 |

## C 侧重点

- 读取 stock_code、doc_id、event_id 时必须保留字符串，避免前导零丢失。
- event_forward_returns.csv 的 entry_trade_date 必须严格晚于 event_time。
- factors.csv 的 trigger_event_ids 和 trigger_rule_ids 必须能回溯到事件和规则。
- 当前 adj_factor=1.000000 是已接受的字段占位，不是真实复权因子序列。
- 不改变 `参考文档/数据格式规范.md` 中锁定字段名。
- Demo 至少检查两个工作区、来源元数据、因子公式、事件/谓词追溯、冻结规则、历史因子截面和报告下载。
- DeepSeek 联调从页面密码框临时填写 Key，确认调用模型为 `deepseek-v4-flash`；不得把 Key 写入代码、`.env`、截图、终端命令或联调记录。
- 打开浏览器开发者工具检查 `/api/analyze`：请求后 Key 输入框自动清空，响应体不含 Key，响应头包含 `Cache-Control: no-store`。
- 模式一必须显示 `local-char-ngram-embedding-v1` 和相似冻结规则命中数；Embedding、DeepSeek 或结构校验任一步失败都应返回错误且不生成候选因子。
- AI 候选规则必须保持待统计验证状态，不得直接写入 rules.csv 或参与因子评分。
- 新文本接口不得临时生成未来收益；历史指标只能读取正式 CSV。

## C 的通过标准

1. 安全流水线退出码为 0，输入保护报告前后 SHA256 一致。
2. B 数据、研究输出和交付包校验均为 0 errors。
3. 收益入场日全部严格晚于事件日。
4. 因子触发事件和规则均可回溯，future_info_ok 全为 true。
5. Demo 从新进程启动后无空白、报错或旧数据缓存。
6. DeepSeek 模拟接口测试通过，且页面 Key 不回显、不持久化、请求结束后清空。
7. 联调记录写明 commit、环境、命令、结果、warning 和是否可冻结数字，但不得记录 Key。

## 常见问题定位

| 现象 | 优先检查 |
|------|----------|
| 股票代码变成 274 或 2594 | CSV 读取时是否把 stock_code 当数字 |
| 未来函数审计失败 | entry_trade_date 是否不晚于 event_time |
| 因子没有触发规则 | 谓词是否缺失，或规则支持数低于 5 |
| Demo 报告缺失 | `查看材料/因子研究报告.md` 是否生成 |
| 交付包自检失败 | 查看 `查看材料/交付包自检报告.md` |

## C 回传模板

```text
C 联调记录
commit：
Python/系统：
运行命令：
errors/warnings：
Demo 页面结果：
发现问题（附 doc_id/event_id）：
需要 B 修改：
是否可进入 PPT 数字冻结：是/否
```
