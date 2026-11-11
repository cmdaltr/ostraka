import { expect, test } from '@playwright/test';
import { count, finding, incident, me, observable, sam, stubApi, stubLive } from './fixtures';

test('the list shows open incidents, and declaring one opens it', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) =>
    method === 'POST' && path === '/api/incidents' ? { status: 201, json: incident() } : undefined,
  );
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Incidents' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Phishing leads to a foothold/ })).toBeVisible();

  await page.getByRole('button', { name: 'Declare an incident' }).click();
  await page.locator('#declare-title').fill('Phishing leads to a foothold on FIN-WS-07');
  await page.locator('#declare-severity').selectOption('high');
  await page.locator('#declare-tags').fill('phishing, bec');
  await page.getByRole('button', { name: 'Declare', exact: true }).click();

  await expect(page).toHaveURL(/\/incidents\/OST-2026-001$/);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents')!.body as Record<string, unknown>;
  expect(sent).toMatchObject({ severity: 'high', team_ids: ['t-soc'], tags: ['phishing', 'bec'] });
});

test('the incident page shows who else is here and refetches what a live event changed', async ({ page }) => {
  const live = await stubLive(page);
  const api = await stubApi(page);
  await page.goto('/incidents/OST-2026-001?s=findings');

  await expect(page.getByRole('heading', { name: 'Phishing leads to a foothold on FIN-WS-07' })).toBeVisible();
  await expect(page.getByRole('list', { name: 'Also here' })).toContainText('Dana Lead editing');
  await expect(page.getByRole('heading', { name: 'PowerShell download cradle on FIN-WS-07' })).toBeVisible();

  const before = count(api, 'GET', '/api/incidents/i-1/findings');
  live().send(
    JSON.stringify({ type: 'activity', event: { incident_id: 'i-1', entity_type: 'finding', position: 9 } }),
  );
  await expect.poll(() => count(api, 'GET', '/api/incidents/i-1/findings')).toBeGreaterThan(before);
});

test('pasted text becomes a pick list, and only the chosen indicators are added', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/incidents/i-1/observables/extract')
      return {
        json: [
          { kind: 'domain', value: 'evil.example', confidence: 0.9, already_in_incident: true },
          { kind: 'ip_address', value: '203.0.113.9', confidence: 0.95, already_in_incident: false },
        ],
      };
    if (method === 'POST' && path === '/api/incidents/i-1/observables') return { status: 201, json: observable };
  });
  await page.goto('/incidents/OST-2026-001?s=observables');

  await expect(page.getByRole('note')).toContainText('Seen in 1 other incident');
  await expect(page.getByRole('note')).toContainText('ask DFIR (lead: Dana Lead)');

  await page.getByRole('button', { name: 'Paste text…' }).click();
  await page.locator('#extract-text').fill('Beacon to 203[.]0[.]113[.]9 and hxxps://evil[.]example/login');
  await page.getByRole('button', { name: 'Find indicators' }).click();
  const found = page.getByRole('list', { name: 'Indicators found' });
  await expect(found.getByRole('checkbox')).toHaveCount(2);
  await expect(found.getByRole('checkbox').first()).toBeDisabled(); // already in the incident
  await page.getByRole('button', { name: 'Add 1 to the incident' }).click();

  await expect(page.getByRole('button', { name: 'Paste text…' })).toBeVisible();
  const added = api.requests.filter((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/observables');
  expect(added.map((r) => r.body)).toEqual([{ value: '203.0.113.9', kind: 'ip_address' }]);
});

