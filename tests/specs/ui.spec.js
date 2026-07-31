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
  await expect(page.locator('#statusText')).toContainText('正式流水线已连接');
  await expect(page.locator('#dataGrid')).toContainText('130');
  await expect(page.locator('footer')).toContainText('不提供投资建议');
});

test('built-in policy example renders trace, factor and historical scope', async ({ page }) => {
  await page.getByRole('button', { name: '储能政策' }).click();
  await page.getByRole('button', { name: '开始分析' }).click();
  await expect(page.locator('.kpis')).toBeVisible();
  await expect(page.locator('.kpis')).toContainText('policy_support');
  await expect(page.locator('body')).toContainText('候选因子排名');
  await expect(page.locator('body')).toContainText('事件、谓词与规则追溯');
  await expect(page.locator('body')).toContainText('has_policy_support = true');
  await expect(page.locator('body')).toContainText('固定历史样本，不是本次文本的单次回测');
  await expect(page.locator('#groupChart')).toBeVisible();
  await expect(page.locator('#icChart')).toBeVisible();
  await expect(page.locator('.report')).toContainText('不构成投资建议');
});

test('announcement example exposes uncertainty predicate', async ({ page }) => {
  await page.getByRole('button', { name: '锂电公告' }).click();
  await page.getByRole('button', { name: '开始分析' }).click();
  await expect(page.locator('.kpis')).toContainText('capacity_expansion');
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
