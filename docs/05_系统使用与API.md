# 系统使用与 API

## 用户输入与输出

用户在“政策文本分析”页输入标题、正文、发布机构、公开时间和来源链接，可选上传 `.txt`、`.md`、可检索 `.pdf` 文件或临时填写大模型密钥。扫描版 PDF 没有文字层时会明确提示先做 OCR。

系统输出文档编号、处理时间、大模型是否使用、结构化事件、14 个固定谓词、原文证据、六类因素分数、触发规则、基准概率、更新后概率和概率变化。

## 接口

### `GET /api/rates/status`

返回版本、数据日期、市场和文本行数、数据错误、来源审计、流动性代理名称和研究边界。

### `GET /api/rates/forecast?as_of=YYYY-MM-DD&horizon=5`

返回三类方向概率、预测标签、债券价格解释、收益率和流动性快照、六类因素和最近证据。首版只接受 `horizon=5`。

### `GET /api/rates/backtest`

返回四路线整体与分时期指标、混淆矩阵、校准、逐期预测、典型案例、区块 Bootstrap 和增量结论。

### 辅助查询与文件接口

- `GET /api/rates/evidence?limit=100`：读取原文到预测的审计链。
- `GET /api/rates/reviews`：读取追加式人工复核历史。
- `GET /api/rates/demo-cases`：读取不依赖临时联网的固定演示案例。
- `GET /api/rates/report`：下载当前 Markdown 投研报告。
- `POST /api/rates/extract-file`：提取不超过 10MB 的 TXT、Markdown 或可检索 PDF 正文；只提取，不自动加入历史样本。

### `POST /api/rates/analyze`

```json
{
  "title": "公开市场逆回购操作",
  "content": "中国人民银行开展逆回购操作，向市场投放流动性。",
  "source_name": "中国人民银行",
  "source_url": "https://www.pbc.gov.cn/",
  "publish_time": "2026-08-28T09:30:00",
  "api_key": "可选且不保存"
}
```

缺少必填字段、时间格式错误或来源链接无效时返回 HTTP 400。

### `POST /api/rates/review`

```json
{
  "document_id": "live-xxxxxxxxxxxx",
  "decision": "approved",
  "comment": "证据与原文一致"
}
```

`decision` 只支持 `approved`、`rejected`、`needs_revision`。记录只追加，不覆盖历史复核。

## 失败语义

数据不足时仍返回结构化 JSON，但 `status` 为 `research_evidence_insufficient`，不得将均匀概率解释为模型预测。未知 API 返回 404；接口响应均设置 `Cache-Control: no-store`。
