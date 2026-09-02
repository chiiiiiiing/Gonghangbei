# AlphaLens：银行利率债文本因子研究平台

AlphaLens利用大模型将央行及宏观政策文本转化为可量化、可验证、可追溯的金融因子，融合债券与银行间市场数据，辅助银行研判未来5个交易日的利率债市场方向。

## 首版研究契约

- 主目标：10年期国债收益率未来5个交易日的 `上行 / 震荡 / 下行`。
- 震荡定义：未来5日变化处于 `[-2bp, +2bp]`。
- 辅助指标：DR007流动性状态。公开历史MVP使用中国货币网FDR007定盘利率做代理，页面和接口均明确标注，不将其冒充原始DR007。
- 文本因素：货币政策、市场流动性、经济增长、通胀、债券供给、风险偏好。
- 模型比较：市场数据基线、仅文本、市场文本融合、融合加规则。
- 研究边界：不自动下单；单篇文本输出的是对现有预测的边际影响。

## 快速启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python 启动演示.py
```

浏览器打开终端显示的本地地址。默认首页包含五个页面：每日总览、政策文本分析、预测详情、证据审计、历史回测。

可选的大模型配置：

```bash
export DEEPSEEK_API_KEY="你的Key"
export ALPHALENS_AI_MODE="api"
```

也可以仅在“政策文本分析”页面临时输入Key。Key只用于当次请求，不写入文件。未配置模型时，系统明确显示“确定性降级”，不会把关键词结果包装成LLM结果。

## 更新官方样例数据

```bash
.venv/bin/python scripts/fetch_rates_market_data.py
.venv/bin/python scripts/fetch_rates_policy_texts.py
```

第一条命令下载中债年度国债收益率曲线、中国货币网FDR007历史定盘数据和当日DR007公开行情，生成：

- `data/sample/rates_market.csv`
- `data/sample/rates_source_audit.json`

第二条命令下载央行政策文本样例并记录页面SHA-256，生成 `data/sample/rates_policy_texts.csv`。
其中会尝试抓取财政部国债发行公告；该来源受站点限流影响时按可选来源记录，不会把空正文当作有效证据。

若需要使用真实LLM为历史文本生成可恢复缓存（正文哈希匹配，证据门控后才进入量化层）：

```bash
.venv/bin/python scripts/annotate_rates_policy_texts.py --workers 3
```

每日刷新（可跳过网络抓取，适合服务器定时任务）：

```bash
.venv/bin/python scripts/run_daily_rates_research.py --annotate-llm
```

## API

- `GET /api/rates/status`
- `GET /api/rates/forecast?as_of=YYYY-MM-DD&horizon=5`
- `GET /api/rates/backtest`
- `POST /api/rates/analyze`
- `POST /api/rates/extract-file`（TXT / Markdown / 可检索PDF）
- `POST /api/rates/review`
- `GET /api/rates/evidence`
- `GET /api/rates/demo-cases`
- `GET /api/rates/report`

`POST /api/rates/analyze` 示例：

```json
{
  "title": "公开市场逆回购操作",
  "content": "中国人民银行开展逆回购操作，向市场投放流动性。",
  "source_name": "中国人民银行",
  "source_url": "https://www.pbc.gov.cn/",
  "publish_time": "2026-08-28T09:30:00",
  "api_key": "可选"
}
```

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

利率版测试覆盖交易日映射、收盘后信息归属、标签阈值、证据原文、规则触发、LLM JSON规范化、滚动训练无未来泄漏、文件上传、全部新增API和人工复核追加写入。

## 人员2方案材料

位于 `docs/person2/`：

1. 项目定位与目标比较。
2. 六类因素字典与规则。
3. 技术流程与论文方法迁移。
4. 朱越腾老师咨询提纲。
5. 人员2交付与队友衔接。

## 旧版兼容

原碳酸锂研究模块、数据和 `/api/lithium/*` 接口仍保留，便于追溯；新版默认首页与主导航不再展示旧项目。原月度宏观实验接口也保持不变。

## 官方来源

- 中债收益率曲线：https://yield.chinabond.com.cn/cbweb-mn/pgxh/showHistory
- 中国货币网回购定盘利率：https://www.chinamoney.com.cn/chinese/bkfrr/
- 中国货币网质押式回购行情：https://www.chinamoney.com.cn/chinese/mkdatapm/?tab=2
- 中国人民银行公开市场业务：https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/index.html

本报告仅供研究参考，不构成投资建议
