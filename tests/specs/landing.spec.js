const { test, expect } = require('@playwright/test');

// ═══════════════════════════════════════════
// Landing page & initial state
// ═══════════════════════════════════════════

test.describe('Page Load', () => {

  test('loads successfully', async ({ page }) => {
    const res = await page.goto('/');
    expect(res.status()).toBe(200);
    await expect(page.locator('h1')).toContainText('AlphaLens');
  });

  test('has all input fields', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#it')).toBeVisible();
    await expect(page.locator('#ic')).toBeVisible();
    await expect(page.locator('#is')).toBeVisible();
    await expect(page.locator('#isn')).toBeVisible();
    await expect(page.locator('#id')).toBeVisible();
    await expect(page.locator('#rb')).toBeVisible();
  });

  test('has example buttons', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('储能政策')).toBeVisible();
    await expect(page.getByText('锂电产能')).toBeVisible();
    await expect(page.getByText('光伏新闻')).toBeVisible();
  });

  test('footer shows data scale', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.ft')).toContainText('120条文本');
    await expect(page.locator('.ft')).toContainText('30只新能源股票');
  });
});

test.describe('Auto-load', () => {

  test('auto-loads example on page load', async ({ page }) => {
    await page.goto('/');
    // Wait for auto-load setTimeout(200ms) + analysis
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
    // Should show detected event
    await expect(page.locator('#out')).toContainText('policy_support');
  });

  test('shows tab bar after auto-load', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('#tabbar', { timeout: 5000 });
    await expect(page.locator('#tabbar')).toBeVisible();
    const tabs = page.locator('#tabbar button');
    await expect(tabs).toHaveCount(4);
  });

  test('KPI cards show correct event type', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
    await expect(page.locator('.kpi .v').first()).toContainText('policy_support');
  });
});
