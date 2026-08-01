const { defineConfig } = require('@playwright/test');
const path = require('path');

const root = path.resolve(__dirname, '..');
const python = process.env.ALPHALENS_PYTHON
  ? path.resolve(__dirname, process.env.ALPHALENS_PYTHON)
  : path.join(root, '.venv', 'bin', 'python');

module.exports = defineConfig({
  testDir: './ai-specs',
  timeout: 30000,
  expect: { timeout: 10000 },
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:8702',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  },
  webServer: [
    {
      command: `PYTHONPATH=. "${python}" tests/fake_ai_server.py`,
      cwd: root,
      port: 8799,
      reuseExistingServer: false,
      timeout: 15000,
    },
    {
      command: `ALPHALENS_AI_MODE=local ALPHALENS_LLM_BASE_URL=http://127.0.0.1:8799/v1 ALPHALENS_LLM_MODEL=fake-chat ALPHALENS_EMBEDDING_MODEL=fake-embedding ALPHALENS_DEMO_PORT=8702 "${python}" app/server.py`,
      cwd: root,
      url: 'http://localhost:8702',
      reuseExistingServer: false,
      timeout: 15000,
    },
  ],
  projects: [{ name: 'chromium-ai', use: { browserName: 'chromium' } }],
});
