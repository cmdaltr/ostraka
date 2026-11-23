import { expect, test } from '@playwright/test';
import { dana, incident, me, sam, stubApi, stubLive } from './fixtures';

const archive = (overrides: Record<string, unknown> = {}) => ({
  state: 'active',
  auto: true,
  archive_after_days: 30,
  cold_after_days: 90,
  archived_at: null,
  cold_at: null,
  restored_at: null,
  next_state: 'archived',
  next_at: '2026-12-20T09:00:00Z',
  can_set: true,
  can_restore: false,
  archive_to: [],
  request: null,
  ...overrides,
});

const entry = (overrides: Record<string, unknown> = {}) => ({
  ...incident({ status: 'closed', closed_at: '2026-10-01T09:00:00Z' }),
  archive_state: 'archived',
  archived_at: '2026-10-31T09:00:00Z',
  cold_at: null,
  techniques: ['T1566.001'],
  ...overrides,
});

const page1 = { items: [entry()], total: 26, page: 1, per_page: 25, has_more: true };

const pending = (overrides: Record<string, unknown> = {}) => ({
  id: 'r-1',
  action: 'restore',
  reason: 'The same sender is back',
  from_state: 'archived',
  to_state: 'active',
  requested_by: dana,
  requested_at: new Date(Date.now() - 3_600_000).toISOString(),
  expires_at: new Date(Date.now() + 20 * 3_600_000).toISOString(),
  outcome: 'pending',
  decided_by: null,
  decided_at: null,
  note: '',
  can_decide: true,
  can_withdraw: false,
  ...overrides,
});

test('the catalogue searches closed records on the server and pages through them', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (_method, path) => {
    if (path === '/api/catalogue') return { json: page1 };
    if (path === '/api/archive-requests') return { json: [] };
  });
  await page.goto('/');
  await page.getByRole('link', { name: 'Catalogue' }).click();
  await expect(page.getByRole('heading', { name: 'Catalogue', level: 1 })).toBeVisible();

  const card = page.getByRole('link', { name: /Folded Lantern/ });
  await expect(card).toContainText('Archived');
  await expect(card).toContainText('T1566.001');
  await expect(page.getByRole('navigation', { name: 'Pages' })).toContainText('1–1 of 26');

  await page.locator('#catalogue-q').fill('lantern');
  await page.locator('#catalogue-technique').fill('T156');
  await expect(page.getByText('A whole ATT&CK ID')).toBeVisible();
  await page.locator('#catalogue-technique').fill('t1566');
  await page.locator('#catalogue-tags').fill('phishing, BEC');
  await page.locator('#catalogue-closed-from').fill('2026-09-01');
  await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: 'Hunts' }).click();
  await expect
    .poll(() => api.requests.filter((r) => r.path === '/api/catalogue').at(-1)?.query.toString())
    .toBe('q=lantern&kind=hunt&tag=phishing&tag=bec&technique=T1566&closed_from=2026-09-01&per_page=25');

  await page.getByRole('button', { name: 'Next' }).click();
  await expect.poll(() => api.requests.filter((r) => r.path === '/api/catalogue').at(-1)?.query.get('page')).toBe('2');
  await page.getByRole('button', { name: 'Clear the search' }).click();
  await expect(page.locator('#catalogue-q')).toHaveValue('');
});

