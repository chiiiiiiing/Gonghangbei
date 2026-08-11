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
let EXAMPLES = [
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
let auditLoadError = "";
let macroData = null;
let historySplit = "oos";

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

function setExample(index) {
  const item = EXAMPLES[index];
  if (!item) return;
  $("title").value = item.title;
  $("content").value = item.content;
  $("sourceType").value = item.type;
  $("sourceName").value = item.name;
  $("eventDate").value = item.date;
  $("sourceUrl").value = item.url;
  // 异步抓取链接全文填入正文（保留摘要前缀，保证冻结回放校验不破坏；失败回退摘要）。
  fetchJson(`/api/example/${index}/fulltext`)
    .then((data) => {
      if (data.full_text && data.full_text !== item.content) {
        $("content").value = `${item.content}\n\n【正文链接全文】\n${data.full_text}`;
      }
    })
    .catch(() => {});
}

async function loadExamples() {
  try {
    const data = await fetchJson("/api/examples");
    if (data.examples?.length) EXAMPLES = data.examples;
  } catch (error) {
    // 离线时使用 app.js 内置兜底示例（仅摘要）。
  }
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
    $("statusText").textContent = `行业景气研究服务已连接 · ${model} ${aiState} · ${data.macro?.version || "v2"}`;
  } catch (error) {
    $("statusText").textContent = `本地研究服务未连接 · ${error.message}`;
  }
}

async function loadMacro() {
  try {
    const [status, forecast, backtest] = await Promise.all([
      fetchJson("/api/macro/status"), fetchJson("/api/macro/forecast"), fetchJson("/api/macro/backtest"),
    ]);
    macroData = { status, forecast, backtest };
    renderMacro(macroData);
  } catch (error) {
    $("macroContent").innerHTML = `<div class="error-box"><strong>宏观研究读取失败</strong>${esc(error.message)}</div>`;
  }
}

