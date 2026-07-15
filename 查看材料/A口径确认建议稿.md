# AlphaLens A 口径确认建议稿

生成日期：2026-07-15

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
| `capacity_expansion` | 30 | 待确认 | 自动抽取候选口径 |
| `investor_question_pressure` | 30 | 待确认 | 自动抽取候选口径 |

## 当前谓词覆盖

| predicate_name | 样本数 | A 是否确认 | 建议确认点 |
|----------------|--------|------------|------------|
| `announcement_contains_uncertainty` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `event_evidence_strength` | 60 | 待确认 | 分数区间和解释口径是否合理 |
| `event_has_short_term_price_impact` | 60 | 待确认 | 分数区间和解释口径是否合理 |
| `event_mentions_core_product` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `evidence_from_authoritative_source` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `has_policy_support` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `institutional_attention_increases` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `investor_questions_increase` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `management_response_vague` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `policy_directly_related_to_business` | 60 | 待确认 | true/false 判断边界是否清晰 |
| `social_attention_spikes` | 60 | 待确认 | true/false 判断边界是否清晰 |

## 建议 A 重点确认

1. `policy_support` 是否只覆盖政策利好，还是也包含产业行动方案、税收优惠、补贴、目录管理。
2. `capacity_expansion` 是否包括海外认证、交付能力、募投项目推进等间接扩产表述。
3. `investor_question_pressure` 是否应该视为关注度信号，还是偏风险/不确定性信号。
4. `social_attention_spikes` 的判断边界是否接受自动规则中的多源报道、政策专项行动、互动问答集中。
5. `event_has_short_term_price_impact` 是否作为经验强度分数保留，还是改名为更中性的 `historical_attention_impact_score`。

## 对外表述边界

- 可以说：将文本事件结构化为可解释因子候选信号。
- 可以说：通过未来函数审计和样例回测验证研究链路。
- 不应说：系统预测股价、推荐买卖、保证收益。
- 不应把当前候选行情结果描述为正式实证结论。
