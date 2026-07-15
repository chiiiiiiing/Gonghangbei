# AlphaLens C 联调数据契约清单

生成日期：2026-07-15

本报告仅供研究参考，不构成投资建议

## 说明

- 本清单用于 B→C 交接时快速确认 CSV 文件、字段顺序和行数。
- `data/sample/*.csv` 是代码读取入口；`查看材料/*.md` 和人工抽检 CSV 是说明与核验材料。
- 正式回测前仍需替换真实来源文本和真实前复权行情。

## B 线锁定 CSV

| 文件 | 行数 | 字段状态 | 字段顺序 |
|------|------|----------|----------|
| `stock_pool.csv` | 30 | 通过 | `stock_code, stock_name, industry_sector, market_cap` |
| `raw_documents.csv` | 120 | 通过 | `doc_id, source_type, title, content, publish_time, source_name, url` |
| `entity_links.csv` | 60 | 通过 | `doc_id, stock_code, stock_name, industry, confidence, evidence` |
| `events.csv` | 60 | 通过 | `event_id, doc_id, stock_code, event_type, event_time, subject, object, impact_path, evidence_text, evidence_strength` |
| `predicates.csv` | 660 | 通过 | `event_id, predicate_name, value, confidence, rationale` |
| `market_data.csv` | 18002 | 通过 | `trade_date, stock_code, open, high, low, close, volume, adj_factor` |

## 研究输出 CSV

| 文件 | 行数 | 字段状态 | 用途 |
|------|------|----------|------|
| `predicate_matrix.csv` | 60 | 通过 | 事件-谓词矩阵 |
| `event_forward_returns.csv` | 20 | 通过 | 事件后收益对齐与未来函数审计 |
| `rules.csv` | 4 | 通过 | 候选规则排序 |
| `factors.csv` | 20 | 通过 | 事件级因子值 |
| `factor_snapshot.csv` | 30 | 通过 | Demo 截面展示 |
| `group_returns.csv` | 5 | 通过 | 分组收益展示 |
| `rank_ic_timeseries.csv` | 3 | 通过 | Rank IC 时序展示 |
| `backtest_metrics.csv` | 5 | 通过 | 报告和 Demo 指标卡 |

## 联调检查顺序

1. C 先按字符串读取 `stock_code`、`doc_id`、`event_id`，避免前导零丢失。
2. 真实文本开始写入后，只使用 `.venv/bin/python run_pipeline.py --preserve-inputs` 或 `run_b_pipeline.py --skip-sample-generation` 复跑。
3. 先跑 `scripts/validate_b_data.py`，再跑 `scripts/validate_research_outputs.py`。
4. 检查 `event_forward_returns.csv` 中 `entry_trade_date` 是否严格晚于 `event_time`。
5. 检查 `factors.csv` 的 `trigger_event_ids` 和 `trigger_rule_ids` 是否能回溯到事件和规则。
6. 替换真实行情后必须重新生成 `event_forward_returns.csv`、`rules.csv`、`factors.csv` 和报告。