function renderMacro(data) {
  const latest = data.forecast.latest || {};
  const singleModel = data.status.single_text_model || {};
  const insufficient = singleModel.text_increment_status !== "validated_positive";
  const history = (data.forecast.history || []).filter((row) => row.split === "oos");
  const html = `<div class="macro-hero">
    <div><span class="eyebrow">AlphaLens · RESEARCH VALIDATION</span><h1>研究验证</h1><p>模型表现与完整研究审计合并展示：先核对官方历史目标、训练/验证划分和基线误差，再检查数据覆盖、AI标注、冻结规则与时间边界。实际预测入口位于“新文本预测”首页。</p></div>
    <button class="primary-button macro-action" id="openLiveAnalysis" type="button"><i data-lucide="sparkles"></i>返回新文本预测</button>
  </div>
  <div class="macro-kpis">
    ${metric(data.status.verified_historical_texts || 0, "可核验历史文本")}
    ${metric(singleModel.training_document_count || 0, "2015—2021 训练文本")}
    ${metric(singleModel.validation_document_count || 0, "2022—2023 验证文本")}
    ${metric(`${fixed(singleModel.validation_mae, 2)}pct`, "单文本模型验证 MAE")}
  </div>
  <div class="notice ${insufficient ? "error" : "good"}"><strong>${insufficient ? "文本增量尚未建立" : "文本模型通过验证"}</strong>：${esc(data.status.evidence_warning || "模型已按验证集选择后冻结。新预测只读取用户本次输入的一篇文本。")}</div>
  <div class="macro-grid">
    <section class="section macro-panel"><div class="section-header"><div><h2>历史目标与基线研究</h2><p>只用于训练/验证说明 · 发布日防泄漏 · 1—2月保持合并观测</p></div>${badge(singleModel.model_name || "--", "info")}</div><div id="macroForecastChart" class="macro-chart"></div></section>
    <section class="section macro-panel"><div class="section-header"><div><h2>单文本模型验证</h2><p>2022—2023冻结验证 · 与持久性基线同口径比较</p></div>${badge(insufficient ? "增量未建立" : "验证优于基线", insufficient ? "warn" : "good")}</div><div id="singleTextValidationChart" class="macro-chart"></div></section>
  </div>
  <section class="section"><div class="section-header"><div><h2>模型验收与适用边界</h2><p>新输入只读取一篇文本，历史库只训练参数</p></div></div><div class="section-body"><div class="acceptance-grid">
    <div class="acceptance-card"><span>单文本预测层</span><strong>${fixed(singleModel.validation_mae, 2)}pct</strong><p>验证 MAE；持久性基线 ${fixed(singleModel.persistence_validation_mae, 2)}pct。${insufficient ? "未优于基线，仍只作研究参考。" : "验证优于基线。"}</p></div>
    <div class="acceptance-card"><span>历史文本来源</span><strong>${esc(data.status.verified_historical_texts || 0)} 篇</strong><p>国家统计局 ${esc(singleModel.historical_source_type_counts?.news || 0)} 篇；国家能源局、国家发展改革委等政策 ${esc(singleModel.historical_source_type_counts?.policy || 0)} 篇。</p></div>
    <div class="acceptance-card warning-card"><span>输出边界</span><strong>同比预测报告</strong><p>规则与候选因子并列展示用于审计，不作为回归特征或股票收益预测；每次预测只读取本次输入。</p></div>
  </div></div></section>
  <section class="section"><div class="section-header"><div><h2>可审计研究边界</h2></div></div><div class="section-body"><div class="detail-grid">
    <div class="detail-item"><b>训练 / 规则发现</b><span>2015—2021</span></div><div class="detail-item"><b>模型 / 策略验证</b><span>2022—2023</span></div><div class="detail-item"><b>冻结 OOS</b><span>2024—最新</span></div><div class="detail-item"><b>交易代理边界</b><span>399808 仅作上市前研究代理，不冒充 ETF</span></div>
  </div><p class="disclaimer">本报告仅供研究参考，不构成投资建议</p></div></section>
  <div id="validationAuditContent"><div class="loading-surface validation-loading">正在读取完整研究审计</div></div>`;
  $("macroContent").innerHTML = html;
  $("openLiveAnalysis").addEventListener("click", () => switchView("liveView"));
  if (window.Plotly) {
    const actual = history.filter((row) => row.actual_yoy !== "");
    Plotly.newPlot("macroForecastChart", [
      { x: actual.map((row) => row.target_period_end), y: actual.map((row) => Number(row.actual_yoy)), name: "官方值", mode: "lines+markers", line: { color: "#15212b" } },
      { x: history.map((row) => row.target_period_end), y: history.map((row) => Number(row.predicted_yoy)), name: "冻结预测", mode: "lines", line: { color: "#116fae" } },
    ], chartLayout("同比增速（%）"), { responsive: true, displayModeBar: false });
    Plotly.newPlot("singleTextValidationChart", [{
      x: ["单文本 Ridge", "持久性基线"],
      y: [Number(singleModel.validation_mae || 0), Number(singleModel.persistence_validation_mae || 0)],
      type: "bar", marker: { color: ["#116fae", "#9aa8b3"] }, texttemplate: "%{y:.2f} pct", textposition: "outside",
    }], { ...chartLayout("MAE（百分点，越低越好）"), showlegend: false }, { responsive: true, displayModeBar: false });
  }
  if (auditData) renderAudit(auditData);
  if (auditLoadError && $("validationAuditContent")) {
    $("validationAuditContent").innerHTML = `<div class="error-box"><strong>研究审计读取失败</strong>${esc(auditLoadError)}</div>`;
  }
  refreshIcons();
}

