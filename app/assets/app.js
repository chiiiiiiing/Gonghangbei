const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const fixed = (value, digits = 3) => Number(value || 0).toFixed(digits);
const pct = (value, digits = 2) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const LABELS = { bullish: "看涨方向", bearish: "看跌方向", neutral: "中性方向" };
const STRATEGIES = { pure_trend: "纯趋势", zero_shot_llm: "零样本 LLM + 趋势", rift_enhanced_trend: "RIFT 增强趋势" };
const PREDICATE_LABELS = {
  supply_disruption: "供应扰动", supply_expansion: "供应扩张", production_resumption: "复产",
  demand_ev_positive: "电动车需求正向", demand_storage_positive: "储能需求正向",
  demand_weak_or_price_war: "需求疲弱或价格战", inventory_drawdown: "产业去库",
  inventory_build: "产业累库", warehouse_receipt_decline: "仓单下降",
  warehouse_receipt_increase: "仓单增加", policy_demand_support: "政策需求支持",
  import_supply_pressure: "进口供应压力", cost_support: "成本支撑", delivery_pressure: "交割压力",
  authoritative_source: "权威来源", quantitative_evidence: "量化证据", uncertainty_high: "高不确定性",
};

let EXAMPLES = [];
let lithium = { status: null, forecast: null, backtest: null };

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({ error: "服务返回了无法解析的响应" }));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function badge(label, kind = "info") {
  return `<span class="badge ${kind}">${esc(label)}</span>`;
}