test('the lessons register shows each lesson with its record', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (_method, path) => {
    if (path === '/api/archive-requests') return { json: [] };
    if (path === '/api/catalogue/lessons')
      return {
        json: {
          items: [
            {
              id: 'l-1',
              ref: 'LSN-4',
              kind: 'improve',
              area: 'detection',
              observation: 'No alert when the forwarding rule was made',
              recommendation: 'Alert on New-InboxRule with ForwardTo',
              task: { id: 't-1', ref: 'TSK-9', status: 'in_progress' },
              created_by: sam,
              created_at: '2026-10-02T09:00:00Z',
              version: 1,
              record: { id: 'i-1', ref: 'OST-26-001', kind: 'incident', codename: 'Folded Lantern', title: 'x', archive_state: 'cold', closed_at: null },
            },
          ],
          total: 1,
          page: 1,
          per_page: 25,
          has_more: false,
        },
      };
  });
  await page.goto('/catalogue?view=lessons');
  const lesson = page.getByRole('article', { name: 'LSN-4' });
  await expect(lesson).toContainText('No alert when the forwarding rule was made');
  await expect(lesson).toContainText('Cold store');
  await expect(lesson).toContainText('Follow-up TSK-9 (in progress)');
  await expect(lesson.getByRole('link', { name: /OST-26-001 Folded Lantern/ })).toBeVisible();

  await page.locator('#lessons-follow-up').selectOption('open');
  await expect
    .poll(() => api.requests.filter((r) => r.path === '/api/catalogue/lessons').at(-1)?.query.get('follow_up'))
    .toBe('open');
});

test('an archived record is read only, and anyone working it can ask for it back', async ({ page }) => {
  await stubLive(page);
  const archived = incident({
    status: 'closed',
    archive_state: 'archived',
    can_edit: false,
    can_manage: false,
    archive: archive({ state: 'archived', next_state: 'cold', can_set: false, can_restore: true }),
  });
  let current: Record<string, unknown> = archived;
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/incidents/by-ref/OST-26-001' || path === '/api/incidents/i-1') return { json: current };
    if (path === '/api/incidents/i-1/lessons') return { json: [] };
    if (method === 'POST' && path === '/api/incidents/i-1/restore') {
      current = { ...archived, archive: archive({ state: 'archived', can_set: false, request: pending({ requested_by: sam, can_decide: false, can_withdraw: true }) }) };
      return { status: 201, json: current };
    }
  });
  await page.goto('/incidents/OST-26-001');
  await expect(page.locator('.incident-head')).toContainText('Archived');
  const panel = page.getByRole('region', { name: /Archiving/ });
  await expect(panel).toContainText('read only until a restore is approved');
  await panel.getByRole('button', { name: 'Ask for it back' }).click();
  await panel.getByLabel('Why you need it back').fill('Audit wants the timeline');
  await panel.getByRole('button', { name: 'Ask', exact: true }).click();

  await expect(panel.getByLabel('Archive request')).toContainText('Sam Analyst asked to restore it');
  await expect(panel.getByRole('button', { name: 'Withdraw' })).toBeVisible();
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/restore');
  expect(sent?.body).toEqual({ reason: 'Audit wants the timeline' });
});

test('a lead approves someone else’s restore from the record and the catalogue lists it waiting', async ({ page }) => {
  await stubLive(page);
  const archived = incident({
    status: 'closed',
    archive_state: 'cold',
    can_edit: false,
    can_manage: false,
    archive: archive({ state: 'cold', cold_at: '2026-11-01T09:00:00Z', next_state: null, can_set: false, request: pending({ from_state: 'cold' }) }),
  });
  let current: Record<string, unknown> = archived;
  const api = await stubApi(page, me({ role: 'team_lead' }), (method, path) => {
    if (path === '/api/incidents/by-ref/OST-26-001' || path === '/api/incidents/i-1') return { json: current };
    if (path === '/api/incidents/i-1/lessons') return { json: [] };
    if (path === '/api/incidents/i-1/evidence') return { json: [] };
    if (path === '/api/catalogue') return { json: { items: [], total: 0, page: 1, per_page: 25, has_more: false } };
    if (path === '/api/archive-requests')
      return {
        json: [
          {
            record: { id: 'i-1', ref: 'OST-26-001', kind: 'incident', codename: 'Folded Lantern', title: 'x', archive_state: 'cold', closed_at: null },
            request: pending({ from_state: 'cold' }),
          },
        ],
      };
    if (method === 'POST' && path === '/api/incidents/i-1/requests/r-1/approve') {
      current = incident({ status: 'closed', archive_state: 'active', archive: archive({ restored_at: new Date().toISOString() }) });
      return { json: current };
    }
  });
  await page.goto('/catalogue');
  const waiting = page.getByRole('region', { name: 'Waiting for approval' });
  await expect(waiting).toContainText('Dana Lead asked to restore it');
  await expect(waiting).toContainText(/1\d h left/);
  await waiting.getByRole('link', { name: /OST-26-001/ }).click();

  // cold store: no sections, only the way back, the lessons and the exhibits
  await expect(page.locator('.incident-head')).toContainText('Cold store');
  await expect(page.getByRole('navigation', { name: 'Incident sections' })).toHaveCount(0);
  const panel = page.getByRole('region', { name: /Archiving/ });
  await expect(panel).toContainText('In cold store since 1 Nov 2026');
  await panel.getByLabel('Note (optional)').fill('Go ahead');
  await panel.getByRole('button', { name: 'Approve and restore' }).click();

  await expect(page.getByRole('navigation', { name: 'Incident sections' })).toBeVisible();
  const sent = api.requests.find((r) => r.path === '/api/incidents/i-1/requests/r-1/approve');
  expect(sent?.body).toEqual({ note: 'Go ahead' });
});

