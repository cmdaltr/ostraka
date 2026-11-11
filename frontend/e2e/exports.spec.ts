import { expect, test } from '@playwright/test';
import { incident, me, stubApi, stubLive } from './fixtures';

const admin = me({ role: 'admin', team: null });
const offered = (available: boolean, reason: string | null = null) => ({
  downloads: true,
  reason: null,
  misp: { available, reason },
});

test('the Export menu downloads STIX and the Navigator layer, and sends to MISP only when asked', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/incidents/i-1/exports') return { json: offered(true) };
    if (method === 'POST' && path === '/api/incidents/i-1/misp')
      return { json: { action: 'added', event_id: '41', uuid: 'e-1', url: 'https://misp.example/events/view/41' } };
    return undefined;
  });
  await page.route('**/api/incidents/i-1/export/**', (r) => {
    const url = new URL(r.request().url());
    const format = url.pathname.split('/').pop();
    const name = `OST-2026-001-${format}${url.searchParams.get('leads') ? '-leads' : ''}.json`;
    return r.fulfill({
      body: '{}',
      headers: { 'Content-Type': 'application/json', 'Content-Disposition': `attachment; filename="${name}"` },
    });
  });
  await page.goto('/incidents/OST-2026-001');
  await page.locator('summary', { hasText: 'Export' }).click();
  await expect(page.getByRole('heading', { name: 'For other tools' })).toBeVisible();
  expect(api.requests.filter((r) => r.method === 'POST' && r.path.endsWith('/misp'))).toHaveLength(0);

  const stix = page.locator('.export-row', { hasText: 'STIX 2.1 bundle' });
  const [file] = await Promise.all([page.waitForEvent('download'), stix.getByRole('menuitem', { name: 'JSON' }).click()]);
  expect(file.suggestedFilename()).toBe('OST-2026-001-stix.json');

  const layer = page.locator('.export-row', { hasText: 'ATT&CK Navigator layer' });
  const [leads] = await Promise.all([page.waitForEvent('download'), layer.getByRole('menuitem', { name: 'With leads' }).click()]);
  expect(leads.suggestedFilename()).toBe('OST-2026-001-navigator-leads.json');

  await page.getByRole('menuitem', { name: 'Send to MISP' }).click();
  await expect(page.getByText('Sent to MISP (event 41).')).toBeVisible();
  expect(api.requests.filter((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/misp')).toHaveLength(1);
});

test('a TLP:RED incident offers no MISP push, and says why', async ({ page }) => {
  await stubLive(page);
  const red = incident({ tlp: 'red' });
  await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && (path === '/api/incidents/i-1' || path === '/api/incidents/by-ref/OST-2026-001')) return { json: red };
    if (path === '/api/incidents/i-1/exports')
      return { json: offered(false, 'TLP:RED and TLP:AMBER+STRICT incidents are never sent to another system.') };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001');
  await page.locator('summary', { hasText: 'Export' }).click();
  await expect(page.getByText('TLP:RED: a download for the commander only, never sent to another system.')).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Send to MISP' })).toHaveCount(0);
  await expect(page.locator('.export-row', { hasText: 'MISP event' }).getByRole('menuitem', { name: 'JSON' })).toBeVisible();
});

test('an admin sets up MISP; the key is sent once and never shown', async ({ page }) => {
  await stubLive(page);
  const saved = {
    configured: true,
    url: 'https://misp.example.org',
    verify_tls: true,
    distribution: 0,
    has_key: true,
    sending: true,
    distributions: { '0': 'Your organisation only', '1': 'This community only', '2': 'Connected communities', '3': 'All' },
    version: 1,
  };
  let current = { ...saved, configured: false, url: '', has_key: false, version: 0 };
  const api = await stubApi(page, admin, (method, path) => {
    if (method === 'GET' && path === '/api/misp') return { json: current };
    if (method === 'PUT' && path === '/api/misp') {
      current = saved;
      return { json: saved };
    }
    if (method === 'POST' && path === '/api/misp/check') return { json: { ok: true, version: '2.5.2', error: '' } };
    return undefined;
  });
  await page.goto('/installation?tab=misp');
  await expect(page.getByRole('heading', { name: 'MISP' })).toBeVisible();
  await page.getByLabel('URL').fill('https://misp.example.org');
  await page.getByLabel('API key').fill('s3cret-key');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
  expect(api.requests.find((r) => r.method === 'PUT')!.body).toEqual({
    url: 'https://misp.example.org',
    api_key: 's3cret-key',
    verify_tls: true,
    distribution: 0,
    version: null,
  });
  await expect(page.getByLabel('API key')).toHaveValue('');
  await expect(page.getByLabel('API key')).toHaveAttribute('placeholder', 'Stored. Leave empty to keep it');
  await page.getByRole('button', { name: 'Check the connection' }).click();
  await expect(page.getByText('MISP answered: version 2.5.2.')).toBeVisible();
});
