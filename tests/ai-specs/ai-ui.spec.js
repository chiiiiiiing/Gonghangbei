const { test, expect } = require('@playwright/test');

test('configured AI layer renders structured output, retrieval and candidates', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#aiStatus')).toContainText('AI 研究层已配置');
  await page.getByRole('button', { name: '储能政策' }).click();
  await page.getByRole('button', { name: '开始分析' }).click();
  const live = page.locator('#liveView');
  await expect(live).toContainText('fake-chat · alphalens-research-v1.0');
  await expect(live).toContainText('严格 JSON Schema 返回');
  await expect(live).toContainText('5 条相似冻结规则');
  await expect(live).toContainText('19/19 通过合法值校验');
  await expect(live).toContainText('储能政策跟进候选');
  await expect(live).toContainText('待统计验证');
  await expect(live).toContainText('AI 与规则谓词一致');
  await expect(live).toContainText('不构成投资建议');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1440);
});
