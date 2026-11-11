import { expect, test } from '@playwright/test';
import { dana, incident, noLinks, sam, stubApi, stubLive, teams } from './fixtures';

const hunt = incident({
  id: 'h-1',
  kind: 'hunt',
  ref: 'HNT-2026-003',
  title: 'Scheduled tasks pointing at user-writable paths',
  severity: 'low',
  phase: 'collect',
  tags: ['persistence'],
});

const brief = (record: Record<string, any>, link_id: string) => ({
  id: record.id,
  link_id,
  kind: record.kind,
  ref: record.ref,
  title: record.title,
  status: record.status,
  phase: record.phase,
  severity: record.severity,
});

function task(overrides: Record<string, unknown> = {}): Record<string, any> {
  return {
    id: 'k-1',
    ref: 'TSK-4',
    incident_id: 'i-1',
    parent_id: null,
    title: 'Contain the finance hosts',
    description: '',
    team: teams[0],
    assignee: sam,
    status: 'open',
    blocked_reason: '',
    due_at: null,
    overdue: false,
    checklist: [],
    blocked_by: [],
    blocks: [],
    hidden_links: 0,
    playbook_id: null,
    outcomes: [],
    outcome: null,
    created_by: dana,
    created_at: '2026-11-10T09:00:00Z',
    updated_at: '2026-11-10T09:00:00Z',
    completed_by: null,
    completed_at: null,
    version: 1,
    ...overrides,
  };
}

test('hunts sit beside incidents: filtered, started with their own wording, and shown with their own phases', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, undefined, (method, path) => {
    if (method === 'GET' && path === '/api/incidents') return { json: [incident(), hunt] };
    if (method === 'POST' && path === '/api/incidents') return { status: 201, json: hunt };
    if (path === '/api/incidents/by-ref/HNT-2026-003' || path === '/api/incidents/h-1') return { json: hunt };
    if (path === '/api/incidents/h-1/links') return { json: noLinks };
    if (path === '/api/playbooks')
      return {
        json: [
          { id: 'pb-i', name: 'Ransomware', description: '', trigger_tags: [], applies_to: 'incident', is_active: true, steps: [], updated_at: '', version: 1 },
          { id: 'pb-h', name: 'Threat hunt', description: '', trigger_tags: ['persistence'], applies_to: 'hunt', is_active: true, steps: [], updated_at: '', version: 1 },
        ],
      };
  });
  await page.goto('/');
  const card = page.getByRole('link', { name: /HNT-2026-003/ });
  await expect(card.getByText('Hunt', { exact: true })).toBeVisible();
  await expect(card.getByRole('list', { name: 'Phase: collect' })).toContainText('hypothesis');

  await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: 'Hunts' }).click();
  await expect.poll(() => api.requests.some((r) => r.path === '/api/incidents' && r.method === 'GET')).toBe(true);

  await page.getByRole('button', { name: 'Start a hunt' }).click();
  const form = page.getByRole('form', { name: 'Start a hunt' });
  await expect(form.getByText('Hypothesis', { exact: true })).toBeVisible();
  // Only playbooks for hunts are offered, and the tag pre-ticks one
  await expect(form.getByText('Ransomware')).toHaveCount(0);
  await page.locator('#declare-title').fill('Scheduled tasks pointing at user-writable paths');
  await page.locator('#declare-tags').fill('persistence');
  await expect(form.getByRole('checkbox', { name: /Threat hunt/ })).toBeChecked();
  await form.getByRole('button', { name: 'Start' }).click();
  await expect(page).toHaveURL(/\/incidents\/HNT-2026-003$/);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents')!.body;
  expect(sent).toMatchObject({ kind: 'hunt', playbook_ids: ['pb-h'] });
  await expect(page.getByRole('list', { name: 'Phase: collect' })).toContainText('conclude');
});

test('a hunt is promoted to a linked incident', async ({ page }) => {
  await stubLive(page);
  const promoted = incident({ id: 'i-9', ref: 'OST-2026-009', title: 'Persistence on FIN-WS-0142' });
  const api = await stubApi(page, undefined, (method, path) => {
    if (path === '/api/incidents/by-ref/HNT-2026-003' || path === '/api/incidents/h-1') return { json: hunt };
    if (path === '/api/incidents/h-1/links') return { json: noLinks };
    if (method === 'POST' && path === '/api/incidents/h-1/promote') return { status: 201, json: promoted };
    if (path === '/api/incidents/by-ref/OST-2026-009' || path === '/api/incidents/i-9') return { json: promoted };
    if (path === '/api/incidents/i-9/links') return { json: { ...noLinks, promoted_from: brief(hunt, 'l-1') } };
  });
  await page.goto('/incidents/HNT-2026-003');
  await page.getByRole('button', { name: 'Promote to an incident' }).click();
  const form = page.getByRole('form', { name: 'Promote to an incident' });
  await form.getByLabel('Incident title').fill('Persistence on FIN-WS-0142');
  await form.getByRole('button', { name: 'Declare the incident' }).click();
  await expect(page).toHaveURL(/\/incidents\/OST-2026-009$/);
  expect(api.requests.find((r) => r.path === '/api/incidents/h-1/promote')!.body).toMatchObject({
    title: 'Persistence on FIN-WS-0142',
    severity: 'high',
  });
  const linked = page.getByRole('region', { name: 'Linked records' });
  await expect(linked.getByText('Promoted from')).toBeVisible();
  await expect(linked.getByRole('link', { name: 'HNT-2026-003' })).toBeVisible();
  // A promotion is history: it can't be unlinked
  await expect(linked.getByRole('button', { name: 'Unlink HNT-2026-003' })).toHaveCount(0);
});

