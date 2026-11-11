import { defineConfig, devices } from '@playwright/test';

// Runs against the production build (vite preview). API calls are stubbed per test with page.route.
// PW_PORT gives each checkout its own preview server, so two running at once never test each
// other's build (a server already on the port is reused).
const port = Number(process.env.PW_PORT ?? 4173);
const url = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: 'e2e',
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: url, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run build && npx vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url,
    reuseExistingServer: !process.env.CI,
  },
});
