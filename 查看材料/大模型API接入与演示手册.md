# AlphaLens 大模型 API 接入与演示手册

更新日期：2026-08-02

本报告仅供研究参考，不构成投资建议

## 一、现在接入了什么

Demo 已在“新文本分析”左侧增加 `DeepSeek API Key（单次使用）` 密码框。填入 Key 后，后端通过 DeepSeek 官方 OpenAI 兼容接口调用 `deepseek-v4-flash`：

```text
新文本
→ DeepSeek V4 Flash 结构化 JSON 抽取
→ 股票、事件、证据、谓词和候选规则合法性校验
→ 与确定性谓词结果对照
→ 冻结规则匹配和候选因子计算
```

Key 的处理边界：

- 只随当前一次 `/api/analyze` 请求发送给本地 Flask 后端。
- 后端只在该次请求中创建客户端，不写入文件、日志、CSV 或环境变量。
- 分析结束或失败后，页面输入框都会自动清空。
- API 响应设置 `Cache-Control: no-store`，页面不使用 `localStorage` 或 Cookie 保存 Key。

## 二、最短使用步骤

1. 启动 Demo：

```bash
.venv/bin/python app/server.py
```

2. 浏览器打开 `http://127.0.0.1:8701`。
3. 在左侧 `DeepSeek API Key（单次使用）` 中填写自己的 Key。
4. “分析引擎”选择 `大模型候选 + 规则校验`。
5. 选择内置真实案例或填写新文本，点击“开始分析”。
6. 在结果区确认显示模型 `deepseek-v4-flash`、JSON 输出、通过校验的谓词和“待统计验证”候选规则。
7. 确认 Key 输入框已经自动清空。

Key 不要发到聊天、截图、PPT、GitHub 或群聊中。现场投屏时，建议在开始共享屏幕前填写；若必须现场填写，密码框只显示掩码。

## 三、DeepSeek 调用口径

- 默认 Base URL：`https://api.deepseek.com`
- Chat Completions：`POST /chat/completions`
- 模型：`deepseek-v4-flash`
- 输出模式：JSON Object，并在 Prompt 中约束完整结构
- 思考模式：显式关闭，减少演示延迟并保持 JSON 输出稳定

DeepSeek API Key 必须由团队从官方平台申请并自行承担额度费用。当前仓库没有真实 Key，也没有用真实账户完成计费调用；自动测试使用本地模拟端点，只证明鉴权、请求格式、结构校验、回退和界面链路正确。

DeepSeek 路径当前只接入结构化 Chat，没有把不存在或未经确认的向量接口包装成 Embedding。页面中的语义检索会显示“不可用”，但不影响结构化事件、谓词和候选规则输出。若以后另接兼容 Embedding 服务，再配置 `ALPHALENS_DEEPSEEK_EMBEDDING_MODEL`。

## 四、可选服务端固定配置

界面单次 Key 是推荐演示方式。若部署方需要固定的通用 OpenAI 兼容服务，可以继续使用 `.env`：

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

## 五、本地模型配置

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

## 六、状态检查

```bash
.venv/bin/python app/server.py
curl http://127.0.0.1:8701/api/ai/status
```

`configured=true` 表示模型参数已经配置，不等于远端健康检查通过；完成一次分析后页面出现结构化结果才表示本次调用成功。状态接口只返回模型、模式和 Prompt 版本，不返回密钥。修改 `.env` 后需要重启 Demo。

浏览器在“分析引擎”中选择：

- `大模型候选 + 规则校验`：调用结构化 Chat；配置了 Embedding 模型时额外执行语义检索，失败时自动回退。
- `仅规则复现`：完全不调用模型，用于断网演示和结果复现。

## 七、页面怎样讲

1. 指出右侧“AI 研究层待凭证”，说明比赛 Key 由使用者在左侧单次填写，系统不保存。
2. 填写 Key，选择真实政策案例并开始分析。
3. 展示模型返回的结构化事件、JSON 输出和通过校验的谓词数量；DeepSeek 未配置 Embedding 时主动说明语义检索未启用。
4. 展示 AI 自动提出的候选规则，强调状态是“待统计验证”。
5. 展示 AI 与规则谓词一致数量；不一致结果只用于审计，不覆盖冻结规则。
6. 展示候选因子公式，说明因子仍由冻结规则和确定性程序计算。

## 八、安全边界

- 模型没有配置、超时或返回非法 JSON 时，Demo 自动使用确定性流程。
- 模型提出的股票必须经过 30 只股票池校验；只有原文明确点名的 AI 实体才可作为确定性链接失败时的回退，行业推断实体只保留为审计候选。
- 非原文证据会被标记并禁止作为事件回退依据。
- 候选规则不会直接写入正式 CSV，也不会直接参与因子评分。
- 新文本不会获得尚未发生的未来收益。
- 使用外部 API 前，应确认输入文本允许发送给对应服务。
- 远程部署必须启用 HTTPS；HTTP 只用于本机 `127.0.0.1` 演示。
- 后端不得打印请求体，错误信息不得包含 Authorization 请求头。

## 九、验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest -v tests/test_ai_research_layer.py
cd tests && ALPHALENS_PYTHON=../.venv/bin/python npm test
```

Python 和 Playwright 测试会启动本地模拟兼容服务，验证 Bearer 鉴权、`deepseek-v4-flash`、JSON Object、关闭思考模式、Key 不回显、输入框自动清空、非法股票拦截和候选规则状态，不产生真实 API 费用。

已连接状态的验收截图见 `查看材料/Demo大模型研究层截图.png`。截图使用本地模拟模型生成，只证明接口和页面链路，不代表真实模型质量评估已经完成。
