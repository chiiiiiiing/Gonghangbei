const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('loads operational workspace and data version', async ({ page }) => {
  await expect(page.locator('.brand')).toContainText('AlphaLens');
  await expect(page.locator('#sourceType')).toBeVisible();
  await expect(page.locator('#sourceName')).toBeVisible();
  await expect(page.locator('#eventDate')).toBeVisible();
  await expect(page.locator('#sourceUrl')).toBeVisible();
  await expect(page.locator('#analysisMode')).toHaveValue('hybrid');
  await expect(page.locator('#apiKey')).toHaveAttribute('type', 'password');
  await expect(page.locator('#aiStatus')).toContainText('AI 研究层待凭证');
  await expect(page.getByRole('tab', { name: '历史研究概览' })).toBeVisible();
  await expect(page.locator('#statusText')).toContainText('正式流水线已连接');
  await expect(page.locator('#dataGrid')).toContainText('130');
  await expect(page.locator('footer')).toContainText('不提供投资建议');
});

test('built-in policy example renders trace, factor and historical scope', async ({ page }) => {
  await page.getByRole('button', { name: '储能政策' }).click();
  await page.locator('#analysisMode').selectOption('rule');
  await page.getByRole('button', { name: '开始分析' }).click();
  await expect(page.locator('#liveView .kpis')).toBeVisible();
  await expect(page.locator('#liveView .kpis')).toContainText('policy_support');
  await expect(page.locator('#liveView')).toContainText('关联实体候选因子');
  await expect(page.locator('#liveView')).toContainText('用户选择规则复现模式');
  await expect(page.locator('body')).toContainText('事件、谓词与规则追溯');
  await expect(page.locator('body')).toContainText('has_policy_support = true');
  await expect(page.locator('.formula-equation')).toContainText('0.7 ×');
  await expect(page.locator('body')).toContainText('固定历史样本用于规则研究与回测');
  await expect(page.locator('.entity-button')).toHaveCount(5);
  await expect(page.getByRole('link', { name: '查看来源正文' })).toHaveAttribute('href', /^https:\/\//);
  await expect(page.locator('.report')).toContainText('不构成投资建议');
});

test('hybrid mode requires a DeepSeek key instead of falling back', async ({ page }) => {
  await page.getByRole('button', { name: '储能政策' }).click();
  await page.getByRole('button', { name: '开始分析' }).click();
  await expect(page.locator('.error')).toContainText('模式一必须填写 DeepSeek API Key');
  await expect(page.locator('#liveView .kpis')).toHaveCount(0);
});

test('historical workspace shows audited backtest, snapshot and rule library', async ({ page }) => {
  await page.getByRole('tab', { name: '历史研究概览' }).click();
  await expect(page.locator('#historyView')).toHaveClass(/active/);
  await expect(page.locator('#historyView')).toContainText('事件因子样本');
  await expect(page.locator('#historyView')).toContainText('最新因子截面');
  await expect(page.locator('#historyView')).toContainText('宁德时代');
  await expect(page.locator('#historyView')).toContainText('冻结规则库');
  await expect(page.locator('#historyView')).toContainText('R001');
  await expect(page.locator('#historyGroupChart')).toBeVisible();
  await expect(page.locator('#historyIcChart')).toBeVisible();
});

test('announcement example exposes uncertainty predicate', async ({ page }) => {
  await page.getByRole('button', { name: '锂电公告' }).click();
  await page.locator('#analysisMode').selectOption('rule');
  await page.getByRole('button', { name: '开始分析' }).click();
  await expect(page.locator('#liveView .kpis')).toContainText('capacity_expansion');
  await expect(page.locator('body')).toContainText('announcement_contains_uncertainty = true');
});

test('empty input reports a useful error', async ({ page }) => {
  await page.getByRole('button', { name: '开始分析' }).click();
  await expect(page.locator('.error')).toContainText('请提供正文内容');
});

test('layout has no horizontal overflow on desktop and mobile', async ({ page }) => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.reload();
    const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
  }
});
