const $ = (id) => document.getElementById(id);
const labels = {down:"收益率下行",flat:"震荡",up:"收益率上行",insufficient:"证据不足"};
// “市场+文本融合”是验收材料中的稳定路线名；结构化宏观为该路线新增输入。
const routes = {market_baseline:"仅市场数据",text_only:"仅文本因子",fusion:"市场+文本融合（含结构化宏观）",fusion_rules:"市场+文本融合+规则增强（含结构化宏观）"};
const periodLabels = {discovery_2018_2022:"发现期 2018—2022",validation_2023_2024:"验证期 2023—2024",retrospective_holdout_2025_latest:"回顾性时间留出 2025—最新"};
const featureLabels = {
  yield_change_1d_bp:"收益率1日变化",yield_change_5d_bp:"收益率5日变化",yield_change_20d_bp:"收益率20日变化",
  yield_volatility_20d_bp:"收益率20日波动",fdr007_level:"FDR007水平",fdr007_change_1d_bp:"FDR007日变化",
  fdr007_gap_20d_bp:"FDR007相对20日均值",rule_pressure:"冻结规则压力",
  text_monetary_policy:"货币政策文本",text_liquidity:"流动性文本",text_growth:"增长文本",
  text_inflation:"通胀文本",text_bond_supply:"债券供给文本",text_risk_appetite:"风险偏好文本",
  structured_cpi_yoy:"CPI同比",structured_ppi_yoy:"PPI同比",structured_pmi_manufacturing:"制造业PMI",
  structured_afre_flow:"社会融资规模增量",structured_afre_rmb_loans:"社融口径人民币贷款",
  structured_afre_government_bonds:"社融口径政府债券",structured_mlf_amount:"MLF操作量",
  structured_mlf_rate:"MLF利率",structured_government_bond_issuance:"政府债计划发行量",
};
let state = {status:null,forecast:null,backtest:null,evidence:[],reviews:[],demoCases:[],lastDocumentId:""};

function esc(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function pct(value,digits=1){return value==null?"—":`${(Number(value)*100).toFixed(digits)}%`;}
function pp(value,digits=2){return value==null?"—":`${(Number(value)*100).toFixed(digits)}个百分点`;}
function fixed(value,digits=3){return value==null?"—":Number(value).toFixed(digits);}
function refreshIcons(){if(window.lucide)window.lucide.createIcons({attrs:{"aria-hidden":"true"}});}
async function json(url,options){const response=await fetch(url,options);const data=await response.json().catch(()=>({error:"服务返回内容无法解析"}));if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);return data;}

document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".nav-item").forEach(item=>item.classList.toggle("active",item===button));
  document.querySelectorAll(".page").forEach(page=>page.classList.toggle("active",page.id===`page-${button.dataset.page}`));
  window.scrollTo({top:0,behavior:"smooth"});
}));

function renderProbabilities(probabilities={}){
  $("probabilityBars").innerHTML=["down","flat","up"].map(label=>`<div class="prob-row ${label}"><span>${labels[label]}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,Number(probabilities[label]||0)*100))}%"></div></div><b>${pct(probabilities[label])}</b></div>`).join("");
}

function probabilityTriplet(probabilities={}){
  return ["down","flat","up"].map(label=>`<span>${labels[label]} <b>${pct(probabilities?.[label],2)}</b></span>`).join("");
}

function deltaTriplet(delta={}){
  return ["down","flat","up"].map(label=>{const value=Number(delta?.[label]||0);return `${labels[label]} ${value>=0?"+":""}${pct(value,2)}`}).join(" / ");
}

function renderProbabilityDecomposition(forecast={}){
  const d=forecast.probability_decomposition||{};
  const stages=[
    ["市场基线",d.market_baseline,"仅使用收益率与资金面"],
    ["叠加文本",d.after_text_overlay,`相对基线：${deltaTriplet(d.text_overlay_delta)}；文本权重 ${pct(forecast.text_overlay_weight,0)}`],
    ["规则调整",d.after_rule_prior,`相对上一步：${deltaTriplet(d.rule_prior_delta)}；规则压力 ${Number(forecast.rule_pressure_applied||0)>=0?"+":""}${fixed(forecast.rule_pressure_applied,3)}`],
  ];
  $("probabilityDecomposition").innerHTML=stages.map(([title,probabilities,note],index)=>`<article class="decomposition-card"><small>步骤 ${index+1}</small><h3>${title}</h3><div>${probabilityTriplet(probabilities||forecast.probabilities)}</div><p>${note}</p></article>`).join("");
}