test('a lead is confirmed only after a verification is recorded', async ({ page }) => {
  await stubLive(page);
  let current = finding();
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/findings') return { json: [current] };
    if (path === '/api/incidents/i-1/findings/f-1/verifications') {
      current = finding({
        version: 2,
        verifications: [
          {
            id: 'v-1',
            method: 'search',
            platform: 'Defender XDR',
            query: 'DeviceProcessEvents | where DeviceName == "fin-ws-07"',
            scope: 'FIN-WS-07',
            time_from: null,
            time_to: null,
            result_count: 3,
            reference: '',
            note: '',
            ran_by: sam,
            ran_at: '2026-11-10T10:00:00Z',
          },
        ],
      });
      return { status: 201, json: current };
    }
    if (path === '/api/incidents/i-1/findings/f-1/confirm') return { json: { ...current, status: 'confirmed' } };
  });
  await page.goto('/incidents/OST-2026-001?s=findings');

  const confirm = page.getByRole('button', { name: 'Confirm', exact: true });
  await expect(confirm).toBeDisabled();

  await page.getByRole('button', { name: 'Add verification' }).click();
  await page.locator('#verify-scope').fill('FIN-WS-07');
  await page.locator('#verify-platform').fill('Defender XDR');
  await page.locator('#verify-query').fill('DeviceProcessEvents | where DeviceName == "fin-ws-07"');
  await page.getByRole('button', { name: 'Add verification' }).click();

  await expect(page.getByText('3 results')).toBeVisible();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => count(api, 'POST', '/api/incidents/i-1/findings/f-1/confirm')).toBe(1);
  const sent = api.requests.find((r) => r.path.endsWith('/verifications'))!.body;
  expect(sent).toMatchObject({ method: 'search', scope: 'FIN-WS-07', platform: 'Defender XDR' });
  expect(api.requests.find((r) => r.path.endsWith('/confirm'))!.body).toEqual({ version: 2, note: '' });
});

test('comments complete @mentions and #references, and render them as links', async ({ page }) => {
  await stubLive(page);
  const comments: unknown[] = [];
  const api = await stubApi(page, me(), (method, path, body) => {
    if (path !== '/api/incidents/i-1/comments') return undefined;
    if (method === 'GET') return { json: comments };
    const comment = {
      id: `c-${comments.length + 1}`,
      target_type: 'incident',
      target_id: null,
      parent_id: null,
      author: sam,
      created_at: '2026-11-10T10:05:00Z',
      edited_at: null,
      deleted: false,
      resolved_at: null,
      ...(body as object),
    };
    comments.push(comment);
    return { status: 201, json: comment };
  });
  await page.goto('/incidents/OST-2026-001');

  const box = page.getByRole('textbox', { name: 'Comment' });
  await box.pressSequentially('Over to @da');
  const suggestions = page.getByRole('listbox', { name: 'Suggestions' });
  await expect(suggestions.getByRole('option', { name: /Dana Lead/ })).toBeVisible();
  await box.press('Enter');
  await box.pressSequentially('for #fnd');
  await expect(suggestions.getByRole('option', { name: /FND-1/ })).toBeVisible();
  await box.press('Enter');
  await page.getByRole('button', { name: 'Comment', exact: true }).click();

  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/comments')!.body;
  expect(sent).toEqual({ body: 'Over to @[Dana Lead](user:0b5e2c1a-7d3f-4e8a-9c21-5f6d7e8a9b02) for #FND-1' });
  const thread = page.getByRole('list', { name: 'Comments' });
  await expect(thread.locator('.mention')).toHaveText('@Dana Lead');
  await thread.getByRole('link', { name: '#FND-1' }).click();
  await expect(page).toHaveURL(/\?s=findings#FND-1$/);
  await expect(page.locator('#FND-1')).toBeVisible();
});

test('a stale edit is refused with an explanation, and the current version is fetched', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) =>
    method === 'PATCH' && path === '/api/incidents/i-1'
      ? {
          status: 409,
          json: { detail: 'Someone else changed this incident while you were editing. Reload to see their change, then redo yours.' },
        }
      : undefined,
  );
  await page.goto('/incidents/OST-2026-001');
  const before = count(api, 'GET', '/api/incidents/i-1');
  await page.getByLabel('Severity').selectOption('critical');
  await expect(page.getByRole('alert')).toContainText('Someone else changed this while you were working on it');
  await expect.poll(() => count(api, 'GET', '/api/incidents/i-1')).toBeGreaterThan(before);
});

test('observers get the briefing view: no editing, and indicator values withheld', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me({ role: 'observer' }), (method, path) => {
    if (path === '/api/incidents/by-ref/OST-2026-001' || path === '/api/incidents/i-1')
      return { json: incident({ can_edit: false, can_manage: false }) };
    if (method === 'GET' && path === '/api/incidents/i-1/observables')
      return { json: [{ ...observable, value: null, masked: true }] };
  });
  await page.goto('/incidents/OST-2026-001?s=observables');
  await expect(page.getByText('Read only')).toBeVisible();
  await expect(page.getByText('Indicator values are hidden in the briefing view')).toBeVisible();
  await expect(page.locator('#OBS-7 .masked')).toHaveText('[domain]');
  await expect(page.getByText('evil.example')).toHaveCount(0);
  await expect(page.getByRole('form', { name: 'Add an observable' })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: 'Comment' })).toHaveCount(0);
});
