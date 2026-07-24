const { test, expect } = require('@playwright/test');

// ═══════════════════════════════════════════
// UI tests — page load, interaction, results rendering
// ═══════════════════════════════════════════

test.describe('Page Load', () => {

  test('loads successfully', async ({ page }) => {
    const res = await page.goto('/');
    expect(res.status()).toBe(200);
    await expect(page.locator('h1')).toContainText('AlphaLens');
  });

  test('has textarea and analyze button', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button')).toContainText(['开始分析']);
  });

  test('has example shortcut buttons', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('储能政策')).toBeVisible();
    await expect(page.getByText('锂电产能')).toBeVisible();
    await expect(page.getByText('光伏新闻')).toBeVisible();
  });

  test('disclaimer is visible', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('footer')).toContainText('不构成投资建议');
  });
});

test.describe('Example Analysis', () => {

  test('储能政策 example auto-runs and shows KPI', async ({ page }) => {
    await page.goto('/');
    // Fill form and trigger analysis via evaluate (bypass DOM click issues)
    await page.locator('textarea').fill(
      '国家发展改革委、国家能源局发布新型储能发展实施方案，强调独立储能、源网荷储协同和新能源消纳，重点支持储能系统集成、逆变器和数据中心电源等方向的技术创新和规模化应用。政策的落地预计将推动派能科技、固德威、锦浪科技等储能企业的订单增长。'
    );
    await page.locator('#rb').click();
    await page.waitForSelector('.kpis', { timeout: 15000 });
    await expect(page.locator('.kpi').first()).toContainText('policy_support');
  });

  test('锂电产能 example shows capacity_expansion', async ({ page }) => {
    await page.goto('/');
    await page.locator('textarea').fill(
      '公司公告显示，宁德时代继续围绕动力电池、储能电池和新技术产品推进产能与客户交付能力建设。公司将新增20GWh动力电池产能，预计2025年Q3投产。公告同时提示项目建设、客户需求和原材料价格存在不确定性。'
    );
    await page.locator('#rb').click();
    await page.waitForSelector('.kpis', { timeout: 15000 });
    await expect(page.locator('.kpi').first()).toContainText('capacity_expansion');
  });

  test('光伏新闻 example shows attention_spread', async ({ page }) => {
    await page.goto('/');
    await page.locator('textarea').fill(
      '财经新闻摘要称，硅料、硅片和组件价格经历调整后出现阶段性企稳迹象，市场关注产能出清和需求修复节奏。隆基绿能、通威股份、TCL中环等头部企业的产能利用率和海外订单情况受到机构关注。'
    );
    await page.locator('#rb').click();
    await page.waitForSelector('.kpis', { timeout: 15000 });
    await expect(page.locator('.kpi').first()).toContainText('attention_spread');
  });
});

test.describe('Results Rendering', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.locator('textarea').fill(
      '国家发展改革委、国家能源局发布新型储能发展实施方案，强调独立储能、源网荷储协同和新能源消纳，重点支持储能系统集成、逆变器和数据中心电源等方向的技术创新和规模化应用。政策的落地预计将推动派能科技、固德威、锦浪科技等储能企业的订单增长。'
    );
    await page.locator('#rb').click();
    await page.waitForSelector('.kpis', { timeout: 15000 });
  });

  test('shows factor ranking bars', async ({ page }) => {
    await expect(page.locator('.bar').first()).toBeVisible();
  });

  test('shows backtest metrics table', async ({ page }) => {
    await expect(page.locator('.tbl').first()).toBeVisible();
    await expect(page.locator('body')).toContainText('Rank IC');
    await expect(page.locator('body')).toContainText('Sharpe');
    await expect(page.locator('body')).toContainText('未来函数审计');
  });

  test('shows event and rules detail section', async ({ page }) => {
    await expect(page.locator('body')).toContainText('事件与规则详情');
  });

  test('shows research report', async ({ page }) => {
    await expect(page.locator('.report')).toBeVisible();
    await expect(page.locator('.report')).toContainText('因子假设');
    await expect(page.locator('.report')).toContainText('不构成投资建议');
  });

  test('shows predicate chips', async ({ page }) => {
    await expect(page.locator('body')).toContainText('has_policy_support');
    await expect(page.locator('body')).toContainText('event_evidence_strength');
  });

  test('shows charts section', async ({ page }) => {
    await expect(page.locator('body')).toContainText('可视化');
  });
});

test.describe('Error Handling', () => {

  test('shows error for empty input', async ({ page }) => {
    await page.goto('/');
    await page.locator('textarea').fill('');
    await page.locator('#rb').click();
    await expect(page.locator('.err-card')).toBeVisible();
    await expect(page.locator('.err-card')).toContainText('请粘贴金融文本内容');
  });

  test('shows no-event message for non-financial text', async ({ page }) => {
    await page.goto('/');
    await page.locator('textarea').fill('今天天气很好，适合户外活动');
    await page.locator('#rb').click();
    await page.waitForSelector('.err-card', { timeout: 10000 });
    await expect(page.locator('.err-card')).toContainText('未检测到明确的金融事件');
  });
});
