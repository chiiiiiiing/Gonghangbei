# AlphaLens C 联调运行手册

生成日期：2026-07-15

本报告仅供研究参考，不构成投资建议

## 目标

帮助 C 在不改 B 线 CSV 字段契约的前提下，复跑流水线、检查研究输出、定位常见问题，并在真实行情替换后完成联调。

## 推荐运行顺序

```bash
.venv/bin/python run_pipeline.py --preserve-inputs
.venv/bin/python scripts/validate_input_preservation.py
.venv/bin/python scripts/audit_text_sources.py
.venv/bin/python scripts/validate_b_data.py
.venv/bin/python scripts/validate_real_market_data.py --input data/sample/market_data.csv
.venv/bin/python scripts/validate_manual_review_results.py
.venv/bin/python scripts/validate_research_outputs.py
.venv/bin/python scripts/validate_delivery_package.py
```

真实文本或真实行情开始写入后，不要使用 `--force-sample-generation`。如只重算 B 线中间表，用 `.venv/bin/python run_b_pipeline.py --skip-sample-generation`。

## 当前关键指标

| 指标 | 当前值 | 说明 |
|------|--------|------|
| `avg_rank_ic_5d` | 0.000000 | 按事件入场日计算的 Rank IC 均值 |
| `event_factor_sample_count` | 44 | 事件触发后的因子样本数 |
| `future_info_audit` | pass | 收益窗口均使用 event_time 之后的交易日 |
| `positive_forward_return_rate_5d` | 0.409091 | 事件样本 5 日收益为正的比例 |
| `top_bottom_group_spread_5d` | 0.074152 | G5 减 G1 的 5 日收益差 |

## C 侧重点

- 读取 `stock_code`、`doc_id`、`event_id` 时必须保留字符串，避免前导零丢失。
- `event_forward_returns.csv` 的 `entry_trade_date` 必须严格晚于 `event_time`。
- `factors.csv` 中 `trigger_event_ids` 和 `trigger_rule_ids` 必须能回溯到 `events.csv` 和 `rules.csv`。
- 替换真实行情后，需要重新生成收益、规则、因子、报告和 Demo 展示数据。
- 当前 `adj_factor=1.000000` 是已接受的占位字段，不是真实复权因子序列；不得据此还原未复权价格或宣称数据供应商已认证。
- 不改变 `参考文档/数据格式规范.md` 中锁定字段名。

## 常见问题定位

| 现象 | 优先检查 |
|------|----------|
| 股票代码变成 274 或 2594 | CSV 读取时是否把 `stock_code` 当数字读入 |
| 未来函数审计失败 | `entry_trade_date` 是否不晚于 `event_time` |
| 因子没有触发规则 | 谓词是否缺失 MVP 字段，或规则支持数低于 5 |
| Demo 报告缺失 | `查看材料/因子研究报告.md` 是否已生成 |
| 交付包自检失败 | 先打开 `查看材料/交付包自检报告.md` 看 Errors |
