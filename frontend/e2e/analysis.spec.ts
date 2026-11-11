import { expect, test } from '@playwright/test';
import { finding, incident, me, sam, stubApi, stubLive } from './fixtures';

const confirmed = finding({ id: 'f-1', ref: 'FND-12', title: 'Payroll export staged', status: 'confirmed' });
const lead = finding({ id: 'f-2', ref: 'FND-14', title: 'Rclone seen on FIN-WS-07', status: 'lead' });

test('an assessment is previewed and linted as it is written, and saved from its parts', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) => {
    if (path === '/api/incidents/i-1/findings') return { json: [confirmed, lead] };
    if (method === 'GET' && path === '/api/incidents/i-1/assessments') return { json: [] };
    if (path === '/api/incidents/i-1/assessments/lint') {
      const draft = body as { judgement: string; finding_ids: string[] };
      const flags = /definitely/.test(draft.judgement)
        ? [{ rule: 'certainty-words', severity: 'warning', field: 'judgement', message: 'Certainty is carried by the yardstick.', words: ['definitely'] }]
        : [];
      if (!draft.finding_ids.length)
        flags.push({ rule: 'no-basis', severity: 'error', field: 'basis', message: 'Cite the findings this rests on.', words: [] });
      return { json: { flags, sentences: [`We assess it is likely (55–75%) that ${draft.judgement}.`] } };
    }
    if (method === 'POST' && path === '/api/incidents/i-1/assessments') return { status: 201, json: {} };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=assessments');
  await page.getByRole('button', { name: 'Make an assessment' }).click();
  await page.locator('#assessment-judgement').fill('the actor definitely staged the payroll export');
  await page.locator('#assessment-probability').selectOption('likely');

  const preview = page.getByLabel('What Ostraka will write');
  await expect(preview).toContainText('We assess it is likely (55–75%) that the actor definitely staged');
  await expect(preview).toContainText('Must fix · Basis');
  await expect(preview).toContainText('(definitely)');
  await expect(page.getByRole('button', { name: 'Save assessment' })).toBeDisabled();

  await page.getByLabel('Based on FND-12').check();
  await expect(preview).not.toContainText('Must fix');
  await page.getByRole('button', { name: 'Save assessment' }).click();
  await expect.poll(() => api.requests.find((r) => r.method === 'POST' && r.path.endsWith('/assessments'))?.body).toMatchObject({
    judgement: 'the actor definitely staged the payroll export',
    probability: 'likely',
    finding_ids: ['f-1'],
  });
});

const ach = (mine: string | null) => ({
  hypotheses: [
    { id: 'h-1', ref: 'HYP-1', statement: 'Financially motivated theft', status: 'open', rejection_reason: '', rejected_by: null, rejected_at: null, milestone: '', milestone_task: null, score: 0, leading: true, version: 1 },
    { id: 'h-2', ref: 'HYP-2', statement: 'An authorised export', status: 'rejected', rejection_reason: 'No ticket.', rejected_by: sam, rejected_at: '2026-11-10T10:00:00Z', milestone: '', milestone_task: null, score: -1.41, leading: false, version: 2 },
  ],
  evidence: [
    { id: 'e-1', ref: 'EVD-1', kind: 'absence', finding_id: null, finding_ref: null, observable_id: null, text: 'No change ticket', credibility: 'h', relevance: 'm', non_diagnostic: false, pivotal: true, lead_without: null, created_by: sam, version: 1 },
  ],
  cells: mine ? [{ hypothesis_id: 'h-1', evidence_id: 'e-1', consensus: mine, disputed: false, mine, ratings: [{ rater: sam, value: mine }] }] : [],
  raters: mine ? [sam] : [],
  max_raters: 8,
  leading: 'HYP-1',
  algorithm: 'Weighted inconsistency (v1): each Inconsistent rating counts −1 × credibility × relevance (H 1.414, M 1, L 0.707).',
});

test('the ACH matrix takes your rating and shows the arithmetic and its flags', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/ach') return { json: ach(null) };
    if (method === 'PUT' && path === '/api/incidents/i-1/ach/ratings') return { json: ach('c') };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=ach');
  const matrix = page.getByRole('table', { name: 'ACH matrix' });
  await expect(matrix).toContainText('Leading');
  await expect(matrix).toContainText('Why: No ticket.');
  await expect(matrix).toContainText('If this were wrong, no hypothesis would lead outright.');
  await expect(page.getByText('1.414')).toBeVisible();
  // A rejected hypothesis can't be rated; an open one can
  await expect(page.getByLabel('Your rating of EVD-1 against HYP-2')).toHaveCount(0);
  await page.getByLabel('Your rating of EVD-1 against HYP-1').selectOption('c');
  await expect(page.getByLabel('Your rating of EVD-1 against HYP-1')).toHaveValue('c');
  expect(api.requests.find((r) => r.method === 'PUT')!.body).toEqual({ hypothesis_id: 'h-1', evidence_id: 'e-1', value: 'c' });
});

test('a lesson to improve becomes a task for one of the incident’s teams', async ({ page }) => {
  await stubLive(page);
  const lesson = {
    id: 'l-1', ref: 'LSN-1', kind: 'improve', area: 'detection',
    observation: 'Rclone ran for 40 minutes unnoticed.', recommendation: 'Alert on rclone.exe by hash.',
    task: null, created_by: sam, created_at: '2026-11-10T10:00:00Z', version: 1,
  };
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/lessons') return { json: [lesson] };
    if (method === 'POST' && path === '/api/incidents/i-1/lessons/l-1/task')
      return { json: { ...lesson, task: { id: 't-9', ref: 'TSK-9', status: 'open' } } };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=lessons');
  await expect(page.getByText('What we’d change')).toBeVisible();
  await page.getByRole('button', { name: 'Make it a task' }).click();
  await page.getByRole('form', { name: 'Follow-up task for LSN-1' }).getByRole('button', { name: 'Make the task' }).click();
  await expect.poll(() => api.requests.find((r) => r.path.endsWith('/l-1/task'))?.body).toEqual({ team_id: 't-soc' });
});

test('export downloads the briefing, and is offered only to people who may export', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.route('**/api/incidents/i-1/export?*', (r) =>
    r.fulfill({
      body: '%PDF-1.4 stub',
      headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="OST-2026-001-briefing.pdf"' },
    }),
  );
  await page.goto('/incidents/OST-2026-001');
  await page.locator('summary', { hasText: 'Export' }).click();
  const row = page.locator('.export-row', { hasText: 'Briefing' });
  const [file] = await Promise.all([page.waitForEvent('download'), row.getByRole('menuitem', { name: 'PDF' }).click()]);
  expect(file.suggestedFilename()).toBe('OST-2026-001-briefing.pdf');
});

test('without permission there is no export, and a manager marks the incident sensitive', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) => {
    if (method === 'GET' && (path === '/api/incidents/i-1' || path === '/api/incidents/by-ref/OST-2026-001'))
      return { json: incident({ can_export: false }) };
    if (path === '/api/incidents/i-1' && method === 'PATCH') return { json: incident({ ...(body as object), can_export: false }) };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001');
  await expect(page.getByLabel('Sensitive')).toBeVisible();
  await expect(page.locator('details.export')).toHaveCount(0);
  await page.getByLabel('Sensitive').click();
  await expect.poll(() => api.requests.find((r) => r.method === 'PATCH')?.body).toEqual({ version: 3, sensitive: true });
});
