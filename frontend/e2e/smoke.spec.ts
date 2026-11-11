import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ready', (r) =>
    r.fulfill({ json: { status: 'ok', checks: { database: 'ok', redis: 'ok' } } }),
  );
  await page.route('**/api/health', (r) => r.fulfill({ json: { status: 'ok', version: '0.1.0' } }));
});

test('status page loads without console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));

  await page.goto('/status');
  await expect(page.getByTestId('server-status')).toHaveText('Ready');
  await expect(page.getByRole('img', { name: 'Ostraka' }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test('degraded dependencies are reported', async ({ page }) => {
  await page.route('**/api/ready', (r) =>
    r.fulfill({ status: 503, json: { status: 'degraded', checks: { database: 'ok', redis: 'unavailable' } } }),
  );
  await page.goto('/status');
  await expect(page.getByTestId('server-status')).toHaveText('Degraded');
  await expect(page.getByText('Unavailable')).toBeVisible();
});

test('Cobalt is the default and a chosen theme survives a reload', async ({ page }) => {
  await page.goto('/design');
  const html = page.locator('html');
  await expect(html).toHaveAttribute('data-palette', 'cobalt');

  await page.locator('#theme-palette').selectOption('ledger');
  await page.locator('#theme-mode').selectOption('dark');
  await expect(html).toHaveAttribute('data-palette', 'ledger');
  await expect(html).toHaveAttribute('data-mode', 'dark');

  await page.reload();
  await expect(html).toHaveAttribute('data-palette', 'ledger');
  await expect(html).toHaveAttribute('data-mode', 'dark');
});

test('each palette gives a different accent, and severity is unchanged', async ({ page }) => {
  await page.goto('/design');
  const read = () =>
    page.evaluate(() => {
      const s = getComputedStyle(document.documentElement);
      return { accent: s.getPropertyValue('--accent').trim(), high: s.getPropertyValue('--sev-high').trim() };
    });
  const seen = new Map<string, { accent: string; high: string }>();
  for (const p of ['cobalt', 'ostrakon', 'ledger']) {
    await page.locator('#theme-palette').selectOption(p);
    seen.set(p, await read());
  }
  expect(new Set([...seen.values()].map((v) => v.accent)).size).toBe(3);
  expect(new Set([...seen.values()].map((v) => v.high)).size).toBe(1);
});