function metric(value, label) {
  return `<div class="metric-cell"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
}

function switchView(viewId) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  document.querySelectorAll(".nav-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === viewId));
  if (viewId === "macroView") requestAnimationFrame(renderValidationCharts);
}

function setConnection(kind, message) {
  const node = $("connectionStatus");
  node.className = `connection-state ${kind}`;
  node.innerHTML = `<span class="mini-dot"></span><span>${esc(message)}</span>`;
}

async function loadExamples() {
  try {
    const data = await fetchJson("api/examples");
    EXAMPLES = data.examples || [];
  } catch (_) {
    EXAMPLES = [];
  }
}

function setExample(index) {
  const item = EXAMPLES[index];
  if (!item) return;
  $("title").value = item.title;
  $("content").value = item.content;
  $("sourceType").value = item.type;
  $("sourceName").value = item.name;
  $("eventDate").value = item.date;
  $("sourceUrl").value = item.url || "";
}

async function loadStatus() {
  try {
    const [status, forecast, backtest] = await Promise.all([
      fetchJson("api/lithium/status"), fetchJson("api/lithium/forecast"), fetchJson("api/lithium/backtest"),
    ]);
    lithium = { status, forecast, backtest };
    $("statusDot").classList.add("on");
    const readiness = status.data_ready ? "受控数据已就绪" : "等待受控 CSV";
    $("statusText").textContent = `碳酸锂研究服务已连接 · ${readiness} · ${status.version}`;
    renderValidation();
  } catch (error) {
    $("statusText").textContent = `本地研究服务未连接 · ${error.message}`;
    $("validationAuditContent").innerHTML = `<div class="error-box"><strong>研究验证读取失败</strong>${esc(error.message)}</div>`;
  }
}

async function checkApi() {
  const apiKey = $("apiKey").value.trim();
  if (!apiKey) return setConnection("fail", "请先填写 API Key");
  $("checkApiButton").disabled = true;
  setConnection("neutral", "正在核对模型权限");
  try {
    const data = await fetchJson("api/ai/check", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey }),
    });
    setConnection("ok", `${data.returned_model} 已验证`);
  } catch (error) {
    setConnection("fail", error.message);
  } finally {
    $("checkApiButton").disabled = false;
  }
}

function analysisPayload() {
  return {
    title: $("title").value.trim(), content: $("content").value.trim(),
    source_type: $("sourceType").value, source_name: $("sourceName").value.trim(),
    event_date: $("eventDate").value, source_url: $("sourceUrl").value.trim(),
    api_key: $("apiKey").value.trim(),
  };
}

async function runAnalysis() {
  $("runButton").disabled = true;
  $("runState").classList.add("on");
  $("result").innerHTML = "";
  try {
    const data = await fetchJson("api/lithium/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(analysisPayload()),
    });
    renderAnalysis(data);
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("result").innerHTML = `<div class="error-box"><strong>分析未完成</strong>${esc(error.message)}</div>`;
  } finally {
    $("runButton").disabled = false;
    $("runState").classList.remove("on");
  }
}

function renderAnalysis(data) {
  const rules = data.activated_rules || [];
  const predicates = data.predicate_consensus || [];
  const activeTrue = predicates.filter((row) => row.status === "agreed_true");
  const disputed = predicates.filter((row) => row.status === "disputed");
  const predicted = data.predicted_variable || {};
  const mapping = data.strategy_mapping || {};
  const increment = data.increment_evidence || {};
  const trendPosition = Number(mapping.baseline_position || 0);
  const enhancedPosition = Number(mapping.enhanced_position || trendPosition);
  const positionDelta = Number(mapping.position_delta || 0);
  const mappingReady = mapping.status === "mapped" || mapping.status === "awaiting_next_trading_day";
  const labelKind = data.direction_label === "bullish" ? "good" : data.direction_label === "bearish" ? "bad" : "warn";
  const ruleRows = rules.map((rule) => `<tr><td class="mono">${esc(rule.rule_id)}</td><td>${esc((rule.conditions || []).map((name) => PREDICATE_LABELS[name] || name).join(" + "))}</td><td>${esc(LABELS[rule.target_label] || rule.target_label)}</td><td class="mono">${fixed(rule.score)}</td><td>${esc(rule.support_documents)}</td></tr>`).join("") || '<tr><td colspan="5">当前没有冻结规则被 agreed_true 谓词激活，RIFT 分数按 0 处理。</td></tr>';
  const predicateRows = predicates.map((row) => {
    const status = { agreed_true: "一致为真", agreed_false: "一致为假", disputed: "判断冲突" }[row.status] || row.status;
    return `<tr><td>${esc(PREDICATE_LABELS[row.name] || row.name)}</td><td class="mono">${row.deterministic_value ? "true" : "false"}</td><td class="mono">${row.ai_value ? "true" : "false"}</td><td>${badge(status, row.status === "agreed_true" ? "good" : row.status === "disputed" ? "bad" : "info")}</td><td>${esc(row.evidence_text || "-")}</td></tr>`;
  }).join("");
  $("result").innerHTML = `
    <section class="result-summary">
      <div class="result-lead"><span class="eyebrow">5-DAY DIRECTION</span><h2>${esc(LABELS[data.direction_label] || data.direction_label)}</h2><p>${esc(data.inference_mode === "rulebook_inactive" ? "规则簿未激活，系统按中性输出，不用零样本判断替代冻结规则。" : "固定谓词与冻结规则簿已完成规则增强推理。")}</p></div>
      ${metric(fixed(data.direction_score, 3), "RIFT 方向分数")}
      ${metric(pct(data.confidence), "模型置信度")}
      ${metric(`${rules.length} 条`, "激活冻结规则")}
    </section>
    <section class="section"><div class="section-header"><div><h2>单文本预测 → 趋势策略</h2><p>${esc(mapping.signal_market_date || data.publish_time)} 收盘后形成信号 · ${esc(mapping.execution_trade_date || "等待下一交易日")} 开盘执行</p></div>${badge(LABELS[data.direction_label] || data.direction_label, labelKind)}</div>
      <div class="section-body"><div class="detail-grid">
        <div class="detail-item"><b>主预测变量</b><span>${esc(predicted.display_name || "LC 未来5日方向分数")}</span></div>
        <div class="detail-item"><b>文本方向分数</b><span class="mono">${fixed(predicted.value ?? data.direction_score, 3)}</span></div>
        <div class="detail-item"><b>20日纯趋势仓位</b><span class="mono">${fixed(trendPosition, 3)}</span></div>
        <div class="detail-item"><b>规则确认趋势仓位</b><span class="mono">${fixed(enhancedPosition, 3)}</span></div>
        <div class="detail-item"><b>仓位边际变化</b><span class="mono">${positionDelta >= 0 ? "+" : ""}${fixed(positionDelta, 3)}</span></div>
        <div class="detail-item"><b>文本是否确认趋势</b><span>${mapping.text_confirmed_trend ? "是" : "否"}</span></div>
      </div>
      <div class="notice ${mappingReady ? "good" : "warn"}"><strong>策略映射：</strong>${mappingReady ? esc(mapping.formula) : `尚不可映射（${esc(mapping.status || "unknown")}）。`}</div>
      <div class="notice ${increment.prospective_status === "positive_increment_established" ? "good" : "warn"}"><strong>增量证据：${esc(increment.prospective_conclusion || "前瞻交易增量待检验")}。</strong> 前瞻观察 ${esc(increment.prospective_observations || 0)} 个；${esc(increment.acceptance_gate || "待统计检验")}。</div></div>
    </section>
    <section class="section"><div class="section-header"><div><h2>激活规则</h2><p>仅 agreed_true 谓词能够触发冻结规则</p></div>${badge(`${rules.length} / ${data.rulebook_size} 条`, rules.length ? "good" : "warn")}</div><div class="table-wrap"><table><thead><tr><th>规则</th><th>条件</th><th>方向</th><th>评分</th><th>独立文档</th></tr></thead><tbody>${ruleRows}</tbody></table></div></section>
    <section class="section"><div class="section-header"><div><h2>固定谓词共识</h2><p>${activeTrue.length} 项一致为真 · ${disputed.length} 项冲突不进入规则</p></div>${badge(`${predicates.length} 项 Schema`, "info")}</div><div class="table-wrap"><table><thead><tr><th>谓词</th><th>确定性程序</th><th>LLM</th><th>共识</th><th>原文证据</th></tr></thead><tbody>${predicateRows}</tbody></table></div></section>
    <p class="disclaimer standalone">${esc(data.disclaimer)}。${esc(data.research_boundary)}</p>`;
  refreshIcons();
}

function renderValidation() {
  const status = lithium.status;
  const backtest = lithium.backtest;
  if (!status || !backtest) return;
  const v3 = backtest.deepseek_v4_research || {};
  const bootstrap = v3.old_oos_stress_bootstrap || backtest.bootstrap || {};
  const dataMessage = status.data_ready ? "受控数据已通过校验" : "等待导入受控 CSV 并生成冻结 AI 标注";
  const dataErrors = (status.data_errors || []).map((error) => `<li>${esc(error)}</li>`).join("");
  const metrics = v3.old_oos_stress_metrics || backtest.metrics || [];
  const metricRows = metrics.map((row) => `<tr><td>${esc(STRATEGIES[row.strategy] || row.strategy)}</td><td>${esc(row.observations)}</td><td>${pct(row.annual_return)}</td><td>${pct(row.annual_volatility)}</td><td class="mono">${fixed(row.sharpe)}</td><td>${pct(row.max_drawdown)}</td><td>${pct(row.annual_turnover)}</td></tr>`).join("") || '<tr><td colspan="7">受控行情或 OOS 历史不足，尚未形成回测指标。</td></tr>';
  const costs = (v3.cost_sensitivity || backtest.cost_sensitivity || []).map((row) => `<tr><td>${esc(row.cost_bps)} bp</td><td>${pct(row.trend_annual_return)}</td><td>${pct(row.enhanced_annual_return)}</td><td class="mono">${pct(row.annual_return_difference)}</td></tr>`).join("") || '<tr><td colspan="4">尚未评估</td></tr>';
  const prospective = backtest.prospective_candidate || {};
  const prospectiveBootstrap = prospective.prospective_bootstrap || {};
  const validationBootstrap = prospective.validation_bootstrap || {};
  const historicalStress = prospective.historical_oos_stress_bootstrap || {};
  const decisionLedger = prospective.decision_ledger || {};
  const v3Counts = v3.counts || {};
  const v3Validation = v3.validation_bootstrap || {};
  const v3Stress = v3.old_oos_stress_bootstrap || {};
  const v3ConfirmedValidation = v3.validation_confirmed_trend_bootstrap || {};
  const v3ConfirmedStress = v3.old_oos_confirmed_trend_bootstrap || {};
  const provenance = status.text_provenance || {};
  const sourceQuality = provenance.quality_counts || {};
  const prospectiveRows = (prospective.validation_metrics || []).map((row) => `<tr><td>${row.strategy === "pure_trend" ? "纯趋势" : "同向文本叠加"}</td><td>${esc(row.observations)}</td><td>${pct(row.annual_return)}</td><td class="mono">${fixed(row.sharpe)}</td><td>${pct(row.max_drawdown)}</td></tr>`).join("") || '<tr><td colspan="5">尚未形成验证指标。</td></tr>';
  $("validationAuditContent").innerHTML = `
    <div class="research-banner validation-banner"><div><span class="eyebrow">FROZEN OUT-OF-SAMPLE</span><h1>研究验证</h1><p>规则归纳、验证选参和 2026 年起 OOS 严格分离；主结论只比较成本后 RIFT 增强趋势与纯趋势。</p></div><div>${badge(backtest.conclusion, backtest.increment_established ? "good" : "bad")}</div></div>
    <div class="macro-kpis">
      ${metric(v3Counts.texts || status.counts.texts, "产业文本")}${metric(status.counts.continuous_days, "主力连续交易日")}${metric(v3Counts.rules || status.counts.qualified_rules, "稳定合格规则")}${metric(bootstrap.observations || 0, "OOS 交易日")}
    </div>
    <div class="notice ${status.data_ready ? "good" : "warn"}"><strong>${esc(dataMessage)}</strong>。Discovery ${esc(status.sample_boundaries.discovery)}；Validation ${esc(status.sample_boundaries.validation)}；OOS ${esc(status.sample_boundaries.oos)}。${dataErrors ? `<ul>${dataErrors}</ul>` : ""}</div>
    <div class="notice ${provenance.verified ? "good" : "warn"}"><strong>文本来源审计：${provenance.verified ? "哈希与来源匹配" : "存在不一致"}。</strong> 全文抓取 ${esc(sourceQuality.fetched_full || 0)} 篇，部分抓取 ${esc(sourceQuality.fetched_partial || 0)} 篇，仅仓库快照 ${esc(sourceQuality.repository_snapshot_only || 0)} 篇，官方仓单派生事实 ${esc(sourceQuality.derived_official_fact || 0)} 条。</div>
    <section class="section"><div class="section-header"><div><h2>DeepSeek V4 规则增强方向推理</h2><p>谓词固定 Schema · Discovery 标签净化 · 零样本与 RIFT 独立调用</p></div>${badge(v3.conclusion || "尚未生成", "bad")}</div><div class="section-body">
      <div class="detail-grid"><div class="detail-item"><b>V4 谓词标注</b><span class="mono">${esc(v3Counts.predicate_annotations || 0)}</span></div><div class="detail-item"><b>V4 方向推理</b><span class="mono">${esc(v3Counts.direction_annotations || 0)}</span></div><div class="detail-item"><b>稳定规则</b><span class="mono">${esc(v3Counts.rules || 0)}</span></div><div class="detail-item"><b>70/30 Validation 差</b><span class="mono">${pct(v3Validation.annualized_net_return_difference)}</span></div><div class="detail-item"><b>70/30 OOS 差</b><span class="mono">${pct(v3Stress.annualized_net_return_difference)}</span></div><div class="detail-item"><b>70/30 OOS 下界</b><span class="mono">${pct(v3Stress.ci_lower_95)}</span></div><div class="detail-item"><b>同向确认 Validation</b><span class="mono">${pct(v3ConfirmedValidation.annualized_net_return_difference)}</span></div><div class="detail-item"><b>同向确认 OOS</b><span class="mono">${pct(v3ConfirmedStress.annualized_net_return_difference)}</span></div></div>
      <div class="notice error"><strong>Validation 结果不能替代 OOS 验收。</strong> 70/30 RIFT 的 2026 收益差为正但 95% 下界小于 0；同向确认候选在 2026 为负，当前不宣称交易增量。</div>
    </div></section>
    <section class="section"><div class="section-header"><div><h2>交易增量验收</h2><p>5 bp 主成本 · 3 个月时间块 Bootstrap · 95% 置信区间</p></div>${badge(backtest.conclusion, backtest.increment_established ? "good" : "bad")}</div><div class="section-body">
      <div class="detail-grid"><div class="detail-item"><b>年化净收益差</b><span class="mono">${pct(bootstrap.annualized_net_return_difference)}</span></div><div class="detail-item"><b>95% 下界</b><span class="mono">${pct(bootstrap.ci_lower_95)}</span></div><div class="detail-item"><b>95% 上界</b><span class="mono">${pct(bootstrap.ci_upper_95)}</span></div><div class="detail-item"><b>Bootstrap 状态</b><span>${esc(bootstrap.conclusion || "not_evaluated")}</span></div></div>
      <div class="notice ${backtest.increment_established ? "good" : "error"}">${backtest.increment_established ? "成本后增量为正且 95% 下界大于 0，交易增量成立。" : "尚未同时满足成本后增量为正且 95% 下界大于 0，交易增量未建立。"}</div>
      <div id="lithiumNavChart" class="strategy-chart"></div>
    </div></section>
    <section class="section"><div class="section-header"><div><h2>前瞻候选 v2</h2><p>Validation-only 冻结 · 不回填已观察的旧 OOS</p></div>${badge(prospective.conclusion || "尚未冻结", prospective.increment_established ? "good" : "warn")}</div><div class="section-body">
      <div class="detail-grid"><div class="detail-item"><b>冻结日期</b><span class="mono">${esc(prospective.frozen_at || "-")}</span></div><div class="detail-item"><b>前瞻起点</b><span class="mono">${esc(prospective.prospective_start || "-")}</span></div><div class="detail-item"><b>已冻结决策</b><span class="mono">${esc(decisionLedger.recorded_decisions || 0)}</span></div><div class="detail-item"><b>已结算决策</b><span class="mono">${esc(decisionLedger.settled_decisions || 0)}</span></div><div class="detail-item"><b>验证期年化收益差</b><span class="mono">${pct(validationBootstrap.annualized_net_return_difference)}</span></div><div class="detail-item"><b>验证期 95% 下界</b><span class="mono">${pct(validationBootstrap.ci_lower_95)}</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>Validation 策略</th><th>观察数</th><th>年化收益</th><th>Sharpe</th><th>最大回撤</th></tr></thead><tbody>${prospectiveRows}</tbody></table></div>
      <div class="notice error"><strong>旧 OOS 压力诊断：${pct(historicalStress.annualized_net_return_difference)}，95% 区间 [${pct(historicalStress.ci_lower_95)}, ${pct(historicalStress.ci_upper_95)}]。</strong> ${esc(prospective.historical_oos_stress_boundary || "该区间不参与选参。")}</div>
      <div class="notice ${prospective.increment_established ? "good" : "warn"}"><strong>前瞻 OOS：${esc(prospectiveBootstrap.observations || 0)} 个交易日。</strong> 仓位只从 append-only 决策账本读取；${esc(prospective.research_boundary || "等待冻结日后的新数据。")}</div>
    </div></section>
    <section class="section"><div class="section-header"><div><h2>OOS 三策略对比</h2><p>同一主力连续、同一执行时点、同一成本</p></div></div><div class="table-wrap"><table><thead><tr><th>策略</th><th>观察数</th><th>年化收益</th><th>年化波动</th><th>Sharpe</th><th>最大回撤</th><th>年换手率</th></tr></thead><tbody>${metricRows}</tbody></table></div></section>
    <section class="section"><div class="section-header"><div><h2>成本敏感性</h2><p>2 bp / 5 bp / 10 bp</p></div></div><div class="table-wrap"><table><thead><tr><th>单边成本</th><th>纯趋势年化</th><th>RIFT 增强年化</th><th>年化差</th></tr></thead><tbody>${costs}</tbody></table></div></section>
    <section class="section"><div class="section-header"><div><h2>受控数据来源</h2><p>网页抓取不是比赛复现的必要依赖</p></div></div><div class="section-body source-links"><a href="${esc(status.official_sources.contract)}" target="_blank" rel="noopener">碳酸锂合约</a><a href="${esc(status.official_sources.daily_market)}" target="_blank" rel="noopener">广期所日行情</a><a href="${esc(status.official_sources.warehouse_receipt)}" target="_blank" rel="noopener">广期所仓单日报</a></div></section>
    <p class="disclaimer standalone">${esc(backtest.disclaimer)}。${esc(backtest.research_boundary)}</p>`;
  refreshIcons();
  renderValidationCharts();
}

function renderValidationCharts() {
  if (!window.Plotly || !$("lithiumNavChart")) return;
  const rows = lithium.backtest?.deepseek_v4_research?.oos_nav || lithium.backtest?.nav || [];
  const colors = { pure_trend: "#71808e", zero_shot_llm: "#bb624c", rift_enhanced_trend: "#116fae" };
  const traces = Object.keys(STRATEGIES).map((strategy) => {
    const selected = rows.filter((row) => row.strategy === strategy);
    return { x: selected.map((row) => row.trade_date), y: selected.map((row) => Number(row.nav)), name: STRATEGIES[strategy], mode: "lines", line: { color: colors[strategy], width: strategy === "rift_enhanced_trend" ? 2.5 : 1.7 } };
  });
  window.Plotly.newPlot("lithiumNavChart", traces, {
    margin: { l: 52, r: 18, t: 28, b: 42 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    hovermode: "x unified", legend: { orientation: "h", y: 1.12 },
    xaxis: { gridcolor: "#edf0f2" }, yaxis: { title: "成本后净值", gridcolor: "#edf0f2" },
    font: { family: "Inter, sans-serif", size: 11, color: "#40505e" },
  }, { responsive: true, displayModeBar: false });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-view]").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); switchView("liveView"); });
  document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => setExample(Number(button.dataset.example))));
  $("toggleKey").addEventListener("click", () => { $("apiKey").type = $("apiKey").type === "password" ? "text" : "password"; });
  $("checkApiButton").addEventListener("click", checkApi);
  $("runButton").addEventListener("click", runAnalysis);
  refreshIcons();
  loadExamples();
  loadStatus();
});
