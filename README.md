# AlphaLens 可运行演示成果

AlphaLens 将政策、公告、财经新闻和互动问答等非结构化文本转化为可解释、可回测、可复用的另类因子研究素材。系统是量化研究助手，不预测股票价格，不提供投资建议。

**本报告仅供研究参考，不构成投资建议**

## 启动

需要 Python 3.9 或更高版本。在当前目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python 启动演示.py
```

浏览器打开 [http://127.0.0.1:8701](http://127.0.0.1:8701)。

## 三分钟演示

1. 在“新文本分析”选择“储能政策”示例。
2. 保持“实时 AI 分析”，填写 DeepSeek API Key，点击“检查连接”。
3. 点击“开始分析”，依次查看事件、关联股票、AI/确定性谓词对照、门控、冻结规则和候选因子。
4. 切换“历史研究”，默认查看 OOS 指标和“证据不足”提示，再切换 Discovery。
5. 进入“研究审计”，说明来源、独立文档支持、未来函数和 `adj_factor=1` 限制。

断网时可选“冻结回放”。回放使用已保存的结构化 AI 输出，页面会持续显示“冻结回放”，不会冒充实时 API 结果。

## 关键保证

- 实时模式缺少 Key、API 失败或模型未返回完整 19 个谓词时直接报错。
- 系统先后执行事件、实体关系和逐股票谓词三层门控；只有 `agreed_true` 谓词可以触发冻结规则。
- API 检查同时核对 `/models` 权限和聊天接口实际返回的模型名；返回 Pro 或其他模型时拒绝结果。
- Discovery 与 OOS 分开展示；当前 OOS 有效日期过少，页面明确判定为“证据不足”。
- 规则支持度按独立 `doc_id`、日期和股票覆盖共同判定，一份政策映射多只股票仍只计一份文档。
- 回测使用行业等权超额收益，按交易日做横截面五组分组和 Rank IC。
- API Key 只随当次请求发送，不写入文件、CSV 或浏览器存储。

## 安全重算

需要重建实体、事件、谓词、规则和回测结果时，只运行：

```bash
.venv/bin/python 运行研究流水线.py
```

该入口不生成 `raw_documents.csv`，并在重算前后强制比对 SHA-256，防止覆盖人工核验成果。

## 可选能力

本地 BGE 语义检索：

```bash
.venv/bin/pip install -r requirements-ai.txt
ALPHALENS_BGE_ALLOW_DOWNLOAD=1 .venv/bin/python 准备本地向量模型.py
```

未安装或未缓存 BGE 时，页面如实标记并降级到确定性字符 n-gram 检索。

历史 DeepSeek 标注缓存：

```bash
DEEPSEEK_API_KEY=你的Key .venv/bin/python 批量生成AI标注.py --limit 10
```

真实文本和行情默认只写入 `data/external/` 暂存区；必须添加 `--apply` 才会替换输入：

```bash
.venv/bin/python 导入真实文本.py 核验后的采集清单.csv
.venv/bin/python 导入真实文本.py 核验后的采集清单.csv --apply

.venv/bin/pip install -r requirements-data.txt
.venv/bin/python 更新真实行情.py --start 2024-01-01 --end 2026-06-30
.venv/bin/python 更新真实行情.py --start 2024-01-01 --end 2026-06-30 --apply
```

行情接口请求前复权价格；按已确认口径，`adj_factor=1` 保留为占位字段并在审计页披露。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app src tests 运行研究流水线.py
```

## 文件

```text
├── app/                    # Flask API、独立前端资源和本地依赖
├── src/                    # AI、文本抽取、数据导入、评分与回测源码
├── data/sample/            # 演示数据、回放和研究输出
├── tests/                  # 精简验收测试
├── 启动演示.py             # 统一启动入口
├── 运行研究流水线.py       # 保护原始文本的重算入口
├── 导入真实文本.py         # 默认暂存的真实文本导入入口
├── 更新真实行情.py         # 默认暂存的前复权行情入口
├── 批量生成AI标注.py       # 可恢复的历史模型标注缓存
├── 检查数据覆盖.py         # 分区×来源×事件类型缺口清单（返回码 0/1）
├── VERSION                 # 版本标签（无 git 目录时的审计页兜底）
├── 完整说明文档.md         # 唯一权威完整说明（架构、口径、API、限制）
├── 问题与待改进.md         # 问题清单与改进进度
├── 原创性证明.md
└── 精益画布.md
```

项目概览、系统架构、数据口径、API、第三方依赖与已知限制见 [完整说明文档.md](完整说明文档.md)。
