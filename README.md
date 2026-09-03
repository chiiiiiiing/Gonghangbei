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
| 10 年期国债收益率 | 2018-01-02 至 2026-09-01，共 2161 个交易日 | 中债收益率曲线 | 主目标与市场特征 |
| FDR007 定盘利率 | 同期 2161 个交易日 | 中国货币网 | DR007 历史公开代理 |
| 政策文本 | 2020-01-02 至 2026-08-31，共 374 篇去重文本 | 中国人民银行 | 事件、谓词、六类文本因子 |
| LLM 结构化缓存 | 374 条，其中 339 条通过模型抽取，35 条透明降级 | DeepSeek + 原文证据门控 | 可恢复语义抽取与审计 |

每条数据保留来源网址、下载时间和 SHA-256。四条路线共完成 1899 个滚动预测观测；规则增强路线相对市场基线的冻结 OOS 准确率差为 `+0.735` 个百分点，但 20 日移动区块 Bootstrap 95% 区间覆盖零，因此当前结论仍为“冻结 OOS 文本预测增量尚未建立”。

## 页面

1. 每日研究总览：五日方向概率、收益率、流动性状态和驱动因素。
2. 政策文本分析：粘贴或上传文本，查看结构化谓词及概率边际变化。
3. 预测详情：六类因素、特征贡献、冻结规则和模型契约。
4. 证据审计：来源、原文、事件、谓词、规则、文件哈希和追加式人工复核。
5. 历史回测：四条路线、分时期结果、概率校准和典型正确/错误案例。

## API

- `GET /api/rates/status`
- `GET /api/rates/forecast?as_of=YYYY-MM-DD&horizon=5`
- `GET /api/rates/backtest`
- `GET /api/rates/evidence`
- `GET /api/rates/reviews`
- `GET /api/rates/demo-cases`
- `GET /api/rates/report`
- `POST /api/rates/extract-file`
- `POST /api/rates/analyze`
- `POST /api/rates/review`

详细字段见 `docs/05_系统使用与API.md`。

## 数据更新与测试

```bash
.venv/bin/python scripts/fetch_rates_market_data.py
.venv/bin/python scripts/fetch_rates_policy_texts.py
.venv/bin/python scripts/annotate_rates_policy_texts.py --workers 3
.venv/bin/python scripts/run_daily_rates_research.py --annotate-llm
.venv/bin/python -m unittest discover -s tests -v
```

服务器无法联网但需要从当前冻结快照重建研究产物时，运行：

```bash
.venv/bin/python scripts/run_daily_rates_research.py --skip-market --skip-text
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
