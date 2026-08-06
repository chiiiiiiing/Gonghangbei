const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");
const fixed = (value, digits = 4) => Number(value || 0).toFixed(digits);
const pct = (value, digits = 2) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const SOURCE_LABELS = { policy: "政策", announcement: "公告", news: "新闻", ir_qa: "互动问答" };
const EVENT_LABELS = {
  policy_support: "政策支持",
  capacity_expansion: "产能扩张",
  attention_spread: "关注扩散",
  regulatory_penalty: "监管处罚",
  inquiry_letter_pressure: "问询压力",
  earnings_quality_anomaly: "业绩质量异常",
  supply_chain_disruption: "供应链扰动",
  product_price_increase: "产品提价",
  investor_question_pressure: "投资者提问压力",
};
const STATUS_LABELS = {
  agreed_true: "一致为真",
  agreed_false: "一致为假",
  disputed: "判断冲突",
  invalid: "无效",
};
const EXAMPLES = [
  {
    title: "关于印发《新型储能规模化建设专项行动方案（2025—2027年）》的通知",
    content: "原文摘要：为推动新型储能高质量发展，国家发展改革委、国家能源局研究制定了《新型储能规模化建设专项行动方案（2025—2027年）》。现予印发，请结合实际认真抓好贯彻落实。",
    type: "policy", name: "中国政府网", date: "2025-08-27",
    url: "https://www.gov.cn/zhengce/zhengceku/202509/content_7040296.htm",
  },
  {
    title: "上海璞泰来新能源科技集团股份有限公司关于投资建设年产72亿平方米锂离子电池隔膜建设项目的公告",
    content: "原文摘要：璞泰来披露年产72亿平方米锂离子电池隔膜建设项目，计划总投资56亿元人民币。重要内容提示：交易实施尚需履行审批及其他相关程序。",
    type: "announcement", name: "巨潮资讯网", date: "2026-05-21",
    url: "http://static.cninfo.com.cn/finalpage/2026-05-21/1225319446.PDF",
  },
  {
    title: "315GW+119GW！2025年光伏、风电年新增装机再创新高",
    content: "原文摘要：国家能源局发布2025年全国电力统计数据，光伏、风电年新增装机规模再创新高，行业装机量受到市场关注。",
    type: "news", name: "腾讯新闻", date: "2026-01-28",
    url: "https://news.qq.com/rain/a/20260128A043VK00",
  },
];

let runMode = "live";
let currentAnalysis = null;
let historyData = null;
let auditData = null;
let historySplit = "oos";

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

function setExample(index) {
  const item = EXAMPLES[index];
  $("title").value = item.title;
  $("content").value = item.content;
  $("sourceType").value = item.type;
  $("sourceName").value = item.name;
  $("eventDate").value = item.date;
  $("sourceUrl").value = item.url;
}

function setConnection(kind, message) {
  const node = $("connectionStatus");
  node.className = `connection-state ${kind}`;
  node.innerHTML = `<span class="mini-dot"></span><span>${esc(message)}</span>`;
}

function setRunMode(mode) {
  runMode = mode;
  document.querySelectorAll("[data-run-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.runMode === mode);
  });
  const replay = mode === "replay";
  $("apiKeyField").hidden = replay;
  $("checkApiButton").hidden = replay;
  $("runButton").innerHTML = replay
    ? '<i data-lucide="archive-restore"></i>载入冻结回放'
    : '<i data-lucide="play"></i>开始分析';
  setConnection(replay ? "neutral" : "neutral", replay ? "断网备用案例" : "等待凭证");
  refreshIcons();
}

