import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['line'], ['html', { open: 'never' }]] : 'list',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:4321',
    colorScheme: 'dark',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'pnpm run preview',
    url: 'http://127.0.0.1:4321',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
