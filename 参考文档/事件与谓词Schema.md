# B 角色事件与谓词 Schema

版本：v0.1
日期：2026-07-13

## 事件类型

| event_type | 中文名 | 定义 | MVP |
|------------|--------|------|-----|
| policy_support | 政策利好 | 政策、通知、行动方案、税收优惠、补贴、产业支持等直接支持相关行业或公司主营业务 | 是 |
| regulatory_penalty | 监管处罚 | 行政处罚、立案调查、交易所纪律处分、通报批评等负向监管事件 | 否 |
| inquiry_letter_pressure | 问询函压力 | 交易所问询、监管问询、关注函等可能反映信息披露压力的事件 | 否 |
| earnings_quality_anomaly | 盈利质量异常 | 业绩变脸、大额减值、收入确认争议、现金流异常等 | 否 |
| product_price_increase | 产品涨价 | 产品价格上调、原材料价格驱动下游涨价、供需紧张带来的价格上涨 | 否 |
| supply_chain_disruption | 供应链中断 | 缺芯、停产、断供、物流或地缘扰动影响供应链 | 否 |
| capacity_expansion | 产能扩张 | 新建产线、项目投产、扩产、募投项目推进、订单驱动扩产 | 否 |
| investor_question_pressure | 投资者追问压力 | 互动平台上投资者对产能、订单、利润、技术路线等问题集中追问 | 否 |
| attention_spread | 关注度扩散 | 政策、公告或新闻导致主题关注升温，媒体报道或讨论热度短期增加 | 是 |

## 谓词列表

| predicate_name | 类型 | 定义 | MVP |
|----------------|------|------|-----|
| has_policy_support | boolean | 事件是否包含明确的政策支持、税收优惠、补贴、规划或专项行动 | 是 |
| policy_directly_related_to_business | boolean | 政策或事件是否直接作用于公司主营业务或所在产业链核心环节 | 是 |
| event_mentions_core_product | boolean | 事件证据是否提及公司核心产品或关键业务，如电池、光伏组件、储能系统、整车 | 否 |
| evidence_from_authoritative_source | boolean | 证据是否来自政府、交易所、公司公告、权威财经媒体等高可信来源 | 是 |
| social_attention_spikes | boolean | 事件是否体现主题关注度短期上升，如多源报道、政策专项行动、互动问答集中 | 是 |
| institutional_attention_increases | boolean | 是否有机构、研报、调研或投资者关系活动增加的迹象 | 否 |
| investor_questions_increase | boolean | 是否体现投资者提问增多或追问同一主题 | 否 |
| management_response_vague | boolean | 管理层回复是否模糊、回避、仅作风险提示且缺少实质信息 | 否 |
| announcement_contains_uncertainty | boolean | 公告是否包含不确定性、审批、交付、价格、客户、履约等风险提示 | 否 |
| event_evidence_strength | score | 事件证据强度，0 到 1，来源权威性、证据直接性和文本清晰度越高越接近 1 | 是 |
| event_has_short_term_price_impact | score | 历史上类似事件对短期收益或成交活跃度的潜在影响强度，0 到 1 | 是 |

## 取值规范

- boolean 值必须写成小写字符串：`true` 或 `false`。
- score 值必须写成字符串形式的小数，如 `0.85`。
- 每个事件至少包含 6 个 MVP 谓词。
- 谓词判断必须给出简短 rationale，便于 A 写案例、C 做审计。

## MVP 规则假设

```text
has_policy_support
AND policy_directly_related_to_business
AND social_attention_spikes
-> short_term_theme_momentum
```

该规则只用于量化研究和因子挖掘，不表示股价预测，也不构成投资建议。
