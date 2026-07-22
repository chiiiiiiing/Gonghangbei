const { test, expect } = require('@playwright/test');

// ═══════════════════════════════════════════
// Pipeline output verification for all 3 examples
// ═══════════════════════════════════════════

async function loadExample(page, index) {
  const buttons = page.locator('.btn-gh');
  await buttons.nth(index).click();
  await page.waitForSelector('#out .kpis', { timeout: 5000 });
}

test.describe('Example 1: 储能政策 (policy_support)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await loadExample(page, 0);
  });

  test('detects policy_support event', async ({ page }) => {
    await expect(page.locator('.kpi .v').first()).toContainText('policy_support');
  });

  test('links 4 储能 stocks', async ({ page }) => {
    const kpi = page.locator('.kpi').nth(1);
    await expect(kpi.locator('.v')).toContainText('4');
  });

  test('triggers R002 rule', async ({ page }) => {
    // Switch to Impact tab to see rule triggers
    await page.locator('#tabbar button').nth(1).click();
    await page.waitForTimeout(200);
    // Factor bar should show R002
    await expect(page.locator('.fb .fr').first()).toContainText('R002');
  });

  test('shows predicate chips in Evidence tab', async ({ page }) => {
    await page.locator('#tabbar button').nth(3).click();
    await page.waitForTimeout(200);
    // has_policy_support should be true
    const chips = page.locator('.pchip');
    await expect(chips.first()).toBeVisible();
    // Find the has_policy_support chip
    await expect(page.locator('.pchip.on').first()).toContainText('has_policy_support');
  });

  test('shows historical backtest in Validation tab', async ({ page }) => {
    await page.locator('#tabbar button').nth(2).click();
    await page.waitForTimeout(200);
    await expect(page.locator('#out')).toContainText('policy_support');
    await expect(page.locator('#out')).toContainText('26'); // n from EH
  });
});

test.describe('Example 2: 锂电产能 (capacity_expansion)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await loadExample(page, 1);
  });

  test('detects capacity_expansion event', async ({ page }) => {
    await expect(page.locator('.kpi .v').first()).toContainText('capacity_expansion');
  });

  test('links 宁德时代 with high confidence', async ({ page }) => {
    await page.locator('#tabbar button').nth(1).click();
    await page.waitForTimeout(200);
    // Entity table should show 宁德时代
    await expect(page.locator('.tbl')).toContainText('宁德时代');
    await expect(page.locator('.tbl')).toContainText('300750');
    // Confidence should be high (name in title)
    await expect(page.locator('.tbl')).toContainText('98%');
  });

  test('triggers R002 and R003', async ({ page }) => {
    await page.locator('#tabbar button').nth(1).click();
    await page.waitForTimeout(200);
    const triggers = page.locator('.fb .fr').first();
    await expect(triggers).toContainText('R002');
    await expect(triggers).toContainText('R003');
  });

  test('announcement_contains_uncertainty is true', async ({ page }) => {
    await page.locator('#tabbar button').nth(3).click();
    await page.waitForTimeout(200);
    const chip = page.locator('.pchip.on').filter({ hasText: 'announcement_contains_uncertainty' });
    await expect(chip).toBeVisible();
  });
});

test.describe('Example 3: 光伏新闻 (attention_spread)', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await loadExample(page, 2);
  });

  test('detects attention_spread event', async ({ page }) => {
    await expect(page.locator('.kpi .v').first()).toContainText('attention_spread');
  });

  test('links 3 光伏 stocks', async ({ page }) => {
    const kpi = page.locator('.kpi').nth(1);
    await expect(kpi.locator('.v')).toContainText('3');
  });

  test('shows 80 historical events in Validation', async ({ page }) => {
    await page.locator('#tabbar button').nth(2).click();
    await page.waitForTimeout(200);
    // attention_spread has n=80 in EH
    await expect(page.locator('#out')).toContainText('80');
  });

  test('social_attention_spikes is true', async ({ page }) => {
    await page.locator('#tabbar button').nth(3).click();
    await page.waitForTimeout(200);
    const chip = page.locator('.pchip.on').filter({ hasText: 'social_attention_spikes' });
    await expect(chip).toBeVisible();
  });

  test('institutional_attention_increases is true', async ({ page }) => {
    await page.locator('#tabbar button').nth(3).click();
    await page.waitForTimeout(200);
    // 机构关注 should be true for this news
    const chip = page.locator('.pchip.on').filter({ hasText: 'institutional_attention_increases' });
    await expect(chip).toBeVisible();
  });
});
