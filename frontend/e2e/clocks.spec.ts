import { expect, test } from '@playwright/test';
import { incident, me, sam, stubApi, stubLive } from './fixtures';

const hours = (h: number) => new Date(Date.now() + h * 3600_000).toISOString();

const regimes = [
  {
    key: 'uk_gdpr',
    name: 'UK GDPR',
    authority: 'the ICO',
    timezone: 'Europe/London',
    source: 'UK GDPR Article 33(1)',
    custom: false,
    needs_classification: false,
    milestones: [{ key: 'report', name: 'Report to the ICO', counted: '72 h from awareness' }],
  },
  {
    key: 'dora',
    name: 'DORA',
    authority: 'the competent authority',
    timezone: 'Europe/Brussels',
    source: 'DORA Article 19',
    custom: false,
    needs_classification: true,
    milestones: [{ key: 'initial', name: 'Initial notification', counted: '4 h from classification or 24 h from awareness' }],
  },
  {
    key: 'custom',
    name: 'Custom',
    authority: '',
    timezone: 'Europe/London',
    source: 'Your own',
    custom: true,
    needs_classification: false,
    milestones: [],
  },
];

function clock(overrides: Record<string, unknown> = {}): Record<string, any> {
  return {
    id: 'c-1',
    ref: 'CLK-3',
    regime: 'uk_gdpr',
    regime_name: 'UK GDPR',
    authority: 'the ICO',
    title: 'Payroll share',
    owner: sam,
    aware_at: hours(-66),
    starts_at: null,
    classified_at: null,
    needs_classification: false,
    timezone: 'Europe/London',
    custom_milestones: [],
    milestones: [
      {
        key: 'report',
        name: 'Report to the ICO',
        due_at: hours(6),
        starts_at: hours(-66),
        provisional: false,
        counted: '72 h from awareness',
        state: 'closing',
        met_at: null,
        met_by: null,
        reference: '',
        note: '',
      },
    ],
    closed_at: null,
    closed_by: null,
    close_reason: '',
    created_by: sam,
    created_at: hours(-60),
    version: 2,
    can_manage: true,
    can_mark: true,
    ...overrides,
  };
}

test('a clock counts down live and a milestone is marked met with its reference', async ({ page }) => {
  await stubLive(page);
  let current = clock();
  const api = await stubApi(page, me(), (method, path, body) => {
    if (method === 'GET' && path === '/api/incidents/i-1/clocks') return { json: [current] };
    if (method === 'POST' && path === '/api/incidents/i-1/clocks/c-1/milestones/report/met') {
      const sent = body as { reference: string };
      current = clock({
        version: 3,
        milestones: [{ ...current.milestones[0], state: 'met', met_at: new Date().toISOString(), met_by: sam, reference: sent.reference }],
      });
      return { json: current };
    }
  });
  await page.goto('/incidents/OST-2026-001?s=clocks');

  const timer = page.getByRole('timer', { name: 'Report to the ICO countdown' });
  await expect(timer).toHaveText(/^0[56]:\d\d:\d\d to go$/);
  const first = await timer.textContent();
  await expect(timer).not.toHaveText(first!); // it ticks
  await expect(page.locator('#CLK-3 .clock-milestone')).toHaveAttribute('data-state', 'closing');

  await page.getByRole('button', { name: 'Mark met' }).click();
  await page.getByLabel('Reference').fill('ICO case IC-412873');
  await page.getByRole('form', { name: 'Mark Report to the ICO met' }).getByRole('button', { name: 'Mark met' }).click();
  await expect(page.getByText('ICO case IC-412873')).toBeVisible();
  await expect(page.locator('#CLK-3 .clock-milestone')).toHaveAttribute('data-state', 'met');
  await expect(timer).toHaveCount(0);
  const sent = api.requests.find((r) => r.path.endsWith('/met'))!;
  expect(sent.body).toMatchObject({ version: 2, reference: 'ICO case IC-412873', met_at: null });
});

