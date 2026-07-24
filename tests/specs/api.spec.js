const { test, expect } = require('@playwright/test');

// ═══════════════════════════════════════════
// Direct API tests — verify pipeline correctness
// ═══════════════════════════════════════════

async function analyze(page, content, opts = {}) {
  const resp = await page.request.post('/api/analyze', {
    data: { content, source_type: opts.source_type || 'auto', ...opts }
  });
  return resp.json();
}

test.describe('API: 3 built-in examples', () => {

  test('储能政策 → policy_support with 储能 stocks', async ({ page }) => {
    const d = await analyze(page,
      '国家发展改革委、国家能源局发布新型储能发展实施方案，强调独立储能、源网荷储协同和新能源消纳，重点支持储能系统集成、逆变器和数据中心电源等方向的技术创新和规模化应用。政策的落地预计将推动派能科技、固德威、锦浪科技等储能企业的订单增长，科华数据的数据中心电源业务也将受益于新型储能应用场景的扩围。',
      { source_type: 'policy' }
    );
    expect(d.event_type).toBe('policy_support');
    const names = d.stock_results.map(s => s.name);
    expect(names).toContain('派能科技');
    expect(names).toContain('固德威');
    expect(names).toContain('锦浪科技');
    expect(d.stock_results.length).toBeGreaterThanOrEqual(3);
    // Backtest
    expect(d.backtest.factor_sample_count).toBeGreaterThan(0);
    expect(d.backtest.group_returns).toHaveLength(5);
    expect(Object.keys(d.backtest.factor_decay).length).toBeGreaterThanOrEqual(2);
    // Report
    expect(d.report.length).toBeGreaterThan(500);
    expect(d.report).toContain('policy_support');
  });

  test('锂电产能 → capacity_expansion with 宁德时代', async ({ page }) => {
    const d = await analyze(page,
      '公司公告显示，宁德时代继续围绕动力电池、储能电池和新技术产品推进产能与客户交付能力建设。公司将新增20GWh动力电池产能，预计2025年Q3投产。公告同时提示项目建设、客户需求和原材料价格存在不确定性。',
      { source_type: 'announcement' }
    );
    expect(d.event_type).toBe('capacity_expansion');
    const names = d.stock_results.map(s => s.name);
    expect(names).toContain('宁德时代');
    // announcement_contains_uncertainty should be true
    const preds = d.stock_results[0]?.predicates || [];
    const un = preds.find(p => p.name === 'announcement_contains_uncertainty');
    expect(un?.value).toBe(true);
  });

  test('光伏新闻 → attention_spread with 光伏 stocks', async ({ page }) => {
    const d = await analyze(page,
      '财经新闻摘要称，硅料、硅片和组件价格经历调整后出现阶段性企稳迹象，市场关注产能出清和需求修复节奏。隆基绿能、通威股份、TCL中环等头部企业的产能利用率和海外订单情况受到机构关注。光伏装机需求持续增长，行业供需格局有望改善。',
      { source_type: 'news' }
    );
    expect(d.event_type).toBe('attention_spread');
    const names = d.stock_results.map(s => s.name);
    expect(names).toContain('隆基绿能');
    expect(names).toContain('通威股份');
    expect(names).toContain('TCL中环');
    // social_attention_spikes should be true for attention_spread
    const preds = d.stock_results[0]?.predicates || [];
    const sa = preds.find(p => p.name === 'social_attention_spikes');
    expect(sa?.value).toBe(true);
    // institutional_attention_increases should be true (mentions 机构关注)
    const ia = preds.find(p => p.name === 'institutional_attention_increases');
    expect(ia?.value).toBe(true);
  });
});

test.describe('API: Edge cases', () => {

  test('no event for generic non-financial text', async ({ page }) => {
    const d = await analyze(page, '今天天气很好，适合户外活动', { source_type: 'news' });
    expect(d.error).toContain('未检测到明确的金融事件');
  });

  test('no event for empty content', async ({ page }) => {
    const d = await analyze(page, '');
    expect(d.error).toBeTruthy();
  });

  test('handles text with special characters', async ({ page }) => {
    const d = await analyze(page,
      '【重磅】工信部发布《新型储能"十四五"行动方案》！强调独立储能、源网荷储协同。支持派能科技的储能系统创新。',
      { source_type: 'policy' }
    );
    expect(d.event_type).toBe('policy_support');
  });

  test('sector-level matching for policy with explicit keyword', async ({ page }) => {
    const d = await analyze(page,
      '国家能源局发布风电产业补贴政策实施方案，推动海上风电项目建设，提升可再生能源消纳能力。',
      { source_type: 'policy' }
    );
    // '实施方案' matches POLICY_KW
    expect(d.event_type).toBe('policy_support');
    expect(d.stock_results.length).toBeGreaterThan(0);
  });

  test('ir_qa source returns no event', async ({ page }) => {
    const d = await analyze(page,
      '投资者提问宁德时代动力电池产能进展，公司回复以公告为准。',
      { source_type: 'ir_qa' }
    );
    // ir_qa always returns null for event type
    expect(d.error || d.event_type === null).toBeTruthy();
  });
});

test.describe('API: Backtest metrics', () => {

  test('all required backtest fields present', async ({ page }) => {
    const d = await analyze(page,
      '国家发改委发布新型储能实施方案，支持储能技术创新和规模化应用，推动派能科技等企业订单增长。',
      { source_type: 'policy' }
    );
    const bt = d.backtest;
    expect(bt).toBeTruthy();
    // Required fields
    expect(typeof bt.factor_sample_count).toBe('number');
    expect(typeof bt.avg_rank_ic).toBe('number');
    expect(typeof bt.icir).toBe('number');
    expect(Array.isArray(bt.group_returns)).toBe(true);
    expect(typeof bt.g5_g1_spread).toBe('number');
    expect(typeof bt.sharpe_ratio).toBe('number');
    expect(typeof bt.max_drawdown).toBe('number');
    expect(typeof bt.positive_return_rate).toBe('number');
    expect(typeof bt.future_function_audit).toBe('string');
    expect(typeof bt.train_period).toBe('object');
    expect(typeof bt.test_period).toBe('object');
    expect(typeof bt.trading_cost_note).toBe('string');
    expect(bt.trading_cost_note.length).toBeGreaterThan(10);
    // Group returns have 5 groups
    expect(bt.group_returns).toHaveLength(5);
    for (const g of bt.group_returns) {
      expect(g.group).toMatch(/G[1-5]/);
      expect(typeof g.avg_return_5d).toBe('number');
    }
    // Factor decay
    const decay = bt.factor_decay;
    expect(decay['5d'] || decay['10d'] || decay['20d']).toBeTruthy();
  });

  test('report has all required sections', async ({ page }) => {
    const d = await analyze(page,
      '国家发改委发布新型储能实施方案支持派能科技。',
      { source_type: 'policy' }
    );
    expect(d.report).toContain('因子假设');
    expect(d.report).toContain('数据来源');
    expect(d.report).toContain('回测指标');
    expect(d.report).toContain('数据和方法限制');
    expect(d.report).toContain('不构成投资建议');
  });
});