function chartLayout(yTitle) {
  return { margin: { l: 48, r: 18, t: 20, b: 42 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", hovermode: "x unified", legend: { orientation: "h", y: 1.12 }, xaxis: { gridcolor: "#edf0f2" }, yaxis: { title: yTitle, gridcolor: "#edf0f2" }, font: { family: "Inter, sans-serif", size: 11, color: "#40505e" } };
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
    auditLoadError = "";
    renderAudit(auditData);
  } catch (error) {
    auditLoadError = error.message;
    if ($("validationAuditContent")) {
      $("validationAuditContent").innerHTML = `<div class="error-box"><strong>研究审计读取失败</strong>${esc(error.message)}</div>`;
    }
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
      : await fetchJson("/api/macro/analyze", {
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

function renderSourceAudit(data) {
  const audit = data.source_audit;
  if (!audit) return "";
  const statusLabel = {
    ok: "抓取成功",
    partial: "部分抓取",
    failed: "抓取失败",
    no_url: "无链接",
  }[audit.fetch_status] || audit.fetch_status;
  const statusKind = audit.fetch_status === "ok" ? "good" : audit.fetch_status === "partial" ? "warn" : audit.fetch_status === "failed" ? "error" : "info";
  const completenessLabel = { full: "全文已读", partial: "部分文本", summary_only: "仅摘要" }[audit.completeness] || audit.completeness;
  const calibrated = data.confidence_calibrated_count || 0;
  return `<section class="section"><div class="section-header"><div><h2>来源与完整性</h2><p>正文链接抓取与文本完整度 · 驱动 AI 置信度校准</p></div>${badge(statusLabel, statusKind)}</div>
  <div class="section-body"><div class="detail-grid">
    <div class="detail-item"><b>来源类型</b><span>${esc(audit.policy_type)} · ${esc(audit.source_name)}</span></div>
    <div class="detail-item"><b>链接类型</b><span>${esc(audit.link_type)} · ${esc(audit.authority)}</span></div>
    <div class="detail-item"><b>完整度</b><span>${completenessLabel}（摘要 ${esc(audit.summary_chars)} 字 / 抓取 ${esc(audit.fetched_chars)} 字）</span></div>
    <div class="detail-item"><b>置信度上限</b><span class="mono">${esc(audit.confidence_cap)}${calibrated ? ` · 已调低 ${calibrated} 项` : " · 无需下调"}</span></div>
  </div>
  <div class="notice ${statusKind}">${esc(audit.reason || "")}</div></div></section>`;
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
  html += renderSourceAudit(data);
  if (data.text_forecast) {
    const forecast = data.text_forecast;
    const contributionRows = (forecast.top_contributions || []).slice(0, 6).map((item) => `<tr><td class="mono">${esc(item.feature)}</td><td class="mono ${Number(item.contribution_pct_point) >= 0 ? "positive" : "negative"}">${Number(item.contribution_pct_point) >= 0 ? "+" : ""}${fixed(item.contribution_pct_point, 4)} pct</td></tr>`).join("");
    html += `<section class="section text-forecast"><div class="section-header"><div><h2>本次新文本的行业同比预测</h2><p>当期输入仅为这一篇文本；历史文本只用于冻结模型和规则参数</p></div>${badge(forecast.text_increment_status === "validated_positive" ? "验证优于基线" : "研究参考", forecast.text_increment_status === "validated_positive" ? "good" : "warn")}</div><div class="section-body">
      <div class="forecast-number"><span>${esc(forecast.target_name)}</span><strong>${fixed(forecast.predicted_yoy, 2)}%</strong><small>目标期 ${esc(forecast.target_period_end)} · 90%区间 ${fixed(forecast.lower_90, 2)}% — ${fixed(forecast.upper_90, 2)}%</small></div>
      <div class="detail-grid"><div class="detail-item"><b>最新已公布值</b><span class="mono">${fixed(forecast.latest_published_yoy, 2)}%</span></div><div class="detail-item"><b>预测加速度</b><span class="mono">${Number(forecast.predicted_acceleration) >= 0 ? "+" : ""}${fixed(forecast.predicted_acceleration, 2)} pct</span></div><div class="detail-item"><b>历史训练 / 验证</b><span class="mono">${esc(forecast.training_document_count)} / ${esc(forecast.validation_document_count)} 篇</span></div><div class="detail-item"><b>验证 MAE</b><span class="mono">${fixed(forecast.validation_mae, 2)} pct</span></div></div>
      <div class="notice ${forecast.text_increment_status === "validated_positive" ? "good" : "error"}">${esc(forecast.analysis_conclusion)} ${esc(forecast.forecast_basis)}</div>
      <details class="disclosure" open><summary>主要结构化特征贡献</summary><div class="table-wrap"><table><thead><tr><th>特征</th><th>预测贡献</th></tr></thead><tbody>${contributionRows}</tbody></table></div></details>
    </div></section>`;
  }
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
  const fusion = stock.predicate_fusion || {};
  document.querySelectorAll("[data-stock-index]").forEach((button, current) => button.classList.toggle("active", current === index));
  const consensusRows = (stock.predicate_consensus || []).map((row) => {
    const fused = fusion[row.name]?.fused;
    const triggers = typeof fused === "number" && fused >= 0.5;
    return `<tr>
    <td class="mono">${esc(row.name)}</td><td class="mono">${esc(row.ai_value)}</td><td class="mono">${esc(row.rule_value)}</td>
    <td class="mono">${typeof fused === "number" ? fixed(fused, 3) : "—"}</td>
    <td class="status-${esc(row.status)}">${esc(STATUS_LABELS[row.status] || row.status)}</td><td>${triggers ? "进入因子" : "不进入因子"}</td>
  </tr>`;
  }).join("");
  const ruleRows = (stock.triggered_rules || []).map((rule) => `<tr><td class="mono">${esc(rule.id)}</td><td class="mono">${esc(rule.condition)}</td><td>${esc(rule.target_label)}</td><td>${esc(rule.support)}</td><td>${pct(rule.win_rate)}</td><td class="mono">${fixed(rule.score)}</td></tr>`).join("") || '<tr><td colspan="6">没有冻结规则通过全部门控</td></tr>';
  const aiCandidateRows = (stock.ai_candidate_rules || []).map((rule) => `<tr><td class="mono">${esc(rule.name)}</td><td class="mono">${esc(rule.condition)}</td><td>${esc(rule.target_label)}</td><td>${pct(rule.confidence)}</td><td>${esc(rule.hit_ratio)}</td><td class="mono">${fixed(rule.ai_candidate_score)}</td></tr>`).join("") || "";
  const explainBlocks = (stock.rule_explainability || []).map((block) => {
    const preds = (block.predicates || []).map((p) => `<div class="predicate-line"><span class="mono">${esc(p.name)}</span><span>融合 ${fixed(p.fused, 2)} · ${esc(p.source)} · 置信 ${fixed(p.ai_confidence, 2)}</span><span class="muted">${esc(p.rationale || "")}</span></div>`).join("");
    const similar = (block.similar_to_frozen || []).map((s) => `${esc(s.rule_id)} ${fixed(s.similarity, 2)}`).join(" · ");
    return `<details class="disclosure"><summary>${block.source === "ai_candidate" ? badge("AI 实时候选", "warn") : badge("冻结", "info")} ${esc(block.target_label)} · ${block.complexity} 谓词 · 可回溯:${block.traceable ? "是" : "否"} · 与冻结规则相似: ${similar || "—"}</summary><div class="section-body">${preds || "无谓词依据"}${block.evidence_snippet ? `<div class="notice good">原文依据：${esc(block.evidence_snippet)}</div>` : ""}</div></details>`;
  }).join("");
  const entityGate = stock.entity_consensus?.accepted ? badge("关系通过", "good") : badge("关系未通过", "bad");
  $("stockDetail").innerHTML = `
    <div class="detail-grid"><div class="detail-item"><b>主体</b><span>${esc(stock.event.subject)}</span></div><div class="detail-item"><b>客体</b><span>${esc(stock.event.object)}</span></div><div class="detail-item"><b>关系门控</b><span>${entityGate}<br>${esc(stock.link_evidence)}</span></div><div class="detail-item"><b>原文证据</b><span>${esc(stock.event.evidence_text)}</span></div></div>
    <div class="formula-band"><div class="formula-item"><strong>${fixed(formula.frozen_rule_score_sum)}</strong><span>历史冻结规则分</span></div><div class="formula-item"><strong>${fixed(formula.ai_candidate_rule_score)}</strong><span>AI 实时候选分</span></div><div class="formula-item"><strong>${fixed(formula.rule_score_sum)}</strong><span>规则评分和</span></div><div class="formula-item"><strong>${fixed(formula.evidence_strength, 2)}</strong><span>透明证据分</span></div><div class="formula-item"><strong>${fixed(formula.impact_prior, 2)}</strong><span>Discovery 影响后验</span></div><div class="formula-item"><strong>${typeof formula.stock_relevance === "number" ? fixed(formula.stock_relevance, 2) : "—"}</strong><span>相关性系数</span></div><div class="formula-item"><strong>${fixed(formula.result)}</strong><span>候选因子</span></div></div>
    <div class="equation">${fixed(formula.frozen_rule_score_sum)}${formula.ai_candidate_rule_score ? ` + ${fixed(formula.ai_candidate_rule_score)} (AI 候选)` : ""} = ${fixed(formula.rule_score_sum)} × (${formula.evidence_weight} × ${fixed(formula.evidence_strength, 2)} + ${formula.impact_weight} × ${fixed(formula.impact_prior, 2)})${typeof formula.stock_relevance === "number" ? ` × 相关性 ${fixed(formula.stock_relevance, 2)}` : ""} = ${fixed(formula.result)}</div>
    <div class="score-grid"><div class="score-part"><strong>${fixed(components.source_reliability, 2)}</strong><span>来源可靠性 · 30%</span></div><div class="score-part"><strong>${fixed(components.evidence_grounding, 2)}</strong><span>证据回溯 · 25%</span></div><div class="score-part"><strong>${fixed(components.information_specificity, 2)}</strong><span>信息具体性 · 25%</span></div><div class="score-part"><strong>${fixed(components.business_relevance, 2)}</strong><span>业务关联 · 20%</span></div></div>
    <details class="disclosure" open><summary>AI 与确定性谓词对照（含融合值 · ${stock.predicate_consensus?.length || 0} 项）</summary><div class="table-wrap"><table><thead><tr><th>谓词</th><th>AI</th><th>确定性程序</th><th>融合值</th><th>一致性</th><th>门控</th></tr></thead><tbody>${consensusRows}</tbody></table></div></details>
    <details class="disclosure"><summary>冻结规则匹配（${stock.triggered_rules?.length || 0} 条）</summary><div class="table-wrap"><table><thead><tr><th>规则</th><th>条件</th><th>标签</th><th>独立文档</th><th>后验参考胜率</th><th>评分</th></tr></thead><tbody>${ruleRows}</tbody></table></div></details>
    ${aiCandidateRows ? `<details class="disclosure"><summary>AI 实时候选规则参与（${stock.ai_candidate_rules?.length || 0} 条 · 未历史验证）</summary><div class="table-wrap"><table><thead><tr><th>候选规则</th><th>谓词条件</th><th>标签</th><th>AI 置信</th><th>命中</th><th>暂定分</th></tr></thead><tbody>${aiCandidateRows}</tbody></table></div></details>` : ""}
    ${explainBlocks ? `<details class="disclosure"><summary>规则可解释性（${stock.rule_explainability?.length || 0} 条）</summary>${explainBlocks}</details>` : ""}`;
}

function renderAICandidates(ai) {
  if (!ai?.used) return "";
  const result = ai.result || {};
  const retrieval = ai.embedding_retrieval || {};
  const candidates = result.candidate_rules || [];
  const refs = retrieval.historical_references || [];
  const rows = candidates.map((rule) => `<tr><td>${esc(rule.name)}</td><td class="mono">${esc((rule.conditions || []).join(" AND "))}</td><td>${esc(rule.target_label)}</td><td>${pct(rule.confidence)}</td><td>${badge("待统计验证", "warn")}</td></tr>`).join("") || '<tr><td colspan="5">本次未提出新规则</td></tr>';
  const refRows = refs.map((r) => `<tr><td class="mono">${esc(r.doc_id)}</td><td>${esc(EVENT_LABELS[r.event_type] || r.event_type)}</td><td class="mono">${fixed(r.similarity, 4)}</td><td>${esc(r.summary || "")}</td></tr>`).join("") || "";
  return `<section class="section"><div class="section-header"><div><h2>AI 候选研究</h2><p>语义检索：${esc(retrieval.model || "未记录")}${retrieval.fallback ? "（降级）" : ""} · RAG 参考 ${refs.length} 条历史 AI 结论</p></div>${badge(ai.repair_attempted ? "修复后通过" : "结构校验通过", "info")}</div><div class="section-body"><div class="notice good">${esc(result.summary || "模型已返回结构化研究候选")}</div>${refRows ? `<details class="disclosure"><summary>参考相似历史 AI 结论（RAG · ${refs.length} 条）</summary><div class="table-wrap"><table><thead><tr><th>历史文档</th><th>事件</th><th>相似度</th><th>AI 结论</th></tr></thead><tbody>${refRows}</tbody></table></div></details>` : ""}<details class="disclosure"><summary>查看 AI 提议规则（${candidates.length} 条 · 可进入实时因子）</summary><div class="table-wrap"><table><thead><tr><th>候选规则</th><th>谓词条件</th><th>标签</th><th>AI 置信</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div></details></div></section>`;
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
  let html = `<div class="page-heading merged-audit-heading"><div><span class="eyebrow">FULL AUDIT TRAIL</span><h2>完整研究审计</h2><p>数据覆盖、模型身份、评分、规则支持与时间边界</p></div>${badge("确定性审计", "info")}</div>`;
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
  const failureRows = (cache.failure_categories || []).map((row) => `<tr><td>${esc(row.category)}</td><td>${esc(row.count)}</td><td>${badge("严格拒绝", "warn")}</td></tr>`).join("") || '<tr><td colspan="3">当前没有已记录的 AI 标注拒绝项</td></tr>';
  html += `<section class="section"><div class="section-header"><div><h2>历史 AI 标注拒绝审计</h2><p>仅归类已拒绝结果；不会把失败记录伪装成 AI 成功，也不会放宽金融语义、股票池或原文连续证据校验。</p></div>${badge(`${cache.failed_count || 0} 条待复核`, cache.failed_count ? "warn" : "good")}</div><div class="section-body"><div class="notice warn">R4.1 的修复请求提供原文候选片段与来源限定事件类型。完成真实 Key 验收后，可用 <code>批量生成AI标注.py --retry-failed</code> 只重试失败文档。</div><div class="table-wrap"><table><thead><tr><th>拒绝类型</th><th>数量</th><th>处理原则</th></tr></thead><tbody>${failureRows}</tbody></table></div></div></section>`;
  html += `<section class="section"><div class="section-header"><div><h2>规则透明评分</h2><p>行业政策映射多只股票仍只计一篇独立文档</p></div></div><div class="table-wrap"><table><thead><tr><th>规则</th><th>独立文档</th><th>股票覆盖</th><th>后验胜率</th><th>收缩收益</th><th>半年稳定性</th><th>覆盖项</th><th>证据项</th><th>复杂度惩罚</th></tr></thead><tbody>${diagnostics.map((row) => `<tr><td class="mono">${esc(row.rule_id)}</td><td>${esc(row.independent_document_count)}</td><td>${esc(row.stock_count)}</td><td>${pct(row.posterior_win_rate)}</td><td>${pct(row.shrunk_return)}</td><td>${pct(row.half_year_stability)}</td><td>${fixed(row.coverage_component)}</td><td>${fixed(row.evidence_component)}</td><td>${fixed(row.complexity_penalty)}</td></tr>`).join("")}</tbody></table></div></section>`;
  const target = $("validationAuditContent");
  if (target) target.innerHTML = html;
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
  document.querySelector(".brand").addEventListener("click", (event) => {
    event.preventDefault();
    switchView("liveView");
  });
  document.querySelectorAll("[data-run-mode]").forEach((button) => button.addEventListener("click", () => setRunMode(button.dataset.runMode)));
  document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => setExample(Number(button.dataset.example))));
  $("toggleKey").addEventListener("click", () => {
    $("apiKey").type = $("apiKey").type === "password" ? "text" : "password";
  });
  $("checkApiButton").addEventListener("click", checkApi);
  $("runButton").addEventListener("click", runAnalysis);
  refreshIcons();
  loadStatus();
  loadMacro();
  loadExamples();
  loadHistory();
  loadAudit();
});