function switchView(viewId) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  document.querySelectorAll(".nav-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === viewId));
  if (viewId === "historyView" && historyData) {
    renderHistory(historyData);
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({ error: "服务返回了无法解析的响应" }));
  if (!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}

async function loadStatus() {
  try {
    const data = await fetchJson("/api/status");
    $("statusDot").classList.add("on");
    const ai = data.ai || {};
    const model = ai.chat_model || "未配置模型";
    const aiState = ai.configured ? "模型已就绪" : "模型待配置";
    $("statusText").textContent = `本地研究服务已连接 · ${model} ${aiState} · 规则库版本 ${data.rule_version}`;
  } catch (error) {
    $("statusText").textContent = `本地研究服务未连接 · ${error.message}`;
  }
}

async function loadHistory() {
  try {
    historyData = await fetchJson("/api/backtest");
    renderHistory(historyData);
  } catch (error) {
    $("historyContent").innerHTML = `<div class="error-box"><strong>历史研究读取失败</strong>${esc(error.message)}</div>`;
  }
}

async function loadAudit() {
  try {
    auditData = await fetchJson("/api/audit");
    renderAudit(auditData);
  } catch (error) {
    $("auditContent").innerHTML = `<div class="error-box"><strong>研究审计读取失败</strong>${esc(error.message)}</div>`;
  }
}

async function checkApi() {
  const apiKey = $("apiKey").value.trim();
  if (!apiKey) {
    setConnection("fail", "请先填写 API Key");
    return;
  }
  $("checkApiButton").disabled = true;
  setConnection("neutral", "正在核对模型权限与实际返回模型");
  try {
    const data = await fetchJson("/api/ai/check", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey }),
    });
    setConnection("ok", `${data.returned_model} 已验证 · ${data.owned_by || "DeepSeek"}`);
  } catch (error) {
    setConnection("fail", error.message);
  } finally {
    $("checkApiButton").disabled = false;
  }
}

function analysisPayload() {
  return {
    title: $("title").value.trim(),
    content: $("content").value.trim(),
    source_type: $("sourceType").value,
    source_name: $("sourceName").value.trim(),
    event_date: $("eventDate").value,
    source_url: $("sourceUrl").value.trim(),
    analysis_mode: "hybrid",
    api_key: $("apiKey").value.trim(),
  };
}

async function runAnalysis() {
  const button = $("runButton");
  button.disabled = true;
  $("runState").classList.add("on");
  $("result").innerHTML = "";
  try {
    const data = runMode === "replay"
      ? await fetchJson("/api/replay/storage-policy")
      : await fetchJson("/api/analyze", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(analysisPayload()),
        });
    currentAnalysis = data;
    renderAnalysis(data);
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("result").innerHTML = `<div class="error-box"><strong>分析未完成</strong>${esc(error.message)}</div>`;
  } finally {
    button.disabled = false;
    $("runState").classList.remove("on");
  }
}