test('linking records: children with progress, a hidden count, and unlinking', async ({ page }) => {
  await stubLive(page);
  const child = incident({ id: 'i-2', ref: 'OST-2026-002', title: 'Domain admin used out of hours', status: 'closed' });
  let links: Record<string, unknown> = { ...noLinks, children: [brief(child, 'l-2')], hidden: 1 };
  const api = await stubApi(page, undefined, (method, path) => {
    if (path === '/api/incidents/i-1/links' && method === 'GET') return { json: links };
    if (path === '/api/incidents/i-1/links' && method === 'POST') {
      links = { ...links, related: [brief(hunt, 'l-3')] };
      return { status: 201, json: links };
    }
    if (method === 'DELETE' && path === '/api/incidents/i-1/links/l-2') {
      links = { ...links, children: [] };
      return { status: 204 };
    }
  });
  await page.goto('/incidents/OST-2026-001');
  const panel = page.getByRole('region', { name: 'Linked records' });
  await expect(panel.getByText('1 of 1 closed')).toBeVisible();
  await expect(panel.getByText('One more linked record you can’t open.')).toBeVisible();

  const form = panel.getByRole('form', { name: 'Link a record' });
  await form.getByRole('combobox').selectOption('related_to');
  await form.getByLabel('Reference').fill('HNT-2026-003');
  await form.getByRole('button', { name: 'Link' }).click();
  await expect(panel.getByRole('link', { name: 'HNT-2026-003' })).toBeVisible();
  expect(api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/links')!.body).toEqual({
    ref: 'HNT-2026-003',
    relation: 'related_to',
  });

  await form.getByRole('combobox').selectOption('duplicate_of');
  await expect(panel.getByText('Marking this a duplicate closes it')).toBeVisible();

  await panel.getByRole('button', { name: 'Unlink OST-2026-002' }).click();
  await expect(panel.getByRole('link', { name: 'OST-2026-002' })).toHaveCount(0);
});

test('subtasks sit under their parent, and a task shows what it waits on', async ({ page }) => {
  await stubLive(page);
  let tasks = [
    task({ blocked_by: [{ id: 'k-9', ref: 'TSK-9', title: 'Rebuild DC03', status: 'in_progress', incident_ref: 'OST-2026-002' }] }),
    task({ id: 'k-2', ref: 'TSK-5', parent_id: 'k-1', title: 'Isolate FIN-WS-0142', status: 'done' }),
  ];
  const api = await stubApi(page, undefined, (method, path) => {
    if (path === '/api/incidents/i-1/tasks' && method === 'GET') return { json: tasks };
    if (path === '/api/incidents/i-1/tasks' && method === 'POST') {
      tasks = [...tasks, task({ id: 'k-3', ref: 'TSK-6', parent_id: 'k-1', title: 'Isolate FIN-WS-0150' })];
      return { status: 201, json: tasks[2] };
    }
    if (path === '/api/incidents/i-1/tasks/k-1/blocked-by' && method === 'POST') return { status: 201, json: tasks[0] };
  });
  await page.goto('/incidents/OST-2026-001?s=tasks');
  const parent = page.locator('#TSK-4');
  await expect(parent.getByRole('list', { name: 'Subtasks of TSK-4' }).getByText('Isolate FIN-WS-0142')).toBeVisible();
  await expect(parent.getByText('1/1')).toBeVisible();
  const waits = parent.getByRole('list', { name: 'TSK-4 waits on' });
  await expect(waits.getByRole('link', { name: 'TSK-9' })).toHaveAttribute('href', '/incidents/OST-2026-002?s=tasks#TSK-9');
  await expect(parent.getByText('1 to go')).toBeVisible();

  await parent.getByRole('button', { name: 'Add a subtask' }).first().click();
  const form = page.getByRole('form', { name: 'Add a subtask to TSK-4' });
  await form.getByLabel('Subtask').fill('Isolate FIN-WS-0150');
  await form.getByRole('button', { name: 'Add subtask' }).click();
  await expect(parent.getByText('Isolate FIN-WS-0150')).toBeVisible();
  expect(api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/tasks')!.body).toMatchObject({
    parent_id: 'k-1',
    team_id: teams[0].id,
  });

  await parent.getByRole('button', { name: 'Waits on…' }).first().click();
  await page.getByLabel('Task that TSK-4 waits on').fill('TSK-12');
  await page.getByRole('button', { name: 'Wait on it' }).click();
  expect(api.requests.find((r) => r.path === '/api/incidents/i-1/tasks/k-1/blocked-by')!.body).toEqual({ ref: 'TSK-12' });
});
