import { expect, test } from '@playwright/test';
import { incident, me, stubApi, stubLive } from './fixtures';

const admin = me({ role: 'admin', team: null });

const kinds = [
  {
    kind: 'splunk', name: 'Splunk', language: 'SPL', window: 'The window chosen above, unless the SPL sets earliest= or latest=.',
    fields: [
      { key: 'url', label: 'Management URL', secret: false, required: true, hint: 'https://splunk.example.org:8089' },
      { key: 'token', label: 'Token', secret: true, required: false, hint: 'Settings > Tokens' },
    ],
  },
  {
    kind: 'defender', name: 'Defender XDR', language: 'KQL', window: 'The KQL’s own.',
    fields: [
      { key: 'tenant_id', label: 'Tenant ID', secret: false, required: true, hint: '' },
      { key: 'client_id', label: 'Application (client) ID', secret: false, required: true, hint: '' },
      { key: 'client_secret', label: 'Client secret', secret: true, required: true, hint: '' },
    ],
  },
];
const splunk = {
  id: 'c-1', name: 'Splunk prod', kind: 'splunk', language: 'SPL', enabled: true, settings: null, secrets_set: null,
  verify_tls: null, checked_at: null, check_ok: null, check_message: '', version: null,
};
const past = {
  id: 'q-1', ref: 'QRY-7', text: 'index=edr process=rclone.exe dest=[ip address]', kind: 'splunk', language: 'SPL',
  connection_id: 'c-1', connection_name: 'Splunk prod', incident: { id: 'i-1', ref: 'OST-26-001', codename: 'Quiet Kiln' },
  window_start: '2026-11-17T00:00:00Z', window_end: null, outcome: 'ok', row_count: 12, truncated: false,
  run_by: { id: 'u-2', name: 'Dana Lead' }, run_at: '2026-11-18T09:00:00Z',
};

test('a query runs against a SIEM, its rows shown and only the query kept', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/siem/kinds') return { json: kinds };
    if (path === '/api/siem/connections') return { json: [splunk] };
    if (method === 'GET' && path === '/api/incidents') return { json: [incident()] };
    if (method === 'GET' && path === '/api/siem/queries') return { json: [past] };
    if (method === 'POST' && path === '/api/siem/queries')
      return {
        json: {
          query: { ...past, id: 'q-2', ref: 'QRY-8', text: 'index=mail user=bob', row_count: 2, incident: null },
          columns: ['user', 'subject'],
          rows: [{ user: 'bob', subject: 'Invoice' }, { user: 'bob', subject: { nested: true } }],
          truncated: false,
          problem: '',
        },
      };
    return undefined;
  });
  await page.goto('/queries');
  const form = page.getByRole('form', { name: 'Run a query' });
  await expect(form.getByText('Search window: The window chosen above, unless the SPL sets earliest= or latest=.')).toBeVisible();
  await form.getByLabel('Query (SPL)').fill('index=mail user=bob');
  await form.getByLabel('Window').selectOption('7d');
  await form.getByRole('button', { name: 'Run' }).click();

  const results = page.getByRole('region', { name: 'Results' });
  await expect(results.getByText(/2 rows from Splunk prod\. Only the query is kept/)).toBeVisible();
  await expect(results.getByRole('columnheader', { name: 'subject' })).toBeVisible();
  await expect(results.getByRole('cell', { name: '{"nested":true}' })).toBeVisible();
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/siem/queries')!.body as Record<string, unknown>;
  expect(sent).toMatchObject({ connection_id: 'c-1', text: 'index=mail user=bob', incident_id: null, end: null });
  expect(Date.now() - new Date(sent.start as string).getTime()).toBeGreaterThan(6.9 * 24 * 3600_000);

  // a past query is found by its text, and loaded back into the form
  const history = page.getByRole('region', { name: 'Past queries' });
  await history.getByLabel('Containing').fill('rclone');
  await expect(history.locator('mark', { hasText: 'rclone' })).toBeVisible();
  await expect(history.getByText(/QRY-7 · Splunk prod \(SPL\) · Dana Lead/)).toBeVisible();
  await expect(history.getByRole('link', { name: 'OST-26-001' })).toHaveAttribute('href', '/incidents/OST-26-001');
  await history.getByRole('button', { name: 'Use this query' }).click();
  await expect(form.getByLabel('Query (SPL)')).toHaveValue('index=edr process=rclone.exe dest=[ip address]');
  await expect(form.getByLabel('For')).toHaveValue('i-1');
  await expect(form.getByLabel('Window')).toHaveValue('custom');
});