test('the commander starts a DORA clock with its classification time', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/clocks') return { json: [] };
    if (method === 'GET' && path === '/api/clocks/regimes') return { json: regimes };
    if (method === 'POST' && path === '/api/incidents/i-1/clocks') return { status: 201, json: clock() };
  });
  await page.goto('/incidents/OST-2026-001?s=clocks');
  await expect(page.getByText('No clocks running.')).toBeVisible();
  await page.getByRole('button', { name: 'Start a clock' }).click();
  await page.getByLabel('Regime').selectOption('dora');
  await expect(page.getByText('4 h from classification or 24 h from awareness')).toBeVisible();
  await page.getByLabel('Classified as major').fill('2026-11-10T11:30');
  await page.getByRole('button', { name: 'Start the clock' }).click();
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/clocks')!;
  expect(sent.body).toMatchObject({ regime: 'dora', owner_id: sam.id, milestones: [] });
  expect((sent.body as { classified_at: string }).classified_at).toMatch(/^2026-11-10T\d\d:30:00\.000Z$/);
});

test('a custom clock takes its own milestones', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/clocks') return { json: [] };
    if (method === 'GET' && path === '/api/clocks/regimes') return { json: regimes };
    if (method === 'POST' && path === '/api/incidents/i-1/clocks') return { status: 201, json: clock() };
  });
  await page.goto('/incidents/OST-2026-001?s=clocks');
  await page.getByRole('button', { name: 'Start a clock' }).click();
  await page.getByLabel('Regime').selectOption('custom');
  await page.getByLabel('Whose deadline').fill('Cyber insurer');
  await page.getByLabel('Milestone 1 name').fill('Call the breach line');
  await page.getByRole('button', { name: 'Add a milestone' }).click();
  await page.getByLabel('Milestone 2 name').fill('Proof of loss');
  await page.getByLabel('Milestone 2 amount').fill('30');
  await page.getByLabel('Milestone 2 unit').selectOption('days');
  await page.getByRole('button', { name: 'Start the clock' }).click();
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/clocks')!;
  expect(sent.body).toMatchObject({
    regime: 'custom',
    title: 'Cyber insurer',
    milestones: [
      { name: 'Call the breach line', amount: 48, unit: 'hours', basis: 'aware' },
      { name: 'Proof of loss', amount: 30, unit: 'days', basis: 'previous' },
    ],
  });
});

test('an observer sees clocks but can’t start, change or mark them', async ({ page }) => {
  await stubLive(page);
  const observing = incident({ can_edit: false, can_manage: false, members: [] });
  await stubApi(page, me({ role: 'observer' }), (method, path) => {
    if (path === '/api/incidents/by-ref/OST-2026-001' || path === '/api/incidents/i-1') return { json: observing };
    if (method === 'GET' && path === '/api/incidents/i-1/clocks')
      return { json: [clock({ can_manage: false, can_mark: false })] };
  });
  await page.goto('/incidents/OST-2026-001?s=clocks');
  await expect(page.getByRole('timer', { name: 'Report to the ICO countdown' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start a clock' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Mark met' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Close' })).toHaveCount(0);
});

test('your work lists the next regulator deadline, breached ones first', async ({ page }) => {
  await stubLive(page);
  const deadline = (ref: string, due: string, as_owner: boolean) => ({
    clock_id: ref,
    clock_ref: ref,
    regime_name: 'EU NIS2',
    title: '',
    key: 'notification',
    name: 'Incident notification',
    due_at: due,
    starts_at: hours(-70),
    provisional: false,
    state: 'running',
    owner: sam,
    as_owner,
    incident: { id: 'i-1', ref: 'OST-2026-001', title: 'Phishing leads to a foothold', severity: 'high', tlp: 'amber' },
  });
  await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/clocks/mine')
      return { json: [deadline('CLK-1', hours(-1), true), deadline('CLK-2', hours(30), false)] };
  });
  await page.goto('/work');
  const section = page.getByRole('region', { name: 'Regulator deadlines' });
  await expect(section.getByRole('timer').first()).toHaveText(/overdue$/);
  await expect(section.getByText('Breached')).toBeVisible();
  await expect(section.getByRole('link', { name: 'CLK-2' })).toHaveAttribute('href', '/incidents/OST-2026-001?s=clocks#CLK-2');
  await expect(section.getByText('Owner: Sam Analyst')).toBeVisible();
});
