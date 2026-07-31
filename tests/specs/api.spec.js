const { test, expect } = require('@playwright/test');

const EXAMPLES = {
  policy: {
    title: '关于印发《新型储能规模化建设专项行动方案（2025—2027年）》的通知',
    content: '原文摘要：为推动新型储能高质量发展，国家发展改革委、国家能源局研究制定了《新型储能规模化建设专项行动方案（2025—2027年）》。现予印发，请结合实际认真抓好贯彻落实。',
    source_type: 'policy', source_name: '中国政府网', event_date: '2025-08-27', source_url: 'https://www.gov.cn/zhengce/zhengceku/202509/content_7040296.htm'
  },
  announcement: {
    title: '上海璞泰来新能源科技集团股份有限公司关于投资建设年产72亿平方米锂离子电池隔膜建设项目的公告',
    content: '原文摘要：璞泰来披露年产72亿平方米锂离子电池隔膜建设项目，计划总投资56亿元人民币。重要内容提示：交易实施尚需履行审批及其他相关程序。',
    source_type: 'announcement', source_name: '巨潮资讯网', event_date: '2026-05-21', source_url: 'http://static.cninfo.com.cn/finalpage/2026-05-21/1225319446.PDF'
  },
  news: {
    title: '315GW+119GW！2025年光伏、风电年新增装机再创新高',
    content: '原文摘要：国家能源局发布2025年全国电力统计数据，光伏、风电年新增装机量分别达到315GW和119GW。截至2025年底，全国累计发电装机容量38.9亿千瓦，同比增长16.1%；太阳能发电装机容量12.0亿千瓦，同比增长35.4%，风电装机容量6.4亿千瓦，同比增长22.9%。',
    source_type: 'news', source_name: '腾讯新闻', event_date: '2026-01-28', source_url: 'https://news.qq.com/rain/a/20260128A043VK00'
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
  const formula = body.stock_results[0].factor_formula;
  expect(formula.result).toBeCloseTo(
    formula.rule_score_sum * (
      formula.evidence_weight * formula.evidence_strength
      + formula.impact_weight * formula.impact_prior
    ),
    5
  );
  expect(body.historical_backtest.scope).toBe('historical_reference_only');
  expect(body.report).toContain('并非对本次新文本单独回测');
  expect(body.report).toContain('本报告仅供研究参考，不构成投资建议');
});

test('announcement grounds uncertainty predicate', async ({ request }) => {
  const { response, body } = await post(request, EXAMPLES.announcement);
  expect(response.ok()).toBeTruthy();
  expect(body.event_type).toBe('capacity_expansion');
  expect(body.stock_results.map(row => row.name)).toContain('璞泰来');
  const predicate = body.stock_results[0].predicates.find(row => row.name === 'announcement_contains_uncertainty');
  expect(predicate.value).toBe(true);
});

test('historical factor snapshot keeps event and rule trace ids', async ({ request }) => {
  const response = await request.get('/api/backtest');
  const body = await response.json();
  expect(body.factor_snapshot).toHaveLength(30);
  expect(body.factor_snapshot[0].trigger_event_ids).toMatch(/^E\d+/);
  expect(body.factor_snapshot[0].trigger_rule_ids).toMatch(/^R\d+/);
});

test('news text produces attention event and quantitative attention predicate', async ({ request }) => {
  const { response, body } = await post(request, EXAMPLES.news);
  expect(response.ok()).toBeTruthy();
  expect(body.event_type).toBe('attention_spread');
  expect(body.stock_results.map(row => row.name)).toEqual(expect.arrayContaining(['隆基绿能', '通威股份', 'TCL中环']));
  const predicate = body.stock_results[0].predicates.find(row => row.name === 'social_attention_spikes');
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
