# AlphaLens A 口径确认建议稿

生成日期：2026-07-31

本报告仅供研究参考，不构成投资建议

## 需要 A 确认的核心口径

本建议稿用于让 A 快速确认事件类型、谓词定义和对外表述边界。当前内容是自动候选版，不能替代项目负责人最终判断。

## 建议项目定位

- AlphaLens 是 AI 量化研究助手，不是股票价格预测系统。
- 项目目标是把非结构化金融文本转化为可解释、可回测、可复用的另类因子研究素材。
- 对外表达应强调规则归纳、因子挖掘、回测审计和投研效率，不承诺收益或交易建议。

## 当前事件分布

| event_type | 样本数 | A 是否确认 | 备注 |
|------------|--------|------------|------|
| `attention_spread` | 82 | 待确认 | 自动抽取候选口径 |
| `capacity_expansion` | 11 | 待确认 | 自动抽取候选口径 |
| `earnings_quality_anomaly` | 17 | 待确认 | 自动抽取候选口径 |
| `inquiry_letter_pressure` | 2 | 待确认 | 自动抽取候选口径 |
| `policy_support` | 98 | 待确认 | 自动抽取候选口径 |

## 当前谓词覆盖

| predicate_name | 样本数 | A 是否确认 | 建议确认点 |
|----------------|--------|------------|------------|
| `announcement_contains_uncertainty` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `capacity_policy_support` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `demand_side_policy` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `event_evidence_strength` | 210 | 待确认 | 分数区间和解释口径是否合理 |
| `event_has_short_term_price_impact` | 210 | 待确认 | 分数区间和解释口径是否合理 |
| `event_mentions_core_product` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `evidence_from_authoritative_source` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `has_policy_support` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `institutional_attention_increases` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `investor_questions_increase` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `management_response_vague` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `policy_attention_followup` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `policy_directly_related_to_business` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `risk_or_uncertainty_disclosure` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `social_attention_spikes` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `source_company_announcement` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `source_government_or_exchange` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `source_major_media` | 210 | 待确认 | true/false 判断边界是否清晰 |
| `supply_side_policy` | 210 | 待确认 | true/false 判断边界是否清晰 |

## 建议 A 重点确认

1. `policy_support` 是否只覆盖政策利好，还是也包含产业行动方案、税收优惠、补贴、目录管理。
2. `capacity_expansion` 是否只接受明确的募投项目、产能建设和项目投产事实；评级报告背景描述和泛化交付能力不自动算事件。
3. `investor_question_pressure` 需要多长时间窗和多少条提问才能成立；当前单条互动问答不生成该事件。
4. `social_attention_spikes` 的判断边界是否要求可量化变化或多源报道，单篇新闻和单条问答不自动成立。
5. `event_has_short_term_price_impact` 是否作为经验强度分数保留，还是改名为更中性的 `historical_attention_impact_score`。

## 对外表述边界

- 可以说：将文本事件结构化为可解释因子候选信号。
- 可以说：通过未来函数审计和样例回测验证研究链路。
- 不应说：系统预测股价、推荐买卖、保证收益。
- 不应把当前候选行情结果描述为正式实证结论。
