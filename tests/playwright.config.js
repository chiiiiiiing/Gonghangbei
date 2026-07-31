const { defineConfig } = require('@playwright/test');
const path = require('path');

const root = path.resolve(__dirname, '..');
const python = process.env.ALPHALENS_PYTHON
  ? path.resolve(__dirname, process.env.ALPHALENS_PYTHON)
  : path.join(root, '.venv', 'bin', 'python');

module.exports = defineConfig({
  testDir: './specs',
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['html', { outputFolder: 'report' }], ['list']],
  use: {
    baseURL: 'http://localhost:8701',
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  webServer: {
    command: `"${python}" app/server.py`,
    cwd: root,
    url: 'http://localhost:8701',
    reuseExistingServer: !process.env.CI,
    timeout: 15000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        viewport: { width: 1440, height: 900 },
        locale: 'zh-CN',
      },
    },
  ],
});
