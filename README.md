# AlphaLens：银行利率债文本因子研究平台

AlphaLens 利用大语言模型将央行及宏观政策文本转化为可量化、可验证、可追溯的金融因子，融合债券与银行间市场数据，辅助银行研判未来 5 个交易日的利率债市场方向。

## 提交版要点

- 主目标：10 年期国债收益率未来 5 个交易日的 `上行 / 震荡 / 下行`。
- 标签口径：变化大于 `+2bp` 为上行，小于 `-2bp` 为下行，其余为震荡。
- 流动性输入：DR007；公开历史样例使用中国货币网 FDR007 定盘利率做代理并明确标识。
- 文本因素：货币政策、市场流动性、经济增长、通胀、债券供给、风险偏好。
- 输出：三类方向概率、主要驱动、原文证据、规则贡献和四路线滚动评估。
- 边界：不自动下单；单篇文本只输出对现有预测的边际影响。

## 一键启动

需要 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python 启动演示.py
```

浏览器打开 `http://127.0.0.1:8701/`。不配置大模型密钥也能完整演示确定性抽取、模型、回测和审计链路。

如需演示大模型抽取，可在页面临时填写密钥，或设置：

```bash
export DEEPSEEK_API_KEY="你的密钥"
export ALPHALENS_AI_MODE="api"
```

密钥只参与当次请求，不写入项目文件或浏览器存储。

## 当前真实数据快照

| 数据 | 范围/数量 | 来源 | 用途 |
| --- | --- | --- | --- |
| 10 年期国债收益率 | 2026-01-04 至 2026-08-28，共 163 个交集交易日 | 中债收益率曲线 | 主目标与市场特征 |
| FDR007 定盘利率 | 同期 163 个交集交易日 | 中国货币网 | DR007 历史公开代理 |
| 政策文本 | 1 篇可核验样例 | 中国人民银行 | 验证文本抽取与证据审计 |

每条市场数据保留来源网址、下载时间和源文件 SHA-256。当前文本覆盖不足，因此系统明确显示“文本预测增量尚未建立”；现有分数只证明流程可复现，不代表正式研究结论。

## 页面

1. 每日研究总览：五日方向概率、收益率、流动性状态和驱动因素。
2. 政策文本分析：粘贴或上传文本，查看结构化谓词及概率边际变化。
3. 预测详情：六类因素、市场口径和模型契约。
4. 证据审计：来源、原文片段、文件哈希和人工复核。
5. 历史回测：市场基线、仅文本、融合、融合加规则四条路线。

## API

- `GET /api/rates/status`
- `GET /api/rates/forecast?as_of=YYYY-MM-DD&horizon=5`
- `GET /api/rates/backtest`
- `POST /api/rates/analyze`
- `POST /api/rates/review`

详细字段见 `docs/05_系统使用与API.md`。

## 数据更新与测试

```bash
.venv/bin/python scripts/fetch_rates_market_data.py
.venv/bin/python scripts/fetch_rates_policy_texts.py
.venv/bin/python 运行利率研究.py
.venv/bin/python -m unittest discover -s tests -v
```

## 目录

```text
app/                 五页网页与 Flask API
src/rates/           时间对齐、因素、规则、模型和服务层
src/ai/gateway.py    可选的大模型网关
scripts/             官方数据获取脚本
data/sample/         带来源审计的真实样例快照
docs/                项目、技术、数据、评估、答辩和分工材料
tests/               自动化验收测试
演示截图/            新版页面截图
```

完整说明见 `完整说明文档.md`，现场操作见 `现场演示操作文档.md`。

本系统仅供研究参考，不构成投资建议或自动交易指令。
