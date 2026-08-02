const { test, expect } = require('@playwright/test');

test('configured AI layer renders structured output, retrieval and candidates', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#aiStatus')).toContainText('AI 研究层待凭证');
  await page.getByRole('button', { name: '储能政策' }).click();
  await page.locator('#apiKey').fill('test-deepseek-key');
  const responsePromise = page.waitForResponse(response => response.url().endsWith('/api/analyze'));
  await page.getByRole('button', { name: '开始分析' }).click();
  const response = await responsePromise;
  const responseBody = await response.json();
  const live = page.locator('#liveView');
  await expect(live).toContainText('deepseek-v4-flash · alphalens-research-v1.0');
  await expect(live).toContainText('JSON 对象 + 程序校验');
  await expect(live).toContainText('5 条相似冻结规则');
  await expect(live).toContainText('19/19 通过合法值校验');
  await expect(live).toContainText('储能政策跟进候选');
  await expect(live).toContainText('待统计验证');
  await expect(live).toContainText('AI 与规则谓词一致');
  await expect(live).toContainText('不构成投资建议');
  await expect(page.locator('#apiKey')).toHaveValue('');
  expect(responseBody.ai_analysis.credential_source).toBe('request');
  expect(JSON.stringify(responseBody)).not.toContain('test-deepseek-key');
  expect(response.headers()['cache-control']).toContain('no-store');
  expect(await page.evaluate(() => window.localStorage.length)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1440);
});
