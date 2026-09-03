const $ = (id) => document.getElementById(id);
const labels = {down:"收益率下行",flat:"震荡",up:"收益率上行",insufficient:"证据不足"};
const routes = {market_baseline:"市场数据基线",text_only:"仅文本因子",fusion:"市场+文本融合",fusion_rules:"融合+规则增强"};
let state = {status:null,forecast:null,backtest:null,lastDocumentId:""};

function esc(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function pct(value){return value==null?"—":`${(Number(value)*100).toFixed(1)}%`;}
async function json(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);return data;}

document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".nav-item").forEach(item=>item.classList.toggle("active",item===button));
  document.querySelectorAll(".page").forEach(page=>page.classList.toggle("active",page.id===`page-${button.dataset.page}`));
}));

function renderProbabilities(probabilities={}){
  $("probabilityBars").innerHTML=["down","flat","up"].map(label=>`<div class="prob-row ${label}"><span>${labels[label]}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,Number(probabilities[label]||0)*100))}%"></div></div><b>${pct(probabilities[label])}</b></div>`).join("");
}
function renderFactors(factors=[]){
  const rows=factors.map(row=>{const score=Number(row.score||0);const left=50+Math.max(-1,Math.min(1,score))*48;return `<div class="factor-row"><span>${esc(row.label)}</span><div class="factor-axis"><i class="factor-marker" style="left:${left}%"></i></div><b>${score>0?"+":""}${score.toFixed(3)}</b></div>`}).join("");
  $("overviewFactors").innerHTML=rows||"<p class='muted'>当日没有已生效的文本因素。</p>";
  $("factorGrid").innerHTML=factors.map(row=>{const score=Number(row.score||0);return `<div class="factor-card"><span>${esc(row.label)}</span><b style="color:${score>0?'var(--red)':score<0?'var(--teal)':'var(--ink)'}">${score>0?"+":""}${score.toFixed(3)}</b><small>${score>0?'收益率上行压力':score<0?'收益率下行压力':'暂无有效文本信号'}</small></div>`}).join("");
}
function renderAudit(items=[]){
  $("auditList").innerHTML=items.map(item=>`<article class="audit-card"><h3>${esc(item.title||item.doc_id)}</h3><p>${esc(item.source_name||"未知来源")} · 公开时间 ${esc(item.publish_time||"—")} · 生效交易日 ${esc(item.effective_trade_date||"未进入样本")}</p><p>${(item.evidence||[]).map(text=>`<span class="evidence-chip">${esc(text)}</span>`).join("")||"无激活谓词"}</p><p>谓词：${esc((item.active_predicates||[]).join("、")||"无")} · 规则：${esc((item.triggered_rules||[]).join("、")||"无")}</p><p><a href="${esc(item.source_url)}" target="_blank" rel="noreferrer">查看官方来源</a> · SHA-256 ${esc(String(item.source_sha256||"").slice(0,16))}…</p></article>`).join("")||"<div class='notice warn'>尚无政策文本证据。</div>";
}
function renderOverview(){
  const s=state.status,f=state.forecast;if(!s||!f)return;
  $("serviceStatus").textContent=s.data_ready?"官方样例已就绪":"证据不足";
  const enough=f.status==="model_estimate";
  $("overviewNotice").className=`notice ${enough?'good':'warn'}`;
  $("overviewNotice").textContent=enough?`官方样例已通过契约校验；当前为${s.first_trade_date}至${s.latest_trade_date}的单年度MVP。`:`研究证据不足：${f.reason||s.data_errors.join('；')}`;
  $("directionLabel").textContent=labels[f.predicted_label]||"证据不足";
  $("directionSub").textContent=f.bond_price_direction||f.reason||"—";
  $("yieldValue").textContent=f.market_snapshot?`${Number(f.market_snapshot.cgb_10y_yield).toFixed(4)}%`:"—";
  $("drValue").textContent=f.market_snapshot?`${Number(f.market_snapshot.dr007_proxy).toFixed(4)}%`:"—";
  $("drState").textContent=f.market_snapshot?`${f.market_snapshot.dr007_proxy_name} · ${f.market_snapshot.dr007_state}`:"—";
  $("asOfValue").textContent=f.as_of||s.latest_trade_date||"—";$("rowCount").textContent=`${s.market_rows}个交易日 · ${s.text_rows}篇官方文本`;
  $("proxyName").textContent=f.market_snapshot?.dr007_proxy_name||"FDR007_FIXING";$("priceDirection").textContent=f.bond_price_direction||"—";
  $("disclaimer").textContent=f.disclaimer||s.disclaimer;
  renderProbabilities(f.probabilities);renderFactors(f.factor_scores||[]);renderAudit(f.evidence||[]);
}
function renderBacktest(){
  const b=state.backtest;if(!b)return;const ok=b.status==="evaluated";
  $("backtestNotice").className=`notice ${b.increment_established?'good':'warn'}`;
  $("backtestNotice").textContent=ok?`${b.increment_conclusion}。${b.research_warning}`:`研究证据不足：${b.reason}`;
  $("backtestRows").innerHTML=(b.routes||[]).map(row=>`<tr><td>${routes[row.route]||esc(row.route)}</td><td>${row.observations}</td><td>${pct(row.accuracy)}</td><td>${row.macro_f1??'—'}</td><td>${row.brier??'—'}</td><td>${row.route==='fusion_rules'?(b.increment_conclusion||'—'):'对比路线'}</td></tr>`).join("")||"<tr><td colspan='6'>尚无可展示的滚动评估</td></tr>";
  const timeline=(b.routes||[]).find(row=>row.route==="fusion_rules")?.timeline||[];
  $("timeline").innerHTML=timeline.slice(-10).map(row=>`<div class="timeline-item ${row.actual===row.predicted?'correct':'wrong'}"><span>${esc(row.as_of)}</span><b>${labels[row.predicted]}</b><span>实际：${labels[row.actual]}</span><span>标签可用截至 ${esc(row.train_label_observed_end||row.train_end)}</span></div>`).join("")||"<p class='muted'>样本不足，未生成滚动预测。</p>";
}

