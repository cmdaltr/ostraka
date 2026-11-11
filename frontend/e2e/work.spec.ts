import { expect, test } from '@playwright/test';
import { count, dana, incident, me, sam, stubApi, stubLive, teams } from './fixtures';

function task(overrides: Record<string, unknown> = {}): Record<string, any> {
  return {
    id: 'k-1',
    ref: 'TSK-4',
    incident_id: 'i-1',
    parent_id: null,
    title: 'Pull the mailbox audit log',
    description: '',
    team: teams[0],
    assignee: null,
    status: 'open',
    blocked_reason: '',
    due_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
    overdue: true,
    checklist: [{ id: 'c-1', text: 'Export the log', done: false, done_by: null, done_at: null }],
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

const phishing = {
  id: 'pb-1',
  name: 'Phishing',
  description: 'First hours of a credential phish',
  trigger_tags: ['phishing'],
  applies_to: 'incident',
  is_active: true,
  steps: [
    { id: 's-1', title: 'Pull the message', description: '', team: null, due_minutes: 30, checklist: [], after: [], phase: null, outcomes: [] },
    { id: 's-2', title: 'Revoke sessions', description: '', team: teams[1], due_minutes: 60, checklist: ['All devices'], after: [], phase: null, outcomes: [] },
  ],
  updated_at: '2026-11-10T09:00:00Z',
  version: 1,
};

test('the bell counts unread notifications, opens them and refreshes on a live signal', async ({ page }) => {
  const live = await stubLive(page);
  let items = [
    {
      id: 'n-1',
      kind: 'assigned',
      title: 'TSK-4: Pull the mailbox audit log',
      body: 'Dana Lead gave you a task in OST-2026-001.',
      link: '/incidents/OST-2026-001?s=tasks#TSK-4',
      actor: dana,
      created_at: '2026-11-10T10:00:00Z',
      read_at: null as string | null,
    },
  ];
  const api = await stubApi(page, me(), (_method, path) => {
    if (path === '/api/notifications') return { json: { items, unread: items.filter((n) => !n.read_at).length } };
    if (path === '/api/notifications/n-1/read') {
      items = items.map((n) => ({ ...n, read_at: '2026-11-10T10:05:00Z' }));
      return { status: 204 };
    }
    if (path === '/api/incidents/i-1/tasks') return { json: [task({ assignee: sam, status: 'in_progress' })] };
  });
  await page.goto('/');
  const bell = page.getByRole('button', { name: /^Notifications/ });
  await expect(bell).toHaveAccessibleName('Notifications, 1 unread');

  await bell.click();
  await page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: /Pull the mailbox audit log/ }).click();
  await expect(page).toHaveURL(/\/incidents\/OST-2026-001\?s=tasks#TSK-4$/);
  await expect(page.locator('#TSK-4')).toBeVisible();
  await expect(bell).toHaveAccessibleName('Notifications');

  const before = count(api, 'GET', '/api/notifications');
  live().send(JSON.stringify({ type: 'notification' }));
  await expect.poll(() => count(api, 'GET', '/api/notifications')).toBeGreaterThan(before);
});

test('a team task can be claimed, ticked, blocked with a reason and nudged', async ({ page }) => {
  await stubLive(page);
  let current = task();
  const api = await stubApi(page, me(), (method, path, body) => {
    const b = (body ?? {}) as Record<string, unknown>;
    if (method === 'GET' && path === '/api/incidents/i-1/tasks') return { json: [current] };
    if (path === '/api/incidents/i-1/tasks/k-1/claim') {
      current = task({ assignee: sam, status: 'in_progress', version: 2 });
      return { json: current };
    }
    if (path === '/api/incidents/i-1/tasks/k-1/checklist/c-1') {
      current = { ...current, checklist: [{ ...current.checklist[0], done: !!b.done, done_by: sam }] };
      return { json: current };
    }
    if (method === 'PATCH' && path === '/api/incidents/i-1/tasks/k-1') {
      current = { ...current, ...b, version: current.version + 1 };
      return { json: current };
    }
    if (path === '/api/incidents/i-1/tasks/k-1/nudge') return { status: 204 };
  });
  await page.goto('/incidents/OST-2026-001?s=tasks');
  const card = page.locator('#TSK-4');
  await expect(card).toContainText('SOC’s queue');
  await expect(card).toContainText('2 h overdue');

  await card.getByRole('button', { name: 'Claim' }).click();
  await expect(card).toContainText('Sam Analyst');
  await card.getByRole('checkbox').click(); // saved first, then shown ticked
  await expect(card.getByRole('checkbox')).toBeChecked();
  await expect(card.getByText('1/1')).toBeVisible();

  await card.getByLabel('Status of TSK-4').selectOption('blocked');
  await card.getByLabel('Why TSK-4 is blocked').fill('Waiting on Exchange admin');
  await card.getByRole('button', { name: 'Mark blocked' }).click();
  await expect(card).toContainText('Blocked: Waiting on Exchange admin');
  const blocked = api.requests.find((r) => r.method === 'PATCH' && r.path === '/api/incidents/i-1/tasks/k-1')!.body;
  expect(blocked).toEqual({ version: 2, status: 'blocked', blocked_reason: 'Waiting on Exchange admin' });

  // Sam holds it now, so the nudge goes to someone else's task: reassign in the stub and reload
  current = { ...current, assignee: dana };
  await page.reload();
  await card.getByRole('button', { name: 'Nudge' }).click();
  await card.getByLabel('Nudge about TSK-4').fill('Any luck?');
  await card.getByRole('button', { name: 'Send nudge' }).click();
  await expect(card.getByRole('button', { name: 'Nudged' })).toBeDisabled();
  expect(api.requests.find((r) => r.path.endsWith('/nudge'))!.body).toEqual({ message: 'Any luck?' });
});

test('adding a task for a person sends the checklist line by line', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) =>
    method === 'POST' && path === '/api/incidents/i-1/tasks' ? { status: 201, json: task() } : undefined,
  );
  await page.goto('/incidents/OST-2026-001?s=tasks');
  await page.getByRole('button', { name: 'Add a task' }).click();
  await page.locator('#task-title').fill('Image the laptop');
  await page.getByRole('radio', { name: 'A person' }).click();
  await page.locator('#task-assignee').selectOption(dana.id);
  await page.locator('#task-checklist').fill('Memory\nDisk\n');
  await page.getByRole('button', { name: 'Add task', exact: true }).click();
  await expect(page.getByRole('form', { name: 'Add a task' })).toHaveCount(0);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/tasks')!.body;
  expect(sent).toMatchObject({ title: 'Image the laptop', team_id: null, assignee_id: dana.id, checklist: ['Memory', 'Disk', ''] });
});

