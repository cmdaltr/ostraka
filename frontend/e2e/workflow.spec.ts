import { expect, test } from '@playwright/test';
import { dana, incident, me, noLinks, stubApi, stubLive, teams } from './fixtures';

const hunt = incident({ id: 'h-1', kind: 'hunt', ref: 'HNT-2026-003', title: 'Kerberoasting', phase: 'analyse' });
const outcomes = [
  { label: 'Confirmed: promote to an incident', action: 'promote', phase: null, opens: [2] },
  { label: 'Refuted: close the hunt', action: 'close', phase: null, opens: [] },
  { label: 'Inconclusive: refine the hypothesis', action: 'phase', phase: 'hypothesis', opens: [0] },
];
const conclude = {
  id: 'k-5',
  ref: 'TSK-5',
  incident_id: 'h-1',
  parent_id: null,
  title: 'Conclude',
  description: '',
  team: null,
  assignee: dana,
  status: 'open',
  blocked_reason: '',
  due_at: null,
  overdue: false,
  checklist: [],
  blocked_by: [],
  blocks: [],
  hidden_links: 0,
  playbook_id: 'pb-h',
  outcomes: outcomes.map((o) => o.label),
  outcome: null,
  created_by: dana,
  created_at: '2026-11-10T09:00:00Z',
  updated_at: '2026-11-10T09:00:00Z',
  completed_by: null,
  completed_at: null,
  version: 2,
};
const step = (position: number, title: string, extra: Record<string, unknown> = {}) => ({
  position,
  title,
  team: null,
  after: [],
  phase: null,
  outcomes: [],
  state: 'done',
  conditional: false,
  task_id: null,
  task_ref: null,
  outcome: null,
  runs: 1,
  ...extra,
});
const run = {
  playbook_id: 'pb-h',
  name: 'Threat hunt',
  applied_by: dana,
  applied_at: '2026-11-10T09:00:00Z',
  steps: [
    step(0, 'Write the hypothesis'),
    step(1, 'Conclude', { after: [0], outcomes, state: 'open', task_id: 'k-5', task_ref: 'TSK-5' }),
    step(2, 'Share what was found with Intelligence', { team: teams[1], state: 'waiting', conditional: true, runs: 0 }),
  ],
};

async function open(page: import('@playwright/test').Page) {
  await stubLive(page);
  return stubApi(page, me(), (method, path) => {
    if (path === '/api/incidents/by-ref/HNT-2026-003' || path === '/api/incidents/h-1') return { json: hunt };
    if (path === '/api/incidents/h-1/links') return { json: noLinks };
    if (path === '/api/incidents/h-1/tasks') return { json: [conclude] };
    if (path === '/api/incidents/h-1/playbooks') return { json: [run] };
    if (method === 'POST' && path === '/api/incidents/h-1/tasks/k-5/decide') return { json: { ...conclude, status: 'done' } };
  });
}

test('a decision is finished by choosing an outcome; promoting asks for the incident first', async ({ page }) => {
  const api = await open(page);
  await page.goto('/incidents/HNT-2026-003?s=tasks');
  const card = page.locator('#TSK-5');
  await expect(card.getByRole('button', { name: 'Mark done' })).toHaveCount(0);
  await expect(card.getByRole('combobox', { name: 'Status of TSK-5' }).locator('option[value=done]')).toHaveCount(0);

  const decide = card.getByRole('group', { name: 'Decide TSK-5' });
  await decide.getByLabel('Why, for TSK-5').fill('One workstation, one burst.');
  await decide.getByRole('button', { name: 'Confirmed: promote to an incident' }).click();
  // Nothing is sent until the incident has a name
  expect(api.requests.some((r) => r.path.endsWith('/decide'))).toBe(false);
  await decide.getByLabel('Incident title').fill('Service account roasted');
  await decide.getByRole('button', { name: 'Promote', exact: true }).click();
  await expect.poll(() => api.requests.find((r) => r.path.endsWith('/decide'))?.body).toMatchObject({
    version: 2,
    outcome: 'Confirmed: promote to an incident',
    note: 'One workstation, one burst.',
    promote: { title: 'Service account roasted', severity: 'high' },
  });
});

test('an outcome that doesn’t promote is sent straight away', async ({ page }) => {
  const api = await open(page);
  await page.goto('/incidents/HNT-2026-003?s=tasks');
  await page.getByRole('button', { name: 'Refuted: close the hunt' }).click();
  await expect.poll(() => api.requests.find((r) => r.path.endsWith('/decide'))?.body).toMatchObject({
    outcome: 'Refuted: close the hunt',
  });
});

test('the workflow view shows each step’s lane and state', async ({ page }) => {
  await open(page);
  await page.goto('/incidents/HNT-2026-003?s=tasks');
  await expect(page.getByText('1 more step to come')).toBeVisible();
  await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Workflow' }).click();
  const flow = page.getByRole('region', { name: 'Threat hunt workflow' });
  await expect(flow.getByLabel('Step 2: Conclude, open')).toBeVisible();
  await expect(flow.getByLabel('Step 3: Share what was found with Intelligence, waiting')).toBeVisible();
  await expect(flow.getByText(teams[1].name, { exact: true })).toBeVisible();
  await expect(flow.getByRole('link', { name: 'TSK-5' })).toHaveAttribute('href', '/incidents/HNT-2026-003?s=tasks#TSK-5');
});

test('the editor makes steps wait, open at a phase, and decide', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me({ role: 'team_lead' }), (method, path) => {
    if (method === 'POST' && path === '/api/playbooks') return { status: 201, json: {} };
  });
  await page.goto('/playbooks');
  await page.getByRole('button', { name: 'New playbook' }).click();
  await page.locator('#playbook-name').fill('Hunt');
  await page.locator('#playbook-for').selectOption('hunt');
  await page.getByLabel('Task 1 title').fill('Look');
  await page.getByLabel('Task 1 phase').selectOption('collect');
  await page.getByRole('button', { name: 'Add a task' }).click();
  await page.getByLabel('Task 2 title').fill('Decide');
  await page.getByLabel('Task 2 after task 1').click();
  await page.getByRole('button', { name: 'Make it a decision' }).nth(1).click();
  await page.getByLabel('Task 2 outcome 1', { exact: true }).fill('Look again');
  await page.getByLabel('Task 2 outcome 1 action').selectOption('phase');
  await page.getByLabel('Task 2 outcome 1 phase').selectOption('hypothesis');
  await page.getByLabel('Outcome Look again opens task 1').click();
  await expect(page.getByLabel('Step 2: Decide')).toBeVisible();
  await page.getByRole('button', { name: 'Create playbook' }).click();
  await expect.poll(() => api.requests.find((r) => r.method === 'POST' && r.path === '/api/playbooks')?.body).toMatchObject({
    applies_to: 'hunt',
    steps: [
      { title: 'Look', phase: 'collect', after: [], outcomes: [] },
      { title: 'Decide', after: [0], outcomes: [{ label: 'Look again', action: 'phase', phase: 'hypothesis', opens: [0] }] },
    ],
  });
});