test('whoever manages a closed record asks to archive it now, and it waits for someone else', async ({ page }) => {
  await stubLive(page);
  const closed = incident({ status: 'closed', closed_at: '2026-11-20T09:00:00Z', archive: archive({ archive_to: ['archived', 'cold'] }) });
  let current: Record<string, unknown> = closed;
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/incidents/by-ref/OST-26-001' || path === '/api/incidents/i-1') return { json: current };
    if (path === '/api/incidents/i-1/lessons') return { json: [] };
    if (method === 'POST' && path === '/api/incidents/i-1/archive') {
      current = {
        ...closed,
        archive: archive({ archive_to: [], request: pending({ action: 'archive', from_state: 'active', to_state: 'cold', requested_by: sam, can_decide: false, can_withdraw: true }) }),
      };
      return { status: 201, json: current };
    }
  });
  await page.goto('/incidents/OST-26-001');
  const panel = page.getByRole('region', { name: /Archiving/ });
  await panel.getByRole('button', { name: 'Archive now' }).click();
  await panel.getByRole('group', { name: 'Archive to' }).getByRole('button', { name: 'Cold store' }).click();
  await panel.getByLabel('Why it should be archived now').fill('Client engagement ended');
  await panel.getByRole('button', { name: 'Ask', exact: true }).click();

  await expect(panel.getByLabel('Archive request')).toContainText('Sam Analyst asked to put it in cold store');
  await expect(panel.getByRole('button', { name: 'Archive now' })).toHaveCount(0);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/archive');
  expect(sent?.body).toEqual({ reason: 'Client engagement ended', to_state: 'cold' });
});

test('an observer’s masked lesson search pages on without a total', async ({ page }) => {
  await stubLive(page);
  const lesson = (n: number) => ({
    id: `l-${n}`,
    ref: `LSN-${n}`,
    kind: 'improve',
    area: 'detection',
    observation: `Beacon to [ip_address] unseen, case ${n}`,
    recommendation: '',
    task: null,
    created_by: sam,
    created_at: '2026-10-02T09:00:00Z',
    version: 1,
    record: { id: 'i-1', ref: 'OST-26-001', kind: 'incident', codename: 'Folded Lantern', title: 'x', archive_state: 'active', closed_at: null },
  });
  const api = await stubApi(page, me({ role: 'observer' }), (_method, path) => {
    if (path === '/api/catalogue/lessons') {
      const at = Number(api.requests.at(-1)?.query.get('page') ?? '1');
      return { json: { items: [lesson(at)], total: null, has_more: at < 3, page: at, per_page: 1 } };
    }
  });
  await page.goto('/catalogue?view=lessons');
  const pager = page.getByRole('navigation', { name: 'Pages' });
  await expect(pager).toContainText('1–1 and more');
  await pager.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByRole('article', { name: 'LSN-2' })).toBeVisible();
  await pager.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByRole('article', { name: 'LSN-3' })).toBeVisible();
  await expect(pager).toContainText('3–3');
  await expect(pager.getByRole('button', { name: 'Next' })).toBeDisabled();
});
