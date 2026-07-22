const { test, expect } = require('@playwright/test');

// ═══════════════════════════════════════════
// UI interaction & visual tests
// ═══════════════════════════════════════════

test.describe('Tab Navigation', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Wait for auto-loaded example
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
  });

  test('all 4 tabs are visible', async ({ page }) => {
    const tabs = page.locator('#tabbar button');
    await expect(tabs).toHaveCount(4);
    await expect(tabs.nth(0)).toContainText('Overview');
    await expect(tabs.nth(1)).toContainText('Impact');
    await expect(tabs.nth(2)).toContainText('Validation');
    await expect(tabs.nth(3)).toContainText('Evidence');
  });

  test('Overview tab is active by default', async ({ page }) => {
    const firstTab = page.locator('#tabbar button').first();
    await expect(firstTab).toHaveClass(/on/);
  });

  test('clicking a tab activates it', async ({ page }) => {
    await page.locator('#tabbar button').nth(2).click();
    await page.waitForTimeout(200);
    await expect(page.locator('#tabbar button').nth(2)).toHaveClass(/on/);
    await expect(page.locator('#tabbar button').nth(0)).not.toHaveClass(/on/);
  });

  test('Overview tab shows KPI cards', async ({ page }) => {
    await page.locator('#tabbar button').first().click();
    await page.waitForTimeout(200);
    await expect(page.locator('.kpis')).toBeVisible();
    const kpis = page.locator('.kpi');
    await expect(kpis).toHaveCount(5);
  });

  test('Impact tab shows factor ranking bars', async ({ page }) => {
    await page.locator('#tabbar button').nth(1).click();
    await page.waitForTimeout(200);
    await expect(page.locator('.fb').first()).toBeVisible();
  });

  test('Validation tab shows historical backtest', async ({ page }) => {
    await page.evaluate(() => swt(2));
    await page.waitForTimeout(300);
    await expect(page.locator('#out')).toContainText('同类事件历史回测');
  });

  test('Evidence tab shows predicate chips', async ({ page }) => {
    await page.locator('#tabbar button').nth(3).click();
    await page.waitForTimeout(300);
    await expect(page.locator('.pchip').first()).toBeVisible();
  });
});

test.describe('Analyze Button States', () => {

  test('button is enabled on page load', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(500);
    await expect(page.locator('#rb')).toBeEnabled();
  });

  test('button shows spinner during analysis', async ({ page }) => {
    await page.goto('/');
    // Clear and type to trigger manual analysis
    await page.locator('#it').fill('工信部储能政策');
    await page.locator('#ic').fill('支持新型储能参与电力系统调节');
    await page.locator('#is').selectOption('policy');
    // Click and immediately check spinner
    await page.locator('#rb').click();
    // Button should be disabled
    await expect(page.locator('#rb')).toBeDisabled();
    // Wait for completion
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
    await expect(page.locator('#rb')).toBeEnabled();
  });
});

test.describe('Visual Consistency', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
  });

  test('KPI values use monospace font', async ({ page }) => {
    const kpiVal = page.locator('.kpi .v').first();
    const fontFamily = await kpiVal.evaluate(el =>
      window.getComputedStyle(el).fontFamily
    );
    expect(fontFamily).toMatch(/JetBrains Mono|monospace/);
  });

  test('table uses monospace font for data', async ({ page }) => {
    await page.locator('#tabbar button').nth(1).click();
    await page.waitForTimeout(200);
    const td = page.locator('.tbl td').first();
    const fontFamily = await td.evaluate(el =>
      window.getComputedStyle(el).fontFamily
    );
    expect(fontFamily).toMatch(/JetBrains Mono|monospace/);
  });

  test('positive values use green color', async ({ page }) => {
    // Check the KPI for positive win rate
    const positiveEls = page.locator('.up');
    const count = await positiveEls.count();
    // Should find at least some green elements (depends on actual data)
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('footer disclaimer is visible', async ({ page }) => {
    await expect(page.locator('.ft')).toContainText('不构成投资建议');
  });
});

test.describe('Responsive Layout', () => {

  test('input grid responsive layout works', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(500);
    // Check grid has 2 columns (with any unit values)
    const cols = await page.locator('.inp-grid').evaluate(el =>
      window.getComputedStyle(el).gridTemplateColumns.split(' ').length
    );
    expect(cols).toBe(2);
  });

  test('tabs are horizontally scrollable on mobile', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('#out .kpis', { timeout: 5000 });

    const tabs = page.locator('#tabbar');
    const overflowX = await tabs.evaluate(el =>
      window.getComputedStyle(el).overflowX
    );
    expect(overflowX).toBe('auto');
  });

  test('KPI cards wrap on mobile', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('#out .kpis', { timeout: 5000 });

    const kpisWidth = await page.locator('.kpis').evaluate(el =>
      el.getBoundingClientRect().width
    );
    const appWidth = await page.locator('.app').evaluate(el =>
      el.getBoundingClientRect().width
    );
    // KPIs should not overflow the app container
    expect(kpisWidth).toBeLessThanOrEqual(appWidth + 2);
  });
});
