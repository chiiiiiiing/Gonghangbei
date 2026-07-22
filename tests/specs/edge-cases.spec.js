const { test, expect } = require('@playwright/test');

// ═══════════════════════════════════════════
// Edge cases & error handling
// ═══════════════════════════════════════════

test.describe('Empty Input', () => {

  test('shows error when both fields empty', async ({ page }) => {
    await page.goto('/');
    // Clear auto-loaded text
    await page.locator('#it').fill('');
    await page.locator('#ic').fill('');
    await page.locator('#rb').click();
    // Should show inline error (not alert)
    await expect(page.locator('#out')).toContainText('请填写标题和正文');
  });

  test('shows error when title empty', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('');
    await page.locator('#ic').fill('some content');
    await page.locator('#rb').click();
    await expect(page.locator('#out')).toContainText('请填写标题和正文');
  });

  test('shows error when content empty', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('some title');
    await page.locator('#ic').fill('');
    await page.locator('#rb').click();
    await expect(page.locator('#out')).toContainText('请填写标题和正文');
  });

  test('error is inline, not browser alert', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('');
    await page.locator('#ic').fill('');
    // Listen for dialogs — there should be none
    let dialogFired = false;
    page.on('dialog', () => { dialogFired = true; });
    await page.locator('#rb').click();
    await page.waitForTimeout(300);
    expect(dialogFired).toBe(false);
  });
});

test.describe('No Event Detected', () => {

  test('shows no-event message for generic text', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('今天天气很好');
    await page.locator('#ic').fill('晴转多云，气温25-32度，适合户外活动');
    await page.locator('#is').selectOption('news');
    await page.locator('#rb').click();
    await page.waitForTimeout(500);
    await expect(page.locator('#out')).toContainText('未检测到明确的金融事件');
  });

  test('shows suggestion to use example buttons', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('随机文本');
    await page.locator('#ic').fill('没有金融关键词的普通文本');
    await page.locator('#is').selectOption('news');
    await page.locator('#rb').click();
    await page.waitForTimeout(500);
    await expect(page.locator('#out')).toContainText('试试点击上方的示例按钮');
  });

  test('shows identified entities even without event', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('宁德时代发布新产品');
    await page.locator('#ic').fill('公司推出全新消费电子产品，不涉及动力电池或新能源业务');
    await page.locator('#is').selectOption('news');
    await page.locator('#rb').click();
    await page.waitForTimeout(500);
    // Should show entity link but no event
    await expect(page.locator('#out')).toContainText('宁德时代');
    await expect(page.locator('#out')).toContainText('未检测到明确的金融事件');
  });

  test('tab bar hidden when no event', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('无事件文本');
    await page.locator('#ic').fill('无关内容');
    await page.locator('#is').selectOption('news');
    await page.locator('#rb').click();
    await page.waitForTimeout(500);
    await expect(page.locator('#tabbar')).not.toBeVisible();
  });
});

test.describe('Special Input', () => {

  test('handles text with special characters', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('【重磅】工信部发布《新型储能"十四五"行动方案》');
    await page.locator('#ic').fill('强调独立储能、源网荷储协同。支持派能科技、固德威的储能系统技术创新。');
    await page.locator('#is').selectOption('policy');
    await page.locator('#isn').fill('工信部');
    await page.locator('#rb').click();
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
    await expect(page.locator('.kpi .v').first()).toContainText('policy_support');
  });

  test('handles very long text', async ({ page }) => {
    await page.goto('/');
    const longContent = '国家发改委发布新能源产业政策。'.repeat(80);
    await page.locator('#it').fill('新能源产业政策文件');
    await page.locator('#ic').fill(longContent);
    await page.locator('#is').selectOption('policy');
    await page.locator('#rb').click();
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
    await expect(page.locator('.kpi .v').first()).toContainText('policy_support');
  });

  test('handles missing source name gracefully', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('工信部储能政策');
    await page.locator('#ic').fill('支持新型储能参与电力系统调节，推动储能系统集成技术创新。');
    await page.locator('#is').selectOption('policy');
    // Leave source name empty
    await page.locator('#isn').fill('');
    await page.locator('#rb').click();
    await page.waitForSelector('#out .kpis', { timeout: 5000 });
    // Should still work with default source name
    await expect(page.locator('.kpi .v').first()).toContainText('policy_support');
  });
});

test.describe('ir_qa Source Type', () => {

  test('ir_qa always returns no event', async ({ page }) => {
    await page.goto('/');
    await page.locator('#it').fill('投资者关注宁德时代产能进展');
    await page.locator('#ic').fill('投资者在互动平台提问宁德时代动力电池产能扩张计划');
    await page.locator('#is').selectOption('ir_qa');
    await page.locator('#rb').click();
    await page.waitForTimeout(500);
    await expect(page.locator('#out')).toContainText('未检测到明确的金融事件');
  });
});