test('your work lists assigned tasks and the team queue, and claims from it', async ({ page }) => {
  await stubLive(page);
  const brief = { id: 'i-1', ref: 'OST-2026-001', title: 'Phishing leads to a foothold', severity: 'high', tlp: 'amber' };
  const api = await stubApi(page, me(), (_method, path) => {
    if (path === '/api/tasks/mine')
      return {
        json: [
          { ...task({ id: 'k-2', ref: 'TSK-5', title: 'Write the comms holding line', assignee: sam }), incident: brief, queued: false },
          { ...task(), incident: brief, queued: true },
        ],
      };
    if (path === '/api/incidents/i-1/tasks/k-1/claim') return { json: task({ assignee: sam }) };
  });
  await page.goto('/work');
  await expect(page.getByRole('region', { name: 'Assigned to you' })).toContainText('Write the comms holding line');
  const queue = page.getByRole('region', { name: 'Team queue' });
  await expect(queue).toContainText('Pull the mailbox audit log');
  await queue.getByRole('button', { name: 'Claim' }).click();
  await expect.poll(() => count(api, 'POST', '/api/incidents/i-1/tasks/k-1/claim')).toBe(1);
});

test('declaring with a matching tag pre-ticks the playbook, and unticking sticks', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/playbooks') return { json: [phishing] };
    if (method === 'POST' && path === '/api/incidents') return { status: 201, json: incident() };
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Declare an incident' }).click();
  await page.locator('#declare-title').fill('Credential phish against finance');
  const pick = page.getByRole('checkbox', { name: /Phishing/ });
  await expect(pick).not.toBeChecked();
  await page.locator('#declare-tags').fill('phishing');
  await expect(pick).toBeChecked();
  await expect(page.getByText('suggested by tag')).toBeVisible();
  await page.getByRole('button', { name: 'Declare', exact: true }).click();
  await expect(page).toHaveURL(/OST-2026-001/);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents')!.body;
  expect(sent).toMatchObject({ tags: ['phishing'], playbook_ids: ['pb-1'] });
});