$("textFile").addEventListener("change",async event=>{const file=event.target.files[0];if(file)$("textContent").value=await file.text();});
$("analysisForm").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;button.textContent="分析中…";$("analysisResult").className="panel result-panel empty-state";$("analysisResult").innerHTML="<div><b>正在抽取文本因素</b><p>验证证据、计算规则并比较加入前后的概率。</p></div>";
  try{
    const payload={title:$("textTitle").value,content:$("textContent").value,source_name:$("sourceName").value,publish_time:$("publishTime").value,source_url:$("sourceUrl").value,api_key:$("apiKey").value};
    const data=await json("/api/rates/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});state.lastDocumentId=data.document.doc_id;
    const active=(data.predicates||[]).filter(row=>row.value);const rulesHtml=(data.triggered_rules||[]).map(row=>`<li><b>${esc(row.rule_id)}</b> ${esc(row.description)}</li>`).join("")||"<li>本篇文本没有触发组合规则。</li>";
    $("analysisResult").className="panel result-panel";$("analysisResult").innerHTML=`<div class="analysis-summary"><span class="kicker">${data.llm_analysis.used?'LLM + RULE VALIDATION':'DETERMINISTIC FALLBACK'}</span><h2>对现有预测的边际影响</h2><p>${esc(data.interpretation)}</p></div><div class="delta-grid">${["down","flat","up"].map(label=>`<div class="delta-card"><span>${labels[label]}</span><b>${pct(data.updated_forecast[label])}</b><small>${Number(data.probability_delta[label])>=0?'+':''}${pct(data.probability_delta[label])}</small></div>`).join('')}</div><div class="notice ${data.llm_analysis.used?'good':'warn'}">${esc(data.llm_analysis.used?'已调用大模型并完成证据校验':data.llm_analysis.reason)}</div><h2>激活谓词与原文证据</h2><div class="predicate-list">${active.map(row=>`<div class="predicate-item"><b>${esc(row.predicate_name)}</b><small>${esc(row.evidence_text)}</small></div>`).join('')||'<p class="muted">没有满足证据门槛的谓词。</p>'}</div><h2>触发规则</h2><ul>${rulesHtml}</ul>`;
  }catch(error){$("analysisResult").innerHTML=`<div><b>分析未完成</b><p>${esc(error.message)}</p></div>`;}finally{button.disabled=false;button.textContent="分析边际影响";}
});

$("saveReview").addEventListener("click",async()=>{
  if(!state.lastDocumentId){$("reviewState").textContent="请先完成一次单文本分析。";return;}
  try{const data=await json("/api/rates/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({document_id:state.lastDocumentId,decision:$("reviewDecision").value,comment:$("reviewComment").value})});$("reviewState").textContent=`复核已保存：${data.review.review_id}`;}catch(error){$("reviewState").textContent=error.message;}
});

async function boot(){
  const now=new Date();now.setMinutes(now.getMinutes()-now.getTimezoneOffset());$("publishTime").value=now.toISOString().slice(0,16);
  try{const [status,forecast,backtest]=await Promise.all([json("/api/rates/status"),json("/api/rates/forecast?horizon=5"),json("/api/rates/backtest")]);state={...state,status,forecast,backtest};renderOverview();renderBacktest();}catch(error){$("serviceStatus").textContent="服务异常";$("overviewNotice").className="notice error";$("overviewNotice").textContent=error.message;}
}
boot();
