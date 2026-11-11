import { expect, test } from '@playwright/test';
import { incident, me, sam, stubApi, stubLive } from './fixtures';

const plan = {
  mode: 'severity',
  every_minutes: 240,
  words: 'every 4 h',
  by_severity: [
    { severity: 'critical', every_minutes: 120, words: 'every 2 h' },
    { severity: 'high', every_minutes: 240, words: 'every 4 h' },
    { severity: 'medium', every_minutes: 1440, words: 'daily' },
    { severity: 'low', every_minutes: null, words: 'on change only' },
  ],
  last_sent_at: null,
  next_due_at: '2026-11-10T13:00:00Z',
  set_by: null,
  version: 1,
};

const lines = [
  { section: 'assessment', kind: 'assessment', ref: 'ASM-1', text: 'We assess it is highly likely (80–90%) that the actor staged the payroll export.' },
  { section: 'what', kind: 'confirmed', ref: 'FND-12', text: 'Confirmed: FND-12 Payroll export staged.' },
  { section: 'what', kind: 'lead', ref: 'FND-14', text: 'Lead, not yet confirmed: FND-14 Rclone seen on [ip address].' },
  { section: 'position', kind: 'position', ref: '', text: 'Where things stand: the response is in the scope phase at high severity; 2 tasks open.' },
  { section: 'next', kind: 'next', ref: '', text: 'Next update: within 4 h.' },
];

function draft(overrides: Record<string, unknown> = {}) {
  return {
    id: 'u-1',
    ref: 'UPD-1',
    number: 1,
    status: 'draft',
    since: '2026-11-10T09:00:00Z',
    until: '2026-11-10T11:00:00Z',
    assessment: 'ASM-1',
    lines,
    note: '',
    rephrased_by: '',
    use_rephrased: false,
    flags: [],
    can_approve: true,
    why_not: '',
    drafted_by: null,
    drafted_at: '2026-11-10T11:00:00Z',
    approved_by: null,
    approved_at: null,
    recipients: [],
    revisions: [{ version: 1, change: 'drafted', by: null, at: '2026-11-10T11:00:00Z', lines: [] }],
    version: 1,
    ...overrides,
  };
}

function updates(overrides: Record<string, unknown> = {}) {
  return {
    plan,
    stakeholders: [{ id: 's-1', kind: 'email', name: 'The board', email: 'board@example.org', can_receive: true, why_not: '' }],
    draft: draft(),
    sent: [],
    can_run: true,
    can_approve: true,
    why_not: '',
    email_enabled: true,
    ai: 'ollama',
    ai_why_not: '',
    restricted: false,
    ...overrides,
  };
}

const briefing = {
  ref: 'OST-2026-001',
  title: 'Phishing leads to a foothold on FIN-WS-07',
  severity: 'high',
  tlp: 'amber',
  phase: 'scope',
  status: 'open',
  commander: 'Sam Analyst',
  declared_at: '2026-11-10T09:00:00Z',
  confirmed: 1,
  leads: 1,
  assets: 0,
  contained: 0,
  tasks_open: 2,
  tasks_done: 1,
  assessment: [lines[0].text],
  rhythm: 'every 4 h',
  next_due_at: '2026-11-10T15:00:00Z',
  updates: [{ ref: 'UPD-1', number: 1, approved_at: '2026-11-10T11:05:00Z', approved_by: 'Lee Comms', lines, ai_worded: false }],
};

test('comms checks the draft Ostraka built, adds a note and approves it', async ({ page }) => {
  await stubLive(page);
  let state = updates();
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/updates') return { json: state };
    if (method === 'GET' && path === '/api/incidents/i-1/briefing') return { json: briefing };
    if (method === 'PATCH' && path === '/api/incidents/i-1/updates/u-1') {
      state = updates({ draft: draft({ note: 'Board call at 17:00.', version: 2 }) });
      return { json: state.draft };
    }
    if (method === 'POST' && path === '/api/incidents/i-1/updates/u-1/approve') {
      state = updates({ draft: null, sent: [draft({ status: 'sent', approved_by: sam, approved_at: '2026-11-10T11:05:00Z', can_approve: false })] });
      return { json: state.sent[0] };
    }
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=updates');

  const preview = page.getByLabel('What stakeholders will read');
  await expect(preview).toContainText('We assess it is highly likely (80–90%)');
  await expect(preview).toContainText('Lead, not yet confirmed: FND-14');
  await expect(page.getByLabel('Update rhythm')).toContainText('Updates go every 4 h');
  await expect(page.getByRole('region', { name: 'Stakeholders' })).toContainText('The board');

  await page.getByLabel('Note from comms').fill('Board call at 17:00.');
  await page.getByRole('button', { name: 'Save note' }).click();
  await expect.poll(() => api.requests.find((r) => r.method === 'PATCH')?.body).toEqual({ version: 1, note: 'Board call at 17:00.' });

  await page.getByRole('button', { name: /Approve and send to 1 stakeholder/ }).click();
  await expect.poll(() => api.requests.find((r) => r.path.endsWith('/approve'))?.body).toEqual({ version: 2 });
  await expect(page.getByRole('button', { name: 'Draft an update now' })).toBeVisible();
  await expect(page.locator('#UPD-1')).toContainText('approved by Sam Analyst');
});

test('an update with a linter error cannot be approved', async ({ page }) => {
  await stubLive(page);
  const flags = [{ rule: 'no-assessment', severity: 'error', field: 'assessment', message: 'Every update carries an assessment.', words: [] }];
  await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/updates')
      return { json: updates({ draft: draft({ flags, can_approve: false, why_not: 'Fix the errors first.', lines: lines.slice(1) }) }) };
    if (method === 'GET' && path === '/api/incidents/i-1/briefing') return { json: briefing };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=updates');
  await expect(page.getByLabel('Checks')).toContainText('Must fix · Every update carries an assessment.');
  await expect(page.getByRole('button', { name: /Approve and send/ })).toBeDisabled();
});

test('a rejected AI rewording leaves the draft as Ostraka wrote it', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/updates') return { json: updates() };
    if (method === 'GET' && path === '/api/incidents/i-1/briefing') return { json: briefing };
    if (method === 'POST' && path === '/api/incidents/i-1/updates/u-1/rephrase')
      return { json: { accepted: false, problems: ['Line 3 changed how strongly it’s put (“lead”).'], update: draft() } };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=updates');
  await page.getByRole('button', { name: 'Reword with AI (ollama)' }).click();
  await expect(page.getByText('Ostraka rejected the rewording')).toContainText('Line 3 changed how strongly');
});

test('observers get the read-only briefing: updates in order and where things stand', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me({ role: 'observer' }), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/by-ref/OST-2026-001') return { json: incident({ can_edit: false }) };
    if (method === 'GET' && path === '/api/incidents/i-1') return { json: incident({ can_edit: false }) };
    if (method === 'GET' && path === '/api/incidents/i-1/briefing') return { json: briefing };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=updates');
  const view = page.getByLabel('Briefing');
  await expect(view).toContainText('Confirmed findings');
  await expect(view).toContainText('Update 1');
  await expect(view).toContainText('[ip address]');
  await expect(page.getByRole('button', { name: /Approve/ })).toHaveCount(0);
  expect(api.requests.some((r) => r.path === '/api/incidents/i-1/updates')).toBe(false);
});
