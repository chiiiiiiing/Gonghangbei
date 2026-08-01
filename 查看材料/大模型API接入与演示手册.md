# AlphaLens 大模型 API 接入与演示手册

更新日期：2026-08-01

本报告仅供研究参考，不构成投资建议

## 一、现在接入了什么

Demo 已增加一层 OpenAI 兼容模型网关，同时支持云端 API 和本地兼容服务：

```text
新文本
→ Embedding 检索相似冻结规则
→ 大模型结构化 JSON 抽取
→ 股票、事件、证据、谓词和候选规则合法性校验
→ 与确定性谓词结果对照
→ 冻结规则匹配和候选因子计算
```

调用端点为：

- `POST /v1/embeddings`：计算文本与冻结规则的语义相似度。
- `POST /v1/chat/completions`：按 JSON Schema 返回结构化研究候选。

## 二、云端 API 配置

仓库不会保存 API 密钥。先执行：

```bash
cp 配置示例.env .env
```

在本机 `.env` 中设置：

```dotenv
ALPHALENS_AI_MODE=api
OPENAI_API_KEY=你的密钥
ALPHALENS_LLM_BASE_URL=https://api.openai.com/v1
ALPHALENS_LLM_MODEL=gpt-5-mini
ALPHALENS_EMBEDDING_MODEL=text-embedding-3-small
ALPHALENS_AI_JSON_MODE=schema
```

`.env` 已在 `.gitignore` 中，不得提交。

## 三、本地模型配置

本地服务需要兼容 OpenAI 的 Chat Completions 和 Embeddings 请求格式：

```dotenv
ALPHALENS_AI_MODE=local
OPENAI_API_KEY=
ALPHALENS_LLM_BASE_URL=http://127.0.0.1:11434/v1
ALPHALENS_LLM_MODEL=本地对话模型名称
ALPHALENS_EMBEDDING_MODEL=本地向量模型名称
ALPHALENS_AI_JSON_MODE=object
```

若本地服务支持严格 JSON Schema，可把 `ALPHALENS_AI_JSON_MODE` 改为 `schema`。

## 四、启动与检查

```bash
.venv/bin/python app/server.py
curl http://127.0.0.1:8701/api/ai/status
```

`configured=true` 表示模型参数已经配置，不等于远端健康检查通过；完成一次分析后页面出现结构化结果才表示本次调用成功。状态接口只返回模型、模式和 Prompt 版本，不返回密钥。修改 `.env` 后需要重启 Demo。

浏览器打开 `http://127.0.0.1:8701`，在“分析引擎”中选择：

- `大模型候选 + 规则校验`：调用 Embedding 和大模型；失败时自动回退。
- `仅规则复现`：完全不调用模型，用于断网演示和结果复现。

## 五、页面怎样讲

1. 指出右侧“AI 研究层已配置”，展示模型和 Prompt 版本；完成分析后再确认本次调用成功。
2. 选择真实政策案例并开始分析。
3. 展示模型返回的结构化事件、Embedding 相似规则数量和通过校验的谓词数量。
4. 展示 AI 自动提出的候选规则，强调状态是“待统计验证”。
5. 展示 AI 与规则谓词一致数量；不一致结果只用于审计，不覆盖冻结规则。
6. 展示候选因子公式，说明因子仍由冻结规则和确定性程序计算。

## 六、安全边界

- 模型没有配置、超时或返回非法 JSON 时，Demo 自动使用确定性流程。
- 模型提出的股票必须经过 30 只股票池校验；只有原文明确点名的 AI 实体才可作为确定性链接失败时的回退，行业推断实体只保留为审计候选。
- 非原文证据会被标记并禁止作为事件回退依据。
- 候选规则不会直接写入正式 CSV，也不会直接参与因子评分。
- 新文本不会获得尚未发生的未来收益。
- 使用外部 API 前，应确认输入文本允许发送给对应服务。

## 七、验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest -v tests/test_ai_research_layer.py
cd tests && ALPHALENS_PYTHON=../.venv/bin/python npm test
```

Python 测试会启动本地模拟 OpenAI 兼容服务，验证 Chat、Embedding、JSON Schema、非法股票拦截和候选规则状态，不产生真实 API 费用。

已连接状态的验收截图见 `查看材料/Demo大模型研究层截图.png`。截图使用本地模拟模型生成，只证明接口和页面链路，不代表真实模型质量评估已经完成。