function renderFactors(factors=[]){
  const status=state.forecast?.factor_evidence_status||{};
  const rows=factors.map(row=>{const score=Number(row.score||0);const insufficient=status[row.name]&&status[row.name]!=="sufficient";const left=50+Math.max(-1,Math.min(1,score))*48;return `<div class="factor-row"><span>${esc(row.label)}</span><div class="factor-axis"><i class="factor-marker" style="left:${left}%"></i></div><b>${insufficient?'证据不足':`${score>0?"+":""}${score.toFixed(3)}`}</b></div>`}).join("");
  $("overviewFactors").innerHTML=rows||"<p class='muted'>当期没有已生效的文本因子。</p>";
  $("factorGrid").innerHTML=factors.map(row=>{const score=Number(row.score||0);const insufficient=status[row.name]&&status[row.name]!=="sufficient";return `<article class="factor-card"><span>${esc(row.label)}</span><b class="${insufficient?'neutral':score>0?'positive':score<0?'negative':'neutral'}">${insufficient?'证据不足':`${score>0?"+":""}${score.toFixed(3)}`}</b><small>${insufficient?'独立事件数不足5个，未进入模型':score>0?'收益率上行压力':score<0?'收益率下行压力':'当前窗口无信号'}</small></article>`}).join("");
}

function contributionHtml(items=[]){
  return items.slice(0,8).map(row=>{const value=Number(row.contribution||0);return `<div class="contribution-row"><span>${esc(featureLabels[row.feature]||row.label||row.feature)}</span><div class="contribution-track"><i class="${value>=0?'positive':'negative'}" style="width:${Math.min(100,Math.abs(value)*500)}%"></i></div><b>${value>=0?'+':''}${value.toFixed(4)}</b></div>`}).join("")||"<p class='muted'>暂无可计算贡献。</p>";
}

function rulesHtml(items=[]){
  return items.slice(-8).map(row=>`<article class="rule-item ${Number(row.yield_direction)>=0?'up':'down'}"><b>${esc(row.rule_id)}</b><span>${esc(row.description)}</span><small>${Number(row.yield_direction)>=0?'收益率上行':'收益率下行'} · 权重${fixed(row.weight,2)}</small></article>`).join("")||"<p class='muted'>当前没有命中冻结规则。</p>";
}

function evidenceHtml(items=[]){
  return items.slice().reverse().slice(0,16).map(item=>{
    const predicates=(item.active_predicates||[]).map(row=>typeof row==="string"?row:row.predicate_name);
    const event=(item.events||[])[0];
    const chain=[`原文 ${item.doc_id}`,event?`事件 ${event.subject}·${event.action}`:"未形成事件",predicates.length?`谓词 ${predicates.join(" / ")}`:"无激活谓词",(item.triggered_rules||[]).length?`规则 ${(item.triggered_rules||[]).map(row=>row.rule_id).join(" / ")}`:"未命中规则","5日方向预测"];
    return `<article class="audit-card"><div class="audit-head"><div><span>${esc(item.source_name)}</span><h3>${esc(item.title||item.doc_id)}</h3></div><time>${esc(item.publish_time||"")}</time></div><div class="evidence-chain">${chain.map((step,index)=>`<span>${index+1}. ${esc(step)}</span>`).join("")}</div><p>生效交易日：${esc(item.effective_trade_date||"未进入当前样本")}</p><p><a href="${esc(item.source_url)}" target="_blank" rel="noreferrer">官方原文</a> · SHA-256 ${esc(String(item.source_sha256||"").slice(0,16))}…</p></article>`;
  }).join("")||"<div class='notice warn'>尚无政策文本证据。</div>";
}