function metric(value, label) {
  return `<div class="metric-cell"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
}

function badge(label, kind) {
  return `<span class="badge ${kind}">${esc(label)}</span>`;
}

function renderAnalysis(data) {
  const stocks = data.stock_results || [];
  const top = stocks[0];
  const gateKind = data.consensus_gate_passed ? "good" : "warn";
  const eventName = EVENT_LABELS[data.event_type] || data.event_type || "未识别";
  const aiSummary = data.ai_analysis?.result?.summary || "已完成结构化事件、实体和谓词校验";
  const replayFlag = data.is_replay ? '<span class="replay-flag">冻结回放</span>' : "";
  const oos = data.historical_backtest?.splits?.oos?.metrics || {};
  const steps = [
    ["结构化事件", eventName],
    ["关联股票", `${stocks.length} 只通过股票池校验`],
    ["谓词对照", `${top?.predicate_consensus?.length || 0} 项逐股票对照`],
    ["一致性门控", data.consensus_gate_passed ? "全部通过" : `${data.disputed_predicates?.length || 0} 项排除`],
    ["冻结规则", `${data.triggered_rules?.length || 0} 条触发`],
    ["候选因子", top ? fixed(top.candidate_factor) : "0.0000"],
  ];
  let html = `<section class="result-summary">
    <div class="result-lead">${replayFlag}<h2>${esc(eventName)}</h2><p>${esc(aiSummary)}</p></div>
    ${metric(top ? `${top.name} · ${top.code}` : "无", "首位关联股票")}
    ${metric(top ? fixed(top.candidate_factor) : "0.0000", "候选因子值")}
    <div class="metric-cell"><strong>${badge(data.consensus_gate_passed ? "门控通过" : "部分排除", gateKind)}</strong><span>一致性状态</span></div>
  </section>`;
  html += `<section class="section"><div class="section-header"><div><h2>处理链路</h2><p>${esc(data.source_name)} · ${esc(data.event_time)}</p></div>${data.source_url ? `<a class="download-button" href="${esc(data.source_url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>查看原文</a>` : ""}</div><div class="pipeline">${steps.map((step, index) => `<div class="pipeline-step"><span class="step-number">${index + 1}</span><b>${esc(step[0])}</b><span>${esc(step[1])}</span></div>`).join("")}</div></section>`;
  if (data.disputed_predicates?.length) {
    html += `<div class="notice error">已排除：${esc(data.disputed_predicates.join("、"))}。争议或非法谓词不会进入规则匹配和因子计算。</div>`;
  }
  html += `<section class="section"><div class="section-header"><div><h2>候选因子与研究证据</h2></div><span>${badge(oos.evidence_status === "sufficient" ? "OOS 证据达标" : "OOS 证据不足", oos.evidence_status === "sufficient" ? "good" : "warn")}</span></div><div class="section-body"><div class="stock-selector">${stocks.map((stock, index) => `<button class="stock-button ${index === 0 ? "active" : ""}" data-stock-index="${index}" type="button">${esc(stock.name)} · ${esc(stock.code)}</button>`).join("") || "未形成股票候选"}</div><div id="stockDetail" class="stock-detail"></div></div></section>`;
  html += renderAICandidates(data.ai_analysis);
  html += `<section class="section"><div class="section-header"><div><h2>历史样本外参考</h2><p>固定历史样本，不是对本次文本的单次收益预测</p></div><button class="download-button" id="openHistory" type="button"><i data-lucide="chart-no-axes-combined"></i>查看历史研究</button></div><div class="section-body"><div class="detail-grid"><div class="detail-item"><b>OOS Rank IC</b><span class="mono">${fixed(oos.avg_rank_ic_5d, 6)}</span></div><div class="detail-item"><b>OOS ICIR</b><span class="mono">${fixed(oos.rank_ic_ir, 6)}</span></div><div class="detail-item"><b>有效 IC 日</b><span class="mono">${esc(oos.rank_ic_valid_date_count ?? 0)}</span></div><div class="detail-item"><b>样本判定</b><span>${oos.evidence_status === "sufficient" ? "证据达标" : "证据不足"}</span></div></div></div></section>`;
  html += `<section class="section"><div class="section-header"><div><h2>自动研究记录</h2></div><button class="download-button" id="downloadReport" type="button"><i data-lucide="download"></i>下载 Markdown</button></div></section>`;
  $("result").innerHTML = html;
  document.querySelectorAll("[data-stock-index]").forEach((button) => button.addEventListener("click", () => renderStockDetail(Number(button.dataset.stockIndex))));
  $("openHistory").addEventListener("click", () => switchView("historyView"));
  $("downloadReport").addEventListener("click", () => downloadReport(data.report || ""));
  if (top) renderStockDetail(0);
  refreshIcons();
}

function renderStockDetail(index) {
  const stock = currentAnalysis.stock_results[index];
  const formula = stock.factor_formula;
  const score = stock.evidence_score_breakdown || {};
  const components = score.final_components || {};
  document.querySelectorAll("[data-stock-index]").forEach((button, current) => button.classList.toggle("active", current === index));
  const consensusRows = (stock.predicate_consensus || []).map((row) => `<tr>
    <td class="mono">${esc(row.name)}</td><td class="mono">${esc(row.ai_value)}</td><td class="mono">${esc(row.rule_value)}</td>
    <td class="status-${esc(row.status)}">${esc(STATUS_LABELS[row.status] || row.status)}</td><td>${row.accepted_for_rule ? "允许触发" : "不进入规则"}</td>
  </tr>`).join("");
  const ruleRows = (stock.triggered_rules || []).map((rule) => `<tr><td class="mono">${esc(rule.id)}</td><td class="mono">${esc(rule.condition)}</td><td>${esc(rule.target_label)}</td><td>${esc(rule.support)}</td><td>${pct(rule.win_rate)}</td><td class="mono">${fixed(rule.score)}</td></tr>`).join("") || '<tr><td colspan="6">没有冻结规则通过全部门控</td></tr>';
  const entityGate = stock.entity_consensus?.accepted ? badge("关系通过", "good") : badge("关系未通过", "bad");
  $("stockDetail").innerHTML = `
    <div class="detail-grid"><div class="detail-item"><b>主体</b><span>${esc(stock.event.subject)}</span></div><div class="detail-item"><b>客体</b><span>${esc(stock.event.object)}</span></div><div class="detail-item"><b>关系门控</b><span>${entityGate}<br>${esc(stock.link_evidence)}</span></div><div class="detail-item"><b>原文证据</b><span>${esc(stock.event.evidence_text)}</span></div></div>
    <div class="formula-band"><div class="formula-item"><strong>${fixed(formula.rule_score_sum)}</strong><span>冻结规则评分和</span></div><div class="formula-item"><strong>${fixed(formula.evidence_strength, 2)}</strong><span>透明证据分</span></div><div class="formula-item"><strong>${fixed(formula.impact_prior, 2)}</strong><span>Discovery 影响后验</span></div><div class="formula-item"><strong>${fixed(formula.result)}</strong><span>候选因子</span></div></div>
    <div class="equation">${fixed(formula.rule_score_sum)} × (${formula.evidence_weight} × ${fixed(formula.evidence_strength, 2)} + ${formula.impact_weight} × ${fixed(formula.impact_prior, 2)}) = ${fixed(formula.result)}</div>
    <div class="score-grid"><div class="score-part"><strong>${fixed(components.source_reliability, 2)}</strong><span>来源可靠性 · 30%</span></div><div class="score-part"><strong>${fixed(components.evidence_grounding, 2)}</strong><span>证据回溯 · 25%</span></div><div class="score-part"><strong>${fixed(components.information_specificity, 2)}</strong><span>信息具体性 · 25%</span></div><div class="score-part"><strong>${fixed(components.business_relevance, 2)}</strong><span>业务关联 · 20%</span></div></div>
    <details class="disclosure" open><summary>AI 与确定性谓词对照（${stock.predicate_consensus?.length || 0} 项）</summary><div class="table-wrap"><table><thead><tr><th>谓词</th><th>AI</th><th>确定性程序</th><th>一致性</th><th>门控</th></tr></thead><tbody>${consensusRows}</tbody></table></div></details>
    <details class="disclosure"><summary>冻结规则匹配（${stock.triggered_rules?.length || 0} 条）</summary><div class="table-wrap"><table><thead><tr><th>规则</th><th>条件</th><th>标签</th><th>独立文档</th><th>后验参考胜率</th><th>评分</th></tr></thead><tbody>${ruleRows}</tbody></table></div></details>`;
}

function renderAICandidates(ai) {
  if (!ai?.used) return "";
  const result = ai.result || {};
  const retrieval = ai.embedding_retrieval || {};
  const candidates = result.candidate_rules || [];
  const rows = candidates.map((rule) => `<tr><td>${esc(rule.name)}</td><td class="mono">${esc(rule.conditions.join(" AND "))}</td><td>${esc(rule.target_label)}</td><td>${badge("待统计验证", "warn")}</td></tr>`).join("") || '<tr><td colspan="4">本次未提出新规则</td></tr>';
  return `<section class="section"><div class="section-header"><div><h2>AI 候选研究</h2><p>语义检索：${esc(retrieval.model || "未记录")}${retrieval.fallback ? "（降级）" : ""}</p></div>${badge(ai.repair_attempted ? "修复后通过" : "结构校验通过", "info")}</div><div class="section-body"><div class="notice good">${esc(result.summary || "模型已返回结构化研究候选")}</div><details class="disclosure"><summary>查看 AI 提议规则（${candidates.length} 条）</summary><div class="table-wrap"><table><thead><tr><th>候选规则</th><th>谓词条件</th><th>标签</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div></details></div></section>`;
}

function renderHistory(history) {
  const split = history.splits[historySplit];
  const metrics = split.metrics;
  const isOos = historySplit === "oos";
  const evidenceOk = metrics.evidence_status === "sufficient";
  const splitLabel = isOos ? "OOS · 2026H1" : "Discovery · 2024—2025";
  const rules = history.qualified_rules || [];
  let html = `<div class="page-heading"><div><h1>历史研究</h1><p>Discovery 发现规则；OOS 独立检验，不混用指标</p></div><div class="split-control"><button class="split-button ${isOos ? "active" : ""}" data-split="oos" type="button">OOS 样本外</button><button class="split-button ${!isOos ? "active" : ""}" data-split="discovery" type="button">Discovery</button></div></div>`;
  html += `<div class="metrics">${metric(fixed(metrics.avg_rank_ic_5d, 6), `${splitLabel} Rank IC`)}${metric(fixed(metrics.rank_ic_ir, 6), `${splitLabel} ICIR`)}${metric(pct(metrics.factor_coverage_rate), "因子覆盖率")}${metric(metrics.rank_ic_valid_date_count, "有效 IC 日")}${metric(metrics.active_factor_date_count, "因子活跃日")}${metric(pct(metrics.top_bottom_group_spread_5d), "G5-G1 行业超额")}</div>`;
  html += `<div class="notice ${evidenceOk ? "good" : ""}"><strong>${evidenceOk ? "证据达到展示门槛" : "证据不足"}</strong> · 当前有效日期 ${esc(metrics.rank_ic_valid_date_count)} 个，${evidenceOk ? "可进入进一步稳健性检验" : "不能宣称因子稳定有效"}。收益为股票 5 日收益减行业等权收益。</div>`;
  html += `<section class="section"><div class="section-header"><div><h2>${esc(splitLabel)} 回测诊断</h2><p>五组按每日横截面分组；Rank IC 使用行业中性排序</p></div>${badge(evidenceOk ? "证据达标" : "证据不足", evidenceOk ? "good" : "warn")}</div><div class="section-body"><div class="charts"><div class="chart" id="groupChart"></div><div class="chart" id="icChart"></div></div></div></section>`;
  html += `<section class="section"><div class="section-header"><div><h2>合格冻结规则</h2><p>分数由后验胜率、收缩收益、半年稳定性、覆盖和证据组成</p></div></div><div class="table-wrap"><table><thead><tr><th>规则</th><th>条件</th><th>独立文档</th><th>独立日期</th><th>OOS 文档</th><th>OOS 超额</th><th>评分</th></tr></thead><tbody>${rules.map((rule) => `<tr><td class="mono">${esc(rule.rule_id)}</td><td class="mono">${esc(rule.condition)}</td><td>${rule.independent_document_count}</td><td>${rule.independent_date_count}</td><td>${rule.oos_document_count}</td><td>${pct(rule.oos_avg_excess_return_5d)}</td><td class="mono">${fixed(rule.score)}</td></tr>`).join("")}</tbody></table></div></section>`;
  $("historyContent").innerHTML = html;
  document.querySelectorAll("[data-split]").forEach((button) => button.addEventListener("click", () => { historySplit = button.dataset.split; renderHistory(history); }));
  if ($("historyView").classList.contains("active")) renderCharts(split);
}

function renderCharts(split) {
  if (!window.Plotly || !$("groupChart") || !$("icChart")) return;
  const layout = { font: { family: "Inter, sans-serif", size: 11, color: "#40505e" }, margin: { l: 52, r: 16, t: 42, b: 42 }, paper_bgcolor: "#fff", plot_bgcolor: "#f8fafb", showlegend: false };
  const groupValues = split.group_returns.map((row) => row.avg_forward_return_5d);
  const groupsEmpty = groupValues.every((value) => Math.abs(value) < 1e-12);
  const emptyAnnotation = { text: "有效横截面不足", x: .5, y: .52, xref: "paper", yref: "paper", showarrow: false, font: { color: "#94600c", size: 12 } };
  Plotly.newPlot("groupChart", [{ x: split.group_returns.map((row) => row.group), y: groupValues, type: "bar", marker: { color: ["#bb624c", "#a7bac7", "#819fb3", "#4386ae", "#127760"] } }], { ...layout, title: "每日五组平均行业超额收益", yaxis: { tickformat: ".1%", range: groupsEmpty ? [-.01, .01] : undefined }, annotations: groupsEmpty ? [emptyAnnotation] : [] }, { responsive: true, displayModeBar: false });
  const values = split.rank_ic_timeseries.map((row) => row.rank_ic_5d);
  Plotly.newPlot("icChart", [{ x: split.rank_ic_timeseries.map((row) => row.trade_date), y: values, type: "scatter", mode: "lines+markers", line: { color: "#116fae", width: 1.5 }, marker: { size: 5 } }], { ...layout, title: "Rank IC 时间序列", yaxis: { zeroline: true, zerolinecolor: "#8d9ba6", range: values.length ? undefined : [-.05, .05] }, annotations: values.length ? [] : [emptyAnnotation] }, { responsive: true, displayModeBar: false });
}

function distribution(rows, labels = {}) {
  return `<div class="distribution">${Object.entries(rows).map(([name, value]) => `<div class="distribution-row"><span>${esc(labels[name] || name)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>`;
}

function renderAudit(audit) {
  const countLabels = { stocks: "股票池", documents: "原始文本", events: "结构化事件", predicates: "谓词记录", qualified_rules: "合格规则", market_rows: "行情行" };
  const coverageRows = [];
  for (const split of ["discovery", "oos"]) {
    for (const sourceType of ["policy", "announcement", "news", "ir_qa"]) {
      const item = audit.split_source_coverage?.[split]?.[sourceType] || { count: 0, target: 25, remaining: 25, status: "insufficient" };
      coverageRows.push(`<tr><td>${split === "discovery" ? "Discovery" : "OOS"}</td><td>${SOURCE_LABELS[sourceType]}</td><td>${item.count}</td><td>${item.target}</td><td>${item.remaining}</td><td>${badge(item.status === "met" ? "达标" : "待补充", item.status === "met" ? "good" : "warn")}</td></tr>`);
    }
  }
  const diagnostics = audit.rule_diagnostics || [];
  let html = `<div class="page-heading"><div><h1>研究审计</h1><p>数据覆盖、模型、评分、规则支持与时间边界</p></div></div>`;
  html += `<div class="audit-counts">${Object.entries(countLabels).map(([key, label]) => `<div class="audit-count"><strong>${esc(audit.counts[key])}</strong><span>${esc(label)}</span></div>`).join("")}</div>`;
  html += `<section class="section"><div class="section-header"><div><h2>样本分区覆盖</h2><p>目标为每种来源、每个研究分区至少 25 篇独立文档</p></div></div><div class="table-wrap"><table><thead><tr><th>分区</th><th>来源</th><th>当前</th><th>目标</th><th>待补</th><th>状态</th></tr></thead><tbody>${coverageRows.join("")}</tbody></table></div></section>`;
  html += `<section class="section"><div class="section-header"><div><h2>来源与事件分布</h2></div></div><div class="section-body"><div class="audit-columns"><div>${distribution(audit.source_type_counts, SOURCE_LABELS)}</div><div>${distribution(audit.event_type_counts, EVENT_LABELS)}</div></div></div></section>`;
  const cache = audit.ai_annotation_cache || {};
  html += `<section class="section"><div class="section-header"><div><h2>数据与版本状态</h2></div></div><div class="section-body"><div class="status-grid">
    <div class="status-card"><b>URL 自动核验</b><span>${audit.source_verification.automated_pass_count} 条 · ${esc(audit.source_verification.status)}</span></div>
    <div class="status-card"><b>事件人工抽检</b><span>${audit.event_review.reviewed_count} 条 · ${esc(audit.event_review.status)}</span></div>
    <div class="status-card"><b>谓词人工抽检</b><span>${audit.predicate_review.reviewed_count} 条 · ${esc(audit.predicate_review.status)}</span></div>
    <div class="status-card"><b>历史 AI 缓存</b><span>${cache.success_count || 0}/${cache.document_count || 0} · ${cache.status === "complete" ? "完成" : "待生成"}</span></div>
    <div class="status-card"><b>行情覆盖</b><span>${esc(audit.market.start)} 至 ${esc(audit.market.end)}</span></div>
    <div class="status-card"><b>复权限制</b><span>${audit.market.adj_factor_placeholder ? "前复权价格；adj_factor=1 占位" : "复权因子已导入"}</span></div>
    <div class="status-card"><b>模型与 Prompt</b><span>${esc(audit.model.chat_model)} · ${esc(audit.model.prompt_version)}</span></div>
    <div class="status-card"><b>规则与评分</b><span>${esc(audit.model.rule_version)} · ${esc(audit.model.scoring_version)}</span></div>
    <div class="status-card"><b>代码版本</b><span class="mono">${esc(audit.model.repository_commit)}</span></div>
    <div class="status-card"><b>未来函数审计</b><span>${esc(audit.future_info_audit)} · 事件日早于收益入场日</span></div>
  </div></div></section>`;
  html += `<section class="section"><div class="section-header"><div><h2>规则透明评分</h2><p>行业政策映射多只股票仍只计一篇独立文档</p></div></div><div class="table-wrap"><table><thead><tr><th>规则</th><th>独立文档</th><th>股票覆盖</th><th>后验胜率</th><th>收缩收益</th><th>半年稳定性</th><th>覆盖项</th><th>证据项</th><th>复杂度惩罚</th></tr></thead><tbody>${diagnostics.map((row) => `<tr><td class="mono">${esc(row.rule_id)}</td><td>${esc(row.independent_document_count)}</td><td>${esc(row.stock_count)}</td><td>${pct(row.posterior_win_rate)}</td><td>${pct(row.shrunk_return)}</td><td>${pct(row.half_year_stability)}</td><td>${fixed(row.coverage_component)}</td><td>${fixed(row.evidence_component)}</td><td>${fixed(row.complexity_penalty)}</td></tr>`).join("")}</tbody></table></div></section>`;
  $("auditContent").innerHTML = html;
}

function downloadReport(text) {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `AlphaLens_研究记录_${new Date().toISOString().slice(0, 10)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-view]").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
  document.querySelectorAll("[data-run-mode]").forEach((button) => button.addEventListener("click", () => setRunMode(button.dataset.runMode)));
  document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => setExample(Number(button.dataset.example))));
  $("toggleKey").addEventListener("click", () => {
    $("apiKey").type = $("apiKey").type === "password" ? "text" : "password";
  });
  $("checkApiButton").addEventListener("click", checkApi);
  $("runButton").addEventListener("click", runAnalysis);
  refreshIcons();
  loadStatus();
  loadHistory();
  loadAudit();
});
