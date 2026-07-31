const { test, expect } = require('@playwright/test');

const EXAMPLES = {
  policy: {
    title: '新型储能参与电力系统调节能力建设方案',
    content: '国家发展改革委、国家能源局发布新型储能发展实施方案，强调独立储能、源网荷储协同和新能源消纳，重点支持储能系统集成、逆变器等方向的技术创新和规模化应用。政策后续将开展试点示范并推动市场交易机制建设。',
    source_type: 'policy', source_name: '国家能源局', event_date: '2025-02-15', source_url: 'https://www.nea.gov.cn/'
  },
  announcement: {
    title: '宁德时代披露动力电池产能规划进展',
    content: '公司公告显示，宁德时代计划新增产能20GWh，预计2025年第三季度投产。公告同时提示项目建设、客户需求和原材料价格存在不确定性。',
    source_type: 'announcement', source_name: '宁德时代公告', event_date: '2025-03-20', source_url: 'https://www.cninfo.com.cn/'
  },
  news: {
    title: '光伏产业链价格出现阶段性企稳迹象',
    content: '财经新闻摘要称，市场关注产能出清和需求修复节奏。隆基绿能、通威股份、TCL中环等企业受到机构关注，光伏装机量保持增长。',
    source_type: 'news', source_name: '证券时报', event_date: '2025-04-18', source_url: 'https://www.stcn.com/'
  }
};

async function post(request, data) {
  const response = await request.post('/api/analyze', { data });
  return { response, body: await response.json() };
}

test('status exposes the merged official dataset', async ({ request }) => {
  const response = await request.get('/api/status');
  const data = await response.json();
  expect(response.ok()).toBeTruthy();
  expect(data.pipeline_mode).toBe('official-shared-functions');
  expect(data.counts.stocks).toBe(30);
  expect(data.counts.documents).toBe(130);
  expect(data.counts.events).toBe(210);
  expect(data.counts.qualified_rules).toBe(12);
  expect(data.adj_factor_placeholder).toBe(true);
  expect(data.disclaimer).toContain('不构成投资建议');
});

test('historical endpoint returns audited files without invented metrics', async ({ request }) => {
  const response = await request.get('/api/backtest');
  const data = await response.json();
  expect(data.scope).toBe('historical_reference_only');
  expect(data.metrics.event_factor_sample_count).toBe(167);
  expect(data.metrics.future_info_audit).toBe('pass');
  expect(data.group_returns).toHaveLength(5);
  expect(data.rank_ic_timeseries.length).toBeGreaterThan(0);
  expect(data.metrics.sharpe_ratio).toBeUndefined();
});

test('policy text reuses official predicates and frozen rules', async ({ request }) => {
  const { response, body } = await post(request, EXAMPLES.policy);
  expect(response.ok()).toBeTruthy();
  expect(body.event_type).toBe('policy_support');
  expect(body.stock_results.length).toBeGreaterThan(0);
  expect(body.stock_results[0].predicates).toHaveLength(19);
  expect(body.triggered_rules.length).toBeGreaterThan(0);
  expect(body.stock_results[0].candidate_factor).toBeGreaterThan(0);
  expect(body.historical_backtest.scope).toBe('historical_reference_only');
  expect(body.report).toContain('并非对本次新文本单独回测');
  expect(body.report).toContain('本报告仅供研究参考，不构成投资建议');
});

test('announcement grounds uncertainty predicate', async ({ request }) => {
  const { response, body } = await post(request, EXAMPLES.announcement);
  expect(response.ok()).toBeTruthy();
  expect(body.event_type).toBe('capacity_expansion');
  expect(body.stock_results.map(row => row.name)).toContain('宁德时代');
  const predicate = body.stock_results[0].predicates.find(row => row.name === 'announcement_contains_uncertainty');
  expect(predicate.value).toBe(true);
});

test('news text produces attention event and institutional predicate', async ({ request }) => {
  const { response, body } = await post(request, EXAMPLES.news);
  expect(response.ok()).toBeTruthy();
  expect(body.event_type).toBe('attention_spread');
  expect(body.stock_results.map(row => row.name)).toEqual(expect.arrayContaining(['隆基绿能', '通威股份', 'TCL中环']));
  const predicate = body.stock_results[0].predicates.find(row => row.name === 'institutional_attention_increases');
  expect(predicate.value).toBe(true);
});

test('rejects missing provenance and future dates', async ({ request }) => {
  const missing = await post(request, { content: '国家能源局发布储能实施方案', source_type: 'policy', event_date: '2025-01-01' });
  expect(missing.response.status()).toBe(400);
  expect(missing.body.error).toContain('来源名称');
  const future = await post(request, { ...EXAMPLES.policy, event_date: '2099-01-01' });
  expect(future.response.status()).toBe(400);
  expect(future.body.error).toContain('不能晚于今天');
});

test('single IR question is not promoted to pressure event', async ({ request }) => {
  const { response, body } = await post(request, {
    title: '投资者提问', content: '投资者提问宁德时代动力电池产能进展，公司回答以公告为准。',
    source_type: 'ir_qa', source_name: '深交所互动易', event_date: '2025-03-01', source_url: 'https://irm.cninfo.com.cn/'
  });
  expect(response.status()).toBe(422);
  expect(body.error).toContain('未检测到明确');
});