function renderOverview(){
  const s=state.status,f=state.forecast;if(!s||!f)return;
  $("serviceStatus").textContent=s.data_ready?"官方数据已就绪":"证据不足";
  const enough=f.status==="model_estimate";
  const insufficient=Object.entries(f.factor_evidence_status||{}).filter(([,value])=>value!=="sufficient").map(([name])=>name).join("、");
  $("overviewNotice").className=`notice ${enough?'good':'warn'}`;
  $("overviewNotice").textContent=enough?`${s.first_trade_date}至${s.latest_trade_date}，${s.market_rows}个交易日、${s.text_rows}篇去重政策文本、${s.structured_rows||0}条结构化观测；${insufficient?`部分因子证据不足（${insufficient}）；`:""}${state.backtest?.increment_conclusion||'评估中'}。`:`研究证据不足：${f.reason||(s.data_errors||[]).join('；')}`;
  $("directionLabel").textContent=labels[f.predicted_label]||"证据不足";
  $("directionSub").textContent=f.bond_price_direction||f.reason||"—";
  $("yieldValue").textContent=f.market_snapshot?`${Number(f.market_snapshot.cgb_10y_yield).toFixed(4)}%`:"—";
  $("drValue").textContent=f.market_snapshot?`${Number(f.market_snapshot.dr007_proxy).toFixed(4)}%`:"—";
  $("drState").textContent=f.market_snapshot?`${f.market_snapshot.dr007_proxy_name} · ${f.market_snapshot.dr007_state}`:"—";
  $("asOfValue").textContent=f.as_of||s.latest_trade_date||"—";
  $("rowCount").textContent=`${s.market_rows}个交易日 · ${s.text_rows}篇文本 · ${s.structured_rows||0}条结构化观测`;
  $("proxyName").textContent=f.market_snapshot?.dr007_proxy_name||"FDR007_FIXING";
  $("priceDirection").textContent=f.bond_price_direction||"—";
  $("modelWindow").textContent=f.train_observations?`${f.train_observations}个训练观测`:"滚动训练";
  $("ruleVersion").textContent=s.rule_version||"—";$("auditRuleVersion").textContent=s.rule_version||"—";
  $("dedupState").textContent=`已剔除${s.deduplicated_text_rows||0}篇重复`;
  $("llmCoverage").textContent=`${s.llm_usable_annotations||0}/${s.text_rows} · ${pct(s.llm_coverage)}`;
  $("sourceState").textContent=(s.data_errors||[]).length?"存在数据错误":"URL + SHA-256通过";
  $("disclaimer").textContent=f.disclaimer||s.disclaimer;
  renderProbabilities(f.probabilities);renderFactors(f.factor_scores||[]);
  renderProbabilityDecomposition(f);
  $("contributionList").innerHTML=contributionHtml(f.feature_contributions||[]);
  $("forecastContributions").innerHTML=contributionHtml(f.feature_contributions||[]);
  $("overviewRules").innerHTML=rulesHtml(f.triggered_rules||[]);
  $("forecastRules").innerHTML=rulesHtml(f.triggered_rules||[]);
}

function renderAudit(){
  $("auditList").innerHTML=evidenceHtml(state.evidence||[]);
  $("reviewHistory").innerHTML=(state.reviews||[]).slice().reverse().map(row=>`<div class="review-row"><span>${esc(row.reviewed_at)}</span><b>${esc(row.decision)}</b><span>${esc(row.reviewer||"")}</span><p>${esc(row.comment||"无备注")}</p></div>`).join("")||"<p class='muted padded'>尚无人工复核记录。</p>";
}