test('a SIEM that refuses a query says why', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) => {
    if (path === '/api/siem/kinds') return { json: kinds };
    if (path === '/api/siem/connections') return { json: [splunk] };
    if (method === 'GET' && path === '/api/incidents') return { json: [] };
    if (method === 'GET' && path === '/api/siem/queries') return { json: [] };
    if (method === 'POST' && path === '/api/siem/queries')
      return {
        json: {
          query: { ...past, outcome: 'failed', row_count: null, text: '| frob' }, columns: [], rows: [], truncated: false,
          problem: "Unknown search command 'frob'.",
        },
      };
    return undefined;
  });
  await page.goto('/queries');
  await page.getByLabel('Query (SPL)').fill('| frob');
  await page.getByRole('button', { name: 'Run' }).click();
  await expect(page.getByRole('alert')).toHaveText("Unknown search command 'frob'.");
  await expect(page.getByText('Nobody has run a query yet.')).toBeVisible();
});

test('an observer searches past queries but runs none', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me({ role: 'observer' }), (method, path) => {
    if (path === '/api/siem/kinds') return { json: kinds };
    if (path === '/api/siem/connections') return { json: [splunk] };
    if (method === 'GET' && path === '/api/siem/queries') return { json: [past] };
    return undefined;
  });
  await page.goto('/queries');
  await expect(page.getByRole('region', { name: 'Past queries' }).getByText('index=edr process=rclone.exe')).toBeVisible();
  await expect(page.getByRole('form', { name: 'Run a query' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Use this query' })).toHaveCount(0);
});

test('an admin connects a SIEM, its secret never shown again', async ({ page }) => {
  await stubLive(page);
  let connected: Record<string, unknown>[] = [];
  const api = await stubApi(page, admin, (method, path) => {
    if (path === '/api/siem/kinds') return { json: kinds };
    if (method === 'GET' && path === '/api/siem/connections') return { json: connected };
    if (method === 'POST' && path === '/api/siem/connections') {
      connected = [{ ...splunk, kind: 'defender', name: 'Defender', language: 'KQL', settings: { tenant_id: 't', client_id: 'c' }, secrets_set: ['client_secret'], verify_tls: true, version: 1 }];
      return { status: 201, json: connected[0] };
    }
    if (method === 'POST' && path === '/api/siem/connections/c-1/test')
      return { json: { ...connected[0], check_ok: false, check_message: 'Advanced Hunting refused the app registration' } };
    return undefined;
  });
  await page.goto('/config?tab=siems');
  const add = page.getByRole('form', { name: 'Connect a SIEM' });
  await add.getByLabel('SIEM').selectOption('defender');
  await add.getByLabel('Name').fill('Defender');
  await add.getByLabel('Tenant ID').fill('t');
  await add.getByLabel('Application (client) ID').fill('c');
  await add.getByLabel('Client secret').fill('s3cret');
  await add.getByRole('button', { name: 'Connect' }).click();
  await expect
    .poll(() => api.requests.find((r) => r.method === 'POST' && r.path === '/api/siem/connections')?.body)
    .toEqual({ name: 'Defender', kind: 'defender', values: { tenant_id: 't', client_id: 'c', client_secret: 's3cret' }, verify_tls: true });

  const saved = page.getByRole('region', { name: 'Defender' });
  await expect(saved.getByLabel('Client secret')).toHaveAttribute('placeholder', 'Saved: leave empty to keep it');
  await expect(saved.getByLabel('Client secret')).toHaveValue('');
  await saved.getByRole('button', { name: 'Test the connection' }).click();
  await expect(saved.getByRole('alert')).toContainText('Advanced Hunting refused the app registration');
});

test('the incident lists the queries run for it', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/siem/queries') return { json: [past] };
    return undefined;
  });
  await page.goto('/incidents/OST-26-001?s=evidence');
  const card = page.getByRole('region', { name: 'Queries' });
  await expect(card.getByText('index=edr process=rclone.exe')).toBeVisible();
  await expect(card.getByRole('link', { name: 'Run a query' })).toHaveAttribute('href', '/queries?incident=i-1');
});