test('team leads write playbooks step by step', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me({ role: 'team_lead' }), (method, path) => {
    if (method === 'POST' && path === '/api/playbooks') return { status: 201, json: phishing };
  });
  await page.goto('/playbooks');
  await page.getByRole('button', { name: 'New playbook' }).click();
  await page.locator('#playbook-name').fill('Phishing');
  await page.locator('#playbook-tags').fill('phishing, credential-theft');
  await page.getByLabel('Task 1 title').fill('Pull the message');
  await page.getByLabel('Task 1 due after').fill('30');
  await page.getByRole('button', { name: 'Add a task' }).click();
  await page.getByLabel('Task 2 title').fill('Revoke sessions');
  await page.getByLabel('Task 2 team').selectOption(teams[1].id);
  await page.getByRole('button', { name: 'Create playbook' }).click();
  await expect(page.getByRole('form', { name: 'New playbook' })).toHaveCount(0);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/playbooks')!.body;
  expect(sent).toMatchObject({
    name: 'Phishing',
    trigger_tags: ['phishing', 'credential-theft'],
    applies_to: 'incident',
    is_active: true,
    steps: [
      { title: 'Pull the message', team_id: null, due_minutes: 30 },
      { title: 'Revoke sessions', team_id: teams[1].id, due_minutes: null },
    ],
  });
});

test('responders see no playbook or channel controls', async ({ page }) => {
  await stubLive(page);
  await stubApi(page);
  await page.goto('/people');
  await expect(page.getByRole('heading', { name: 'Directory' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Microsoft Teams channels' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Playbooks' })).toHaveCount(0);
});

test('a team lead connects their own team’s channel and nobody else’s', async ({ page }) => {
  await stubLive(page);
  let connected = false;
  const api = await stubApi(page, me({ role: 'team_lead' }), (method, path) => {
    if (path === '/api/teams') return { json: teams.map((t) => ({ ...t, has_channel: t.id === 't-soc' && connected })) };
    if (path === '/api/invitations') return { json: [] };
    if (method === 'PUT' && path === '/api/teams/t-soc/channel') {
      connected = true;
      return { json: { ...teams[0], has_channel: true } };
    }
  });
  await page.goto('/people');
  const panel = page.locator('section', { has: page.getByRole('heading', { name: 'Microsoft Teams channels' }) });
  await expect(panel.getByRole('row')).toHaveCount(1); // SOC only
  await panel.getByLabel('Workflows webhook URL for SOC').fill('https://prod-1.uksouth.logic.azure.com/workflows/x');
  await panel.getByRole('button', { name: 'Connect' }).click();
  await expect(panel).toContainText('Connected. Send a test');
  await expect(panel.getByRole('button', { name: 'Send a test' })).toBeVisible();
  expect(api.requests.find((r) => r.method === 'PUT')!.body).toEqual({ url: 'https://prod-1.uksouth.logic.azure.com/workflows/x' });
});

test('notification settings save per account and explain what leaves Ostraka', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) =>
    method === 'PATCH' && path === '/api/users/me' ? { json: me(body as Record<string, unknown>) } : undefined,
  );
  await page.goto('/profile?tab=notifications');
  await expect(page.getByText('only the reference is sent')).toBeVisible();
  await page.locator('#notify-email').click();
  await expect(page.locator('#notify-email')).not.toBeChecked();
  expect(api.requests.find((r) => r.method === 'PATCH')!.body).toEqual({ notify_email: false });
});