function renderBacktest(){
  const b=state.backtest;if(!b)return;const ok=b.status==="evaluated";
  $("backtestNotice").className=`notice ${b.increment_established?'good':'warn'}`;
  const boot=b.holdout_increment_bootstrap||{};
  const diagnostics=b.enhancement_diagnostics||{};const textEffect=diagnostics.text_overlay||{};const ruleEffect=diagnostics.rule_prior||{};
  $("backtestNotice").textContent=ok?`${b.increment_conclusion}。文本叠加相对市场基线：准确率${Number(textEffect.accuracy_difference_vs_market||0)>=0?'+':''}${pp(textEffect.accuracy_difference_vs_market)}、Macro-F1 ${Number(textEffect.macro_f1_difference_vs_market||0)>=0?'+':''}${fixed(textEffect.macro_f1_difference_vs_market,4)}；规则在${ruleEffect.active_observations||0}个留出观测中平均改变概率${pct(ruleEffect.mean_total_variation_probability_change)}，改变${ruleEffect.changed_predictions||0}次最终分类。最终准确率差Bootstrap 95%区间[${pct(boot.ci_lower_95)}, ${pct(boot.ci_upper_95)}]。${b.research_warning}`:`研究证据不足：${b.reason}`;
  $("backtestRows").innerHTML=(b.routes||[]).map(row=>`<tr><td><b>${routes[row.route]||esc(row.route)}</b></td><td>${row.observations}</td><td>${pct(row.accuracy)}</td><td>${pct(row.macro_precision)}</td><td>${pct(row.macro_recall)}</td><td>${fixed(row.macro_f1)}</td><td>${fixed(row.macro_auc_ovr)}</td><td>${fixed(row.brier)}</td></tr>`).join("")||"<tr><td colspan='8'>尚无滚动评估</td></tr>";
  const enhanced=(b.routes||[]).find(row=>row.route==="fusion_rules");
  $("periodMetrics").innerHTML=(enhanced?.period_metrics||[]).map(row=>`<div class="compact-row"><span>${esc(periodLabels[row.period]||row.period.replaceAll('_',' '))}</span><b>${row.observations}期</b><span>Acc ${pct(row.accuracy)}</span><span>F1 ${fixed(row.macro_f1)}</span><span>AUC ${fixed(row.macro_auc_ovr)}</span></div>`).join("")||"<p class='muted padded'>暂无分期结果。</p>";
  $("calibration").innerHTML=(enhanced?.calibration||[]).map(row=>`<div class="calibration-row"><span>${esc(row.confidence_range)}</span><div><i style="width:${Number(row.mean_confidence)*100}%"></i><em style="left:${Number(row.accuracy)*100}%"></em></div><b>${row.observations}期</b><small>置信${pct(row.mean_confidence)} / 准确${pct(row.accuracy)}</small></div>`).join("")||"<p class='muted padded'>暂无校准结果。</p>";
  const timeline=enhanced?.timeline||[];
  $("timeline").innerHTML=timeline.slice(-10).map(row=>`<div class="timeline-item ${row.correct?'correct':'wrong'}"><span>${esc(row.as_of)}</span><b>${labels[row.predicted]}</b><span>实际：${labels[row.actual]}</span><small>训练源截至 ${esc(row.train_origin_end)}</small></div>`).join("")||"<p class='muted'>样本不足。</p>";
  const examples=enhanced?.examples||{};
  $("caseComparison").innerHTML=[["典型正确",examples.correct||[],"correct"],["典型错误",examples.incorrect||[],"wrong"]].map(([title,rows,kind])=>`<div><h3>${title}</h3>${rows.map(row=>`<article class="case-row ${kind}"><time>${esc(row.as_of)}</time><b>预测${labels[row.predicted]} / 实际${labels[row.actual]}</b><span>最高概率 ${pct(Math.max(...Object.values(row.probabilities||{})))}</span></article>`).join('')||'<p class="muted">暂无案例</p>'}</div>`).join("");
}

function populateDemoCases(){
  $("demoCase").innerHTML='<option value="">固定演示案例</option>'+state.demoCases.map((row,index)=>`<option value="${index}">${esc(row.title)}</option>`).join('');
}

$("demoCase").addEventListener("change",event=>{const row=state.demoCases[Number(event.target.value)];if(!row)return;$("textTitle").value=row.title;$("textContent").value=row.content;$("sourceName").value=row.source_name;$("publishTime").value=row.publish_time.slice(0,16);$("sourceUrl").value=row.source_url;});
$("textFile").addEventListener("change",async event=>{
  const file=event.target.files[0];if(!file)return;
  const formData=new FormData();formData.append("file",file);
  try{
    const data=await json("api/rates/extract-file",{method:"POST",body:formData});
    $("textContent").value=data.content;
    if(!$("textTitle").value.trim())$("textTitle").value=file.name.replace(/\.[^.]+$/,"");
  }catch(error){$("analysisResult").innerHTML=`<div><b>文件导入失败</b><p>${esc(error.message)}</p></div>`;}
});

