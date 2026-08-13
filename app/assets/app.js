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
let auditData = null;
let auditLoadError = "";
let macroData = null;

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
  fetchJson(`api/example/${index}/fulltext`)
    .then((data) => {
      if (data.full_text && data.full_text !== item.content) {
        $("content").value = `${item.content}\n\n【正文链接全文】\n${data.full_text}`;
      }
    })
    .catch(() => {});
}

async function loadExamples() {
  try {
    const data = await fetchJson("api/examples");
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
  if (viewId === "macroView" && window.Plotly) {
    requestAnimationFrame(() => {
      ["macroForecastChart", "singleTextValidationChart"].forEach((id) => {
        const chart = $(id);
        if (chart && chart.data) Plotly.Plots.resize(chart);
      });
    });
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
    const data = await fetchJson("api/status");
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
    const [status, forecast, backtest, routes] = await Promise.all([
      fetchJson("api/macro/status"), fetchJson("api/macro/forecast"), fetchJson("api/macro/backtest"), fetchJson("api/macro-nowcast"),
    ]);
    macroData = { status, forecast, backtest, routes };
    renderMacro(macroData);
    if (currentAnalysis && $("strategyBacktest")) {
      $("strategyBacktest").innerHTML = renderStrategyBacktest(backtest);
      renderStrategyBacktestChart(backtest);
      refreshIcons();
    }
  } catch (error) {
    $("macroContent").innerHTML = `<div class="error-box"><strong>宏观研究读取失败</strong>${esc(error.message)}</div>`;
  }
}

function renderMacro(data) {
  const latest = data.forecast.latest || {};
  const monthlyModel = data.status.monthly_nowcast_model || {};
  const insufficient = Number(monthlyModel.validation_mae) >= Number(monthlyModel.no_text_validation_mae);
  const oosMetric = (data.forecast.metrics || []).find((row) => row.split === "oos") || {};
  const oosIncrementEstablished = Number(oosMetric.mae_improvement_vs_no_text) > 0;
  const history = (data.forecast.history || []).filter((row) => row.split === "oos");
  const html = `<div class="macro-hero">
    <div><span class="eyebrow">AlphaLens · RESEARCH VALIDATION</span><h1>研究验证</h1><p>模型表现与完整研究审计合并展示：先核对官方历史目标、训练/验证划分和基线误差，再检查数据覆盖、AI标注、冻结规则与时间边界。</p></div>
  </div>
  <div class="macro-kpis">
    ${metric(data.status.verified_historical_texts || 0, "可核验历史文本")}
    ${metric(monthlyModel.training_month_count || 0, "2015—2021 训练月份")}
    ${metric(monthlyModel.validation_month_count || 0, "2022—2023 验证月份")}
    ${metric(`${fixed(monthlyModel.validation_mae, 2)}pct`, "月度 Nowcast 验证 MAE")}
  </div>
  <div class="notice ${oosIncrementEstablished ? "good" : "error"}"><strong>${oosIncrementEstablished ? "冻结 OOS 文本增量已观察" : "冻结 OOS 文本增量不足"}</strong>：验证期文本 MAE ${fixed(monthlyModel.validation_mae, 2)}pct、无文本 ${fixed(monthlyModel.no_text_validation_mae, 2)}pct；OOS 文本 MAE ${fixed(oosMetric.mae, 2)}pct、无文本 ${fixed(oosMetric.no_text_mae, 2)}pct。${esc(data.status.evidence_warning || "")}</div>
  <div class="macro-grid">
    <section class="section macro-panel"><div class="section-header"><div><h2>历史目标与月度 Nowcast</h2><p>发布日防泄漏 · 1—2月保持合并观测 · 月度文本去重聚合</p></div>${badge(monthlyModel.model_name || "--", "info")}</div><div id="macroForecastChart" class="macro-chart"></div></section>
    <section class="section macro-panel"><div class="section-header"><div><h2>月度文本增量验证</h2><p>2022—2023冻结验证 · 与无文本同比模型同口径比较</p></div>${badge(insufficient ? "增量未建立" : "验证优于基线", insufficient ? "warn" : "good")}</div><div id="singleTextValidationChart" class="macro-chart"></div></section>
  </div>
  ${renderRouteEvaluation(data.routes)}
  ${renderStrategyBacktest(data.backtest, "Macro")}
  <section class="section"><div class="section-header"><div><h2>模型验收与适用边界</h2><p>一月一个目标样本；新文本只形成月度聚合的边际变化</p></div></div><div class="section-body"><div class="acceptance-grid">
    <div class="acceptance-card"><span>月度 Nowcast</span><strong>${fixed(monthlyModel.validation_mae, 2)}pct</strong><p>验证 MAE；无文本同比模型 ${fixed(monthlyModel.no_text_validation_mae, 2)}pct。冻结 OOS 文本 MAE ${fixed(oosMetric.mae, 2)}pct、无文本 ${fixed(oosMetric.no_text_mae, 2)}pct。</p></div>
    <div class="acceptance-card"><span>月度样本</span><strong>${esc(monthlyModel.training_month_count || 0)} / ${esc(monthlyModel.validation_month_count || 0)}</strong><p>2015—2021训练 / 2022—2023验证；2024年起冻结OOS。</p></div>
    <div class="acceptance-card warning-card"><span>输出边界</span><strong>边际 Nowcast</strong><p>本篇文本只改变本月聚合预测与下一次调仓权重；未来持有期未发生时不生成策略收益。</p></div>
  </div></div></section>
  <section class="section"><div class="section-header"><div><h2>可审计研究边界</h2></div></div><div class="section-body"><div class="detail-grid">
    <div class="detail-item"><b>训练 / 规则发现</b><span>2015—2021</span></div><div class="detail-item"><b>模型 / 策略验证</b><span>2022—2023</span></div><div class="detail-item"><b>冻结 OOS</b><span>2024—最新</span></div><div class="detail-item"><b>交易代理边界</b><span>399808 仅作上市前研究代理，不冒充 ETF</span></div>
  </div><p class="disclaimer">本报告仅供研究参考，不构成投资建议</p></div></section>
  <div id="validationAuditContent"><div class="loading-surface validation-loading">正在读取完整研究审计</div></div>`;
  $("macroContent").innerHTML = html;
  if (window.Plotly) {
    const actual = history.filter((row) => row.actual_yoy !== "");
    Plotly.newPlot("macroForecastChart", [
      { x: actual.map((row) => row.target_period_end), y: actual.map((row) => Number(row.actual_yoy)), name: "官方值", mode: "lines+markers", line: { color: "#15212b" } },
      { x: history.map((row) => row.target_period_end), y: history.map((row) => Number(row.predicted_yoy)), name: "冻结预测", mode: "lines", line: { color: "#116fae" } },
    ], chartLayout("同比增速（%）"), { responsive: true, displayModeBar: false });
    Plotly.newPlot("singleTextValidationChart", [{
      x: ["文本增强 Nowcast", "无文本同比模型"],
      y: [Number(monthlyModel.validation_mae || 0), Number(monthlyModel.no_text_validation_mae || 0)],
      type: "bar", marker: { color: ["#116fae", "#9aa8b3"] }, texttemplate: "%{y:.2f} pct", textposition: "outside",
    }], { ...chartLayout("MAE（百分点，越低越好）"), showlegend: false }, { responsive: true, displayModeBar: false });
    renderStrategyBacktestChart(data.backtest, "strategyNavChartMacro");
  }
  if (auditData) renderAudit(auditData);
  if (auditLoadError && $("validationAuditContent")) {
    $("validationAuditContent").innerHTML = `<div class="error-box"><strong>研究审计读取失败</strong>${esc(auditLoadError)}</div>`;
  }
  refreshIcons();
}

function renderRouteEvaluation(evaluation) {
  if (!evaluation) return "";
  const labels = {
    historical_rules: "路线一：历史文本学习并冻结规则",
    ai_dynamic_rules: "路线二：新文本由 LLM 动态提取规则",
  };
  const rows = Object.entries(labels).map(([key, label]) => {
    const route = evaluation.routes?.[key] || {};
    const metrics = route.text_validation?.metrics || {};
    const status = !route.route_available ? "无合格规则" : route.qualified ? "验证门槛通过" : "未通过";
    return `<tr><td>${esc(label)}</td><td>${esc(route.selected_model || "--")}</td><td class="mono">${fixed(metrics.mae, 3)}</td><td class="mono">${fixed(metrics.rmse, 3)}</td><td>${pct(metrics.acceleration_direction_accuracy)}</td><td>${pct(route.validation_text_coverage)}</td><td>${badge(status, route.qualified ? "good" : "warn")}</td></tr>`;
  }).join("");
  const routeChosen = evaluation.selected_route === "historical_rules" || evaluation.selected_route === "ai_dynamic_rules";
  const selectedLabel = labels[evaluation.selected_route] || "无文本 Ridge 基线";
  const provisionalLabel = labels[evaluation.provisional_rule_route] || "尚无可比规则路线";
  return `<section class="section"><div class="section-header"><div><h2>两条规则生成路线对照</h2><p>只使用2022—2023验证期选择，2024年以后冻结；评价目标是行业同比 Nowcast，不是股票收益</p></div>${badge(routeChosen ? "已选规则路线" : "文本增量不足", routeChosen ? "good" : "warn")}</div><div class="section-body">
    <div class="notice ${evaluation.data_sufficient ? "good" : "error"}"><strong>正式选择：${esc(selectedLabel)}；两路线暂优：${esc(provisionalLabel)}</strong>。${esc(evaluation.conclusion || "文本预测增量不足")}；训练 / 验证 / OOS 目标观测为 ${esc(evaluation.target_counts?.train || 0)} / ${esc(evaluation.target_counts?.validation || 0)} / ${esc(evaluation.target_counts?.oos || 0)}，有效宏观 LLM 标注 ${esc(evaluation.data_audit?.macro_ai_success_count || 0)} 篇。当前 Train 尚缺 ${esc(evaluation.data_sufficiency_checks?.train_gap || 0)} 个官方观测，暂优路线不能视为冻结结论。</div>
    <div class="table-wrap"><table><thead><tr><th>规则路线</th><th>模型</th><th>验证 MAE</th><th>验证 RMSE</th><th>加速度方向</th><th>文本覆盖</th><th>门槛</th></tr></thead><tbody>${rows}</tbody></table></div>
  </div></section>`;
}

function chartLayout(yTitle) {
  return { margin: { l: 48, r: 18, t: 20, b: 42 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", hovermode: "x unified", legend: { orientation: "h", y: 1.12 }, xaxis: { gridcolor: "#edf0f2" }, yaxis: { title: yTitle, gridcolor: "#edf0f2" }, font: { family: "Inter, sans-serif", size: 11, color: "#40505e" } };
}

async function loadAudit() {
  try {
    auditData = await fetchJson("api/audit");
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
    const data = await fetchJson("api/ai/check", {
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
      ? await fetchJson("api/replay/storage-policy")
      : await fetchJson("api/macro/analyze", {
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
  const forecast = data.text_forecast || {};
  const eventName = EVENT_LABELS[data.event_type] || data.event_type || "未识别";
  const aiSummary = data.ai_analysis?.result?.summary || "已完成结构化事件、实体和谓词校验";
  const replayFlag = data.is_replay ? '<span class="replay-flag">冻结回放</span>' : "";
  const steps = [
    ["结构化事件", eventName],
    ["关联股票", `${stocks.length} 只通过股票池校验`],
    ["谓词对照", `${top?.predicate_consensus?.length || 0} 项逐股票对照`],
    ["一致性门控", data.consensus_gate_passed ? "全部通过" : `${data.disputed_predicates?.length || 0} 项排除`],
    ["冻结规则", `${data.triggered_rules?.length || 0} 条触发`],
    ["月度聚合 Nowcast", data.text_forecast ? `${fixed(forecast.nowcast_after_text, 2)}%` : "未生成"],
  ];
  let html = `<section class="result-summary">
    <div class="result-lead">${replayFlag}<h2>${esc(eventName)}</h2><p>${esc(aiSummary)}</p></div>
    ${metric(data.text_forecast ? `${fixed(forecast.nowcast_after_text, 2)}%` : "—", "加入后月度 Nowcast")}
    ${metric(data.text_forecast ? `${Number(forecast.marginal_change) >= 0 ? "+" : ""}${fixed(forecast.marginal_change, 2)} pct` : "—", "本篇文本边际变化")}
    ${metric(data.text_forecast ? `${fixed(forecast.lower_90, 2)}% — ${fixed(forecast.upper_90, 2)}%` : "—", "90%预测区间")}
  </section>`;
  html += `<section class="section"><div class="section-header"><div><h2>处理链路</h2><p>${esc(data.source_name)} · ${esc(data.event_time)}</p></div>${data.source_url ? `<a class="download-button" href="${esc(data.source_url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>查看原文</a>` : ""}</div><div class="pipeline">${steps.map((step, index) => `<div class="pipeline-step"><span class="step-number">${index + 1}</span><b>${esc(step[0])}</b><span>${esc(step[1])}</span></div>`).join("")}</div></section>`;
  html += renderSourceAudit(data);
  if (data.text_forecast) {
    const forecast = data.text_forecast;
    const contributionRows = (forecast.top_contributions || []).slice(0, 6).map((item) => `<tr><td class="mono">${esc(item.feature)}</td><td class="mono ${Number(item.contribution_pct_point) >= 0 ? "positive" : "negative"}">${Number(item.contribution_pct_point) >= 0 ? "+" : ""}${fixed(item.contribution_pct_point, 4)} pct</td></tr>`).join("");
    const impact = forecast.strategy_impact || {};
    const duplicate = forecast.duplicate_status || {};
    html += `<section class="section text-forecast"><div class="section-header"><div><h2>本篇文本对本月 Nowcast 的边际影响</h2><p>同月文本先去重聚合；本篇只增加一次证据贡献，不独立代表整个月</p></div>${badge(forecast.text_increment_status === "validated_positive" ? "验证优于无文本基线" : "研究参考", forecast.text_increment_status === "validated_positive" ? "good" : "warn")}</div><div class="section-body">
      <div class="forecast-number"><span>${esc(forecast.target_name)}</span><strong>${fixed(forecast.nowcast_after_text, 2)}%</strong><small>加入前 ${fixed(forecast.nowcast_before_text, 2)}% · 本篇边际 ${Number(forecast.marginal_change) >= 0 ? "+" : ""}${fixed(forecast.marginal_change, 2)} pct · 90%区间 ${fixed(forecast.lower_90, 2)}% — ${fixed(forecast.upper_90, 2)}%</small></div>
      <div class="detail-grid"><div class="detail-item"><b>无文本同比预测</b><span class="mono">${fixed(forecast.no_text_predicted_yoy, 2)}%</span></div><div class="detail-item"><b>月度文本数</b><span class="mono">${esc(forecast.monthly_document_count_before)} → ${esc(forecast.monthly_document_count_after)}</span></div><div class="detail-item"><b>新能源仓位</b><span class="mono">${pct(impact.risk_weight_before)} → ${pct(impact.risk_weight_after)}</span></div><div class="detail-item"><b>验证 MAE</b><span class="mono">${fixed(forecast.validation_mae, 2)} pct</span></div></div>
      ${duplicate.is_duplicate ? `<div class="notice"><strong>重复证据已拦截：</strong>该文本已按${duplicate.matched_by === "canonical_url" ? "规范化链接" : "标准化标题"}匹配历史文档 ${esc(duplicate.matched_doc_id)}，不会再次贡献月度特征。</div>` : ""}
      <div class="notice ${forecast.text_increment_status === "validated_positive" ? "good" : "error"}">${esc(forecast.analysis_conclusion)} ${esc(forecast.forecast_basis)}</div>
      <div class="notice"><strong>策略收益边界：</strong>${esc(impact.explanation)}</div>
      <details class="disclosure" open><summary>本篇文本的边际特征贡献</summary><div class="table-wrap"><table><thead><tr><th>特征</th><th>预测边际贡献</th></tr></thead><tbody>${contributionRows}</tbody></table></div></details>
    </div></section>`;
  }
  if (data.disputed_predicates?.length) {
    html += `<div class="notice error">已排除：${esc(data.disputed_predicates.join("、"))}。争议或非法谓词不会进入冻结规则或同比增长预测证据。</div>`;
  }
  html += `<section class="section"><div class="section-header"><div><h2>逐股票结构化证据审计</h2><p>股票仅用于核验产业链关系、原文证据和19谓词，不输出个股预测值</p></div>${badge("证据审计", "info")}</div><div class="section-body"><div class="stock-selector">${stocks.map((stock, index) => `<button class="stock-button ${index === 0 ? "active" : ""}" data-stock-index="${index}" type="button">${esc(stock.name)} · ${esc(stock.code)}</button>`).join("") || "未形成关联主体"}</div><div id="stockDetail" class="stock-detail"></div></div></section>`;
  html += renderAICandidates(data.ai_analysis);
  html += `<section class="section"><div class="section-header"><div><h2>自动研究记录</h2></div><button class="download-button" id="downloadReport" type="button"><i data-lucide="download"></i>下载 Markdown</button></div></section>`;
  html += `<div id="strategyBacktest">${macroData?.backtest ? renderStrategyBacktest(macroData.backtest) : '<div class="loading-surface strategy-loading">正在读取量化策略回测</div>'}</div>`;
  $("result").innerHTML = html;
  document.querySelectorAll("[data-stock-index]").forEach((button) => button.addEventListener("click", () => renderStockDetail(Number(button.dataset.stockIndex))));
  $("downloadReport").addEventListener("click", () => downloadReport(data.report || ""));
  if (top) renderStockDetail(0);
  if (macroData?.backtest) renderStrategyBacktestChart(macroData.backtest);
  refreshIcons();
}

function renderStrategyBacktest(backtest, idSuffix = "") {
  const labels = {
    buy_hold: "买入并持有新能源ETF",
    trend: "纯时间序列动量",
    trend_latest_macro: "动量 + 最新已公布行业数据",
    trend_alphalens: "动量 + AlphaLens行业景气预测",
    trend_oracle: "动量 + 下一期真实值（Oracle）",
  };
  const metrics = backtest.metrics || [];
  const alpha = metrics.find((row) => row.strategy === "trend_alphalens") || {};
  const bootstrap = (backtest.bootstrap || [])[0] || {};
  const selection = backtest.strategy_selection || {};
  const incrementEstablished = bootstrap.conclusion === "positive_increment_observed";
  const observedPositive = Number(bootstrap.annualized_net_return_difference) > 0;
  const conclusion = incrementEstablished ? "正增量且置信区间通过" : (observedPositive ? "观察到正增量，统计显著性尚未建立" : "交易增量尚未建立");
  const metricRows = metrics.map((row) => `<tr><td>${esc(labels[row.strategy] || row.strategy)}${row.tradable === "false" ? ` ${badge("不可交易", "warn")}` : ""}</td><td>${pct(row.annual_return)}</td><td>${pct(row.annual_volatility)}</td><td class="mono">${fixed(row.sharpe, 3)}</td><td>${pct(row.max_drawdown)}</td><td>${pct(row.annual_turnover)}</td></tr>`).join("");
  return `<section class="section strategy-backtest"><div class="section-header"><div><h2>趋势策略 + AlphaLens 宏观确认</h2><p>新能源ETF 516160 的12个月趋势与60日波动率缩放；剩余仓位配置5年期国债ETF 511010</p></div>${badge(`${esc(backtest.primary_cost_bps || 10)} bp 成本`, "info")}</div><div class="section-body">
    <div class="detail-grid"><div class="detail-item"><b>调仓时点</b><span>${esc(backtest.rebalance_timing)}</span></div><div class="detail-item"><b>AlphaLens增强年化收益</b><span class="mono">${pct(alpha.annual_return)}</span></div><div class="detail-item"><b>AlphaLens增强 Sharpe</b><span class="mono">${fixed(alpha.sharpe, 3)}</span></div><div class="detail-item"><b>相对纯趋势年化差</b><span class="mono ${Number(bootstrap.annualized_net_return_difference) >= 0 ? "positive" : "negative"}">${pct(bootstrap.annualized_net_return_difference)}</span></div></div>
    <div class="notice ${observedPositive ? "good" : "error"}"><strong>${conclusion}</strong>：AlphaLens增强相对纯趋势的年化净收益差 ${pct(bootstrap.annualized_net_return_difference)}，6个月时间块 Bootstrap 95%区间为 ${pct(bootstrap.ci_lower_95)} 至 ${pct(bootstrap.ci_upper_95)}。负结果也如实披露，不作收益宣传。</div>
    <div class="audit-note"><strong>策略防泄漏：</strong>12个月趋势、60日波动率与10%目标波动率全部预先固定；2024年OOS前冻结。${esc(backtest.comparison_boundary || "")} ${esc(backtest.oracle_label || "")}</div>
    <div id="strategyNavChart${esc(idSuffix)}" class="strategy-chart"></div>
    <div class="table-wrap"><table><thead><tr><th>策略</th><th>年化收益</th><th>年化波动</th><th>Sharpe</th><th>最大回撤</th><th>年换手率</th></tr></thead><tbody>${metricRows}</tbody></table></div>
    <p class="disclaimer">本报告仅供研究参考，不构成投资建议</p>
  </div></section>`;
}

function renderStrategyBacktestChart(backtest, chartId = "strategyNavChart") {
  if (!window.Plotly || !$(chartId)) return;
  const nav = backtest.nav || [];
  const series = [
    ["buy_hold", "买入持有", "#9aa8b3"],
    ["trend", "纯趋势", "#15212b"],
    ["trend_latest_macro", "趋势 + 已公布宏观", "#7a8995"],
    ["trend_alphalens", "趋势 + AlphaLens", "#116fae"],
    ["trend_oracle", "Oracle（不可交易）", "#b76b2a"],
  ];
  Plotly.newPlot(chartId, series.map(([strategy, name, color]) => {
    const rows = nav.filter((row) => row.strategy === strategy);
    return { x: rows.map((row) => row.trade_month), y: rows.map((row) => Number(row.nav)), name, mode: "lines", line: { color, width: strategy === "trend_alphalens" ? 2.4 : 1.6, dash: strategy === "trend_oracle" ? "dot" : "solid" } };
  }), { ...chartLayout("成本后净值"), margin: { l: 52, r: 18, t: 34, b: 42 } }, { responsive: true, displayModeBar: false });
}

function renderStockDetail(index) {
  const stock = currentAnalysis.stock_results[index];
  const fusion = stock.predicate_fusion || {};
  document.querySelectorAll("[data-stock-index]").forEach((button, current) => button.classList.toggle("active", current === index));
  const consensusRows = (stock.predicate_consensus || []).map((row) => {
    const fused = fusion[row.name]?.fused;
    const triggers = typeof fused === "number" && fused >= 0.5;
    return `<tr>
    <td class="mono">${esc(row.name)}</td><td class="mono">${esc(row.ai_value)}</td><td class="mono">${esc(row.rule_value)}</td>
    <td class="mono">${typeof fused === "number" ? fixed(fused, 3) : "—"}</td>
    <td class="status-${esc(row.status)}">${esc(STATUS_LABELS[row.status] || row.status)}</td><td>${triggers ? "可进入规则审计" : "不进入冻结规则"}</td>
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
    <details class="disclosure" open><summary>AI 与确定性谓词对照（含融合值 · ${stock.predicate_consensus?.length || 0} 项）</summary><div class="table-wrap"><table><thead><tr><th>谓词</th><th>AI</th><th>确定性程序</th><th>融合值</th><th>一致性</th><th>证据门控</th></tr></thead><tbody>${consensusRows}</tbody></table></div></details>
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
  return `<section class="section"><div class="section-header"><div><h2>AI 候选研究</h2><p>语义检索：${esc(retrieval.model || "未记录")}${retrieval.fallback ? "（降级）" : ""} · RAG 参考 ${refs.length} 条历史 AI 结论</p></div>${badge(ai.repair_attempted ? "修复后通过" : "结构校验通过", "info")}</div><div class="section-body"><div class="notice good">${esc(result.summary || "模型已返回结构化研究候选")}</div>${refRows ? `<details class="disclosure"><summary>参考相似历史 AI 结论（RAG · ${refs.length} 条）</summary><div class="table-wrap"><table><thead><tr><th>历史文档</th><th>事件</th><th>相似度</th><th>AI 结论</th></tr></thead><tbody>${refRows}</tbody></table></div></details>` : ""}<details class="disclosure"><summary>查看 AI 提议规则（${candidates.length} 条 · 仅作同比预测证据候选）</summary><div class="table-wrap"><table><thead><tr><th>候选规则</th><th>谓词条件</th><th>标签</th><th>AI 置信</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div></details></div></section>`;
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
  loadAudit();
});