$("analysisForm").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;button.innerHTML='<span>分析中…</span>';$("analysisResult").className="panel result-panel empty-state";$("analysisResult").innerHTML="<div><b>正在构建证据链</b><p>事件抽取、谓词校验、规则匹配与概率比较。</p></div>";
  try{
    const payload={title:$("textTitle").value,content:$("textContent").value,source_name:$("sourceName").value,publish_time:$("publishTime").value,source_url:$("sourceUrl").value,api_key:$("apiKey").value};
    const data=await json("api/rates/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});state.lastDocumentId=data.document.doc_id;
    const active=(data.predicates||[]).filter(row=>row.value);const events=data.events||[];
    $("analysisResult").className="panel result-panel";$("analysisResult").innerHTML=`
      <div class="analysis-summary"><span class="kicker">${data.llm_analysis.used?'LLM + EVIDENCE GATE':'DETERMINISTIC FALLBACK'}</span><h2>加入前后预测</h2><p>${esc(data.interpretation)}</p></div>
      <div class="delta-grid">${["down","flat","up"].map(label=>`<div class="delta-card"><span>${labels[label]}</span><b>${pct(data.updated_forecast[label])}</b><small>${Number(data.probability_delta[label])>=0?'+':''}${pct(data.probability_delta[label])}</small><em>加入前 ${pct(data.baseline_forecast[label])}</em></div>`).join('')}</div>
      <div class="notice ${data.llm_analysis.used?'good':'warn'}">${esc(data.llm_analysis.used?'大模型输出已完成原文证据校验':data.llm_analysis.reason)}</div>
      <h2>原文 → 事件</h2><div class="event-list">${events.map(row=>`<article><b>${esc(row.subject)} · ${esc(row.action)} · ${esc(row.object)}</b><span>${esc(row.transmission_channel||'')} / ${esc(row.horizon||'')}</span><q>${esc(row.evidence_text)}</q></article>`).join('')||'<p class="muted">没有通过证据门槛的事件。</p>'}</div>
      <h2>事件 → 谓词 → 因子</h2><div class="predicate-list">${active.map(row=>`<div class="predicate-item"><b>${esc(row.predicate_name)}</b><span>${esc(row.factor)} · ${row.yield_direction>0?'收益率上行':'收益率下行'}</span><small>${esc(row.evidence_text)}</small></div>`).join('')||'<p class="muted">没有激活谓词。</p>'}</div>
      <h2>命中规则</h2><div class="rule-list">${rulesHtml(data.triggered_rules||[])}</div>`;
    refreshIcons();
  }catch(error){$("analysisResult").innerHTML=`<div><b>分析未完成</b><p>${esc(error.message)}</p></div>`;}finally{button.disabled=false;button.innerHTML='<i data-lucide="scan-text"></i><span>分析边际影响</span>';refreshIcons();}
});

$("saveReview").addEventListener("click",async()=>{
  if(!state.lastDocumentId){$("reviewState").textContent="请先完成一次单文本分析。";return;}
  try{const data=await json("api/rates/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({document_id:state.lastDocumentId,decision:$("reviewDecision").value,reviewer:$("reviewer").value,comment:$("reviewComment").value})});state.reviews.push(data.review);renderAudit();$("reviewState").textContent=`复核已追加：${data.review.review_id}`;}catch(error){$("reviewState").textContent=error.message;}
});

async function boot(){
  const now=new Date();now.setMinutes(now.getMinutes()-now.getTimezoneOffset());$("publishTime").value=now.toISOString().slice(0,16);
  try{
    const [status,forecast,backtest,evidence,reviews,demos]=await Promise.all([
      json("api/rates/status"),json("api/rates/forecast?horizon=5"),json("api/rates/backtest"),
      json("api/rates/evidence?limit=100"),json("api/rates/reviews"),json("api/rates/demo-cases"),
    ]);
    state={...state,status,forecast,backtest,evidence:evidence.documents||[],reviews:reviews.reviews||[],demoCases:demos.cases||[]};
    renderOverview();renderAudit();renderBacktest();populateDemoCases();refreshIcons();
  }catch(error){$("serviceStatus").textContent="服务异常";$("overviewNotice").className="notice error";$("overviewNotice").textContent=error.message;}
}

refreshIcons();boot();
