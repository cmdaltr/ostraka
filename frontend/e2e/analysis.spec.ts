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
  await page.goto('/incidents/OST-26-001?s=assessments');
  await page.getByRole('button', { name: 'Make an assessment' }).click();
  await page.locator('#assessment-judgement').fill('the actor definitely staged the payroll export');
  await page.locator('#assessment-probability').selectOption('likely');

  const preview = page.getByLabel('What Ostraka will write');
  await expect(preview).toContainText('We assess it is likely (55–75%) that the actor definitely staged');
  await expect(preview).toContainText('Must fix · Basis');
  await expect(preview).toContainText('(definitely)');
  await expect(page.getByRole('button', { name: 'Save assessment' })).toBeDisabled();

  await page.getByLabel('Based on FND-12').check();
  // the re-check waits for typing to stop, then asks the server: under a full run's load that can outlast 5 s
  await expect(preview).not.toContainText('Must fix', { timeout: 10_000 });
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
  // ACH sits under Assessments; the old address still leads there
  await page.goto('/incidents/OST-26-001?s=ach');
  await expect(page.getByRole('button', { name: 'Assessments' })).toHaveAttribute('aria-current', 'page');
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
    if (path === '/api/incidents/by-ref/OST-26-001' || path === '/api/incidents/i-1')
      return { json: incident({ phase: 'learn' }) };
    if (method === 'GET' && path === '/api/incidents/i-1/lessons') return { json: [lesson] };
    if (method === 'POST' && path === '/api/incidents/i-1/lessons/l-1/task')
      return { json: { ...lesson, task: { id: 't-9', ref: 'TSK-9', status: 'open' } } };
    return undefined;
  });
  // lessons sit in the overview once the incident reaches Learn, and the old address still leads there
  await page.goto('/incidents/OST-26-001?s=lessons');
  await expect(page.getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('What we’d change')).toBeVisible();
  await page.getByRole('button', { name: 'Make it a task' }).click();
  await page.getByRole('form', { name: 'Follow-up task for LSN-1' }).getByRole('button', { name: 'Make the task' }).click();
  await expect.poll(() => api.requests.find((r) => r.path.endsWith('/l-1/task'))?.body).toEqual({ team_id: 't-soc' });
});

test('lessons stay out of the overview until the incident reaches Learn', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) => (method === 'GET' && path === '/api/incidents/i-1/lessons' ? { json: [] } : undefined));
  await page.goto('/incidents/OST-26-001');
  await expect(page.getByRole('heading', { name: 'Summary', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Record a lesson' })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Incident sections' }).getByRole('button')).toHaveText([
    /Overview/, /Tasks/, /Findings/, /Observables/, /Assets/, /Evidence/, /Assessments/, /Updates/, /Timeline/,
  ]);
});

test('the sections fit on one row, and old links land where their section went', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.goto('/incidents/OST-26-001?s=handover');
  const tabs = page.getByRole('navigation', { name: 'Incident sections' });
  await expect(tabs.getByRole('button', { name: 'Updates' })).toHaveAttribute('aria-current', 'page');
  const row = await tabs.evaluate((nav) => ({
    scrolls: nav.scrollWidth > nav.clientWidth,
    rows: new Set([...nav.children].map((b) => (b as HTMLElement).offsetTop)).size,
  }));
  expect(row).toEqual({ scrolls: false, rows: 1 });

  await page.goto('/incidents/OST-26-001?s=activity');
  await expect(tabs.getByRole('button', { name: 'Timeline' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'The attack' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'The work' })).toBeVisible();

  // a phone has no room for one row, so the tabs wrap and every one stays in sight
  await page.setViewportSize({ width: 390, height: 800 });
  expect(await tabs.evaluate((nav) => nav.scrollWidth > nav.clientWidth)).toBe(false);
});

test('the head reads code name and title on one line, and the tabs share the row evenly', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/incidents/OST-26-001');
  const name = await page.locator('.incident-name').evaluate((el) => {
    const [h1, p] = [el.querySelector('h1')!, el.querySelector('p')!].map((x) => x.getBoundingClientRect());
    return { sameLine: Math.abs(h1.bottom - p.bottom) < 12, after: p.left > h1.right };
  });
  expect(name).toEqual({ sameLine: true, after: true });
  const widths = await page
    .getByRole('navigation', { name: 'Incident sections' })
    .evaluate((nav) => [...nav.children].map((b) => b.getBoundingClientRect().width));
  expect(Math.max(...widths) - Math.min(...widths)).toBeLessThan(60);
});

test('scrolling over the discussion never scrolls the page behind it', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.goto('/incidents/OST-26-001');
  await page.getByRole('heading', { name: 'Summary', exact: true }).waitFor();
  const tab = page.getByRole('button', { name: /Discussion/ });
  await tab.hover();
  await page.mouse.wheel(0, 600);
  await tab.click();
  await page.getByRole('complementary', { name: 'Discussion' }).hover();
  await page.mouse.wheel(0, 600);
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('a card folds by its title, stays folded next time, and find-in-page still reaches inside', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.goto('/incidents/OST-26-001');
  const summary = page.locator('.panel', { has: page.getByRole('heading', { name: 'Summary', exact: true }) });
  const facts = summary.locator('.facts');
  await expect(facts).toBeVisible();
  await page.getByRole('heading', { name: 'Summary', exact: true }).click();
  await expect(facts).toBeHidden();
  // hidden until found, not removed: the browser's find searches it
  await expect(facts).toHaveAttribute('hidden', 'until-found');

  await page.reload();
  await expect(page.getByRole('heading', { name: 'Summary', exact: true })).toBeVisible();
  await expect(summary.locator('.facts')).toHaveAttribute('hidden', 'until-found');

  // what the browser does when find-in-page matches inside: beforematch, then the hidden part shows
  await summary.locator('.facts').evaluate((el) => {
    el.dispatchEvent(new Event('beforematch', { bubbles: true }));
    el.removeAttribute('hidden');
  });
  await expect(summary.locator('.facts')).toBeVisible();
  await expect(summary.locator('[hidden]')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.panel', { has: page.getByRole('heading', { name: 'Summary', exact: true }) }).locator('.facts')).toBeVisible();

  // and by keyboard
  await page.getByRole('heading', { name: 'Summary', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.panel', { has: page.getByRole('heading', { name: 'Summary', exact: true }) }).locator('.facts')).toBeHidden();
});

test('Pnyx gives the bearings and answers a free question, beside the discussion', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) =>
    method === 'POST' && path === '/api/incidents/i-1/pnyx/ask'
      ? { json: { answer: 'TSK-4 is yours and due this evening.', provider: 'ollama' } }
      : undefined,
  );
  await page.goto('/incidents/OST-26-001');
  await page.getByRole('button', { name: 'Pnyx' }).click();
  const drawer = page.getByRole('complementary', { name: 'Pnyx' });
  await expect(drawer.getByRole('button', { name: 'What do I do now?' })).toHaveAttribute('aria-pressed', 'true');
  await expect(drawer.getByRole('link', { name: 'TSK-4' })).toHaveAttribute('href', /s=tasks#TSK-4/);
  await drawer.getByRole('button', { name: 'What’s the biggest concern?' }).click();
  await expect(drawer.getByText('1 lead still open')).toBeVisible();

  await drawer.getByRole('textbox').fill('What is mine?');
  await drawer.getByRole('button', { name: 'Ask' }).click();
  await expect(drawer.getByRole('region', { name: 'Pnyx’s answer' })).toContainText('TSK-4 is yours');
  expect(api.requests.find((r) => r.path.endsWith('/pnyx/ask'))?.body).toEqual({ question: 'What is mine?' });

  // one drawer at a time: opening the discussion shuts Pnyx
  await page.getByRole('button', { name: 'Discussion' }).click();
  await expect(drawer).toBeHidden();
  await expect(page.getByRole('complementary', { name: 'Discussion' })).toBeVisible();
});

test('without a model Pnyx still answers its questions and says why it cannot take others', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) =>
    method === 'GET' && path === '/api/incidents/i-1/pnyx'
      ? {
          json: {
            can_ask: false,
            why_not: 'OST-26-001 is TLP:RED, so only a local Ollama model may read it, and none is configured.',
            answers: [{ key: 'now', question: 'What do I do now?', lines: ['Nothing is waiting on you.'] }],
          },
        }
      : undefined,
  );
  await page.goto('/incidents/OST-26-001');
  await page.getByRole('button', { name: 'Pnyx' }).click();
  const drawer = page.getByRole('complementary', { name: 'Pnyx' });
  await expect(drawer.getByText('Nothing is waiting on you.')).toBeVisible();
  await expect(drawer.getByRole('textbox')).toBeDisabled();
  await expect(drawer.getByText(/only a local Ollama model/)).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Ask' })).toHaveCount(0);
});

const step = {
  links: [], techniques: [], suggestions: [], tasks_open: [], tasks_done: 0, call: null, items: [], findings: [], why: '',
};
const flowData = {
  stages: [
    {
      ...step, key: 'phishing', name: 'Phishing', column: 'way', verb: 'Phishes', says: 'Lures', layer: 'seen', coverage: 'covered',
      findings: [{ ref: 'FND-1', title: 'Invoice attachment opened', status: 'confirmed' }],
      techniques: [{ id: 'T1566.001', name: 'Spearphishing Attachment' }], items: ['Dropbox'], tasks_done: 1,
    },
    {
      ...step, key: 'toolkit', name: 'Toolkit', column: 'toolkit', verb: 'Runs', says: 'What it runs', layer: 'seen', coverage: 'focused',
      findings: [{ ref: 'FND-2', title: 'Mimikatz on WS-114', status: 'lead' }], items: ['Stealers: Mimikatz'],
    },
    {
      ...step, key: 'steals_logins', name: 'Steals logins', column: 'action', verb: 'To', says: 'Something it does once in', layer: 'expected',
      coverage: 'open', why: '19 of the 25 ATT&CK groups most like this record (sharing T1566.001) are known to do this.',
      suggestions: [{ id: 'T1003', name: 'OS Credential Dumping', groups: 19 }],
      tasks_open: [{ ref: 'TSK-4', title: 'Check LSASS access' }],
    },
    {
      ...step, key: 'infrastructure', name: 'Infrastructure', column: 'service', verb: 'Connects to', says: 'Its own addresses',
      layer: 'seen', coverage: 'focused', items: ['2 addresses'],
    },
  ],
  dismissed: [{ key: 'destroys', name: 'Encrypts or destroys', note: 'No encryption anywhere', by: 'Dana Lead', at: '2026-11-18T10:00:00Z' }],
  covered: 1,
  peers: 25,
  group: null,
  all_stages: [
    { id: 'phishing', name: 'Phishing', column: 'way' },
    { id: 'toolkit', name: 'Toolkit', column: 'toolkit' },
    { id: 'steals_logins', name: 'Steals logins', column: 'action' },
    { id: 'gathers', name: 'Gathers data', column: 'action' },
    { id: 'infrastructure', name: 'Infrastructure', column: 'service' },
    { id: 'destroys', name: 'Encrypts or destroys', column: 'action' },
  ],
  can_edit: true,
};

test('the attack flow draws the anatomy, with coverage, calls and attribution', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/flow') return { json: flowData };
    if (method === 'PUT' && path.startsWith('/api/incidents/i-1/flow/')) return { status: 204 };
    return undefined;
  });
  await page.goto('/incidents/OST-26-001');
  const panel = page.locator('.attack-flow');
  await expect(panel.getByText('of 4 steps covered')).toBeVisible();
  const drawing = panel.getByRole('group', { name: 'How the attack unfolds' });
  await expect(drawing.getByText('Phishes')).toBeVisible();
  await expect(drawing.getByText('Dropbox')).toBeVisible();
  await expect(drawing.getByText('Stealers: Mimikatz')).toBeVisible();
  await expect(drawing.getByText('Unattributed')).toBeVisible();
  await expect(drawing.getByRole('button', { name: 'Phishing, covered' })).toBeVisible();
  await expect(drawing.getByRole('button', { name: 'Steals logins, to check' })).toBeVisible();

  // the overlay toggles, and is remembered
  await panel.getByLabel('Show coverage').uncheck();
  await expect(drawing.getByRole('button', { name: 'Phishing', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('.attack-flow').getByLabel('Show coverage')).not.toBeChecked();

  // an action opens to what's in it, and takes a call naming where someone looked
  await page.locator('.attack-flow').getByRole('button', { name: 'Steals logins' }).click();
  const detail = page.getByRole('region', { name: 'Steals logins' });
  await expect(detail.getByText(/19 of the 25 ATT&CK groups/)).toBeVisible();
  await expect(detail.getByRole('link', { name: 'T1003' })).toHaveAttribute('href', 'https://attack.mitre.org/techniques/T1003/');
  await detail.getByLabel('Where you looked').fill('EDR LSASS access alerts, 10–14 Nov');
  await detail.getByRole('button', { name: 'Save' }).click();
  await expect
    .poll(() => api.requests.find((r) => r.method === 'PUT' && r.path.endsWith('/flow/stages/steals_logins'))?.body)
    .toEqual({ call: 'cleared', note: 'EDR LSASS access alerts, 10–14 Nov' });

  // a node opens the same way, with what the record names there
  await page.locator('.attack-flow').getByRole('button', { name: 'Toolkit' }).click();
  await expect(page.getByRole('region', { name: 'Toolkit' }).getByText('Stealers: Mimikatz')).toBeVisible();

  await expect(page.getByText('No encryption anywhere')).toBeVisible();
  await page.locator('.attack-flow').getByRole('combobox', { name: 'Step' }).selectOption('gathers');
  await expect(page.locator('.attack-flow').getByLabel('Why')).toBeVisible();
  await page.locator('.attack-flow').getByRole('combobox', { name: 'Attributed to' }).selectOption('G0016');
  await expect
    .poll(() => api.requests.find((r) => r.path.endsWith('/flow/attribution'))?.body)
    .toEqual({ group: 'G0016' });
});

test('with no techniques yet the attack flow says how it builds itself', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.goto('/incidents/OST-26-001');
  await expect(page.locator('.attack-flow').getByText(/Give findings their ATT&CK techniques/)).toBeVisible();
});

test('export downloads the briefing, and is offered only to people who may export', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.route('**/api/incidents/i-1/export?*', (r) =>
    r.fulfill({
      body: '%PDF-1.4 stub',
      headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="OST-26-001-briefing.pdf"' },
    }),
  );
  await page.goto('/incidents/OST-26-001');
  await page.locator('summary', { hasText: 'Export' }).click();
  const row = page.locator('.export-row', { hasText: 'Briefing' });
  const [file] = await Promise.all([page.waitForEvent('download'), row.getByRole('menuitem', { name: 'PDF' }).click()]);
  expect(file.suggestedFilename()).toBe('OST-26-001-briefing.pdf');
});

test('without permission there is no export, and a manager marks the incident sensitive', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) => {
    if (method === 'GET' && (path === '/api/incidents/i-1' || path === '/api/incidents/by-ref/OST-26-001'))
      return { json: incident({ can_export: false }) };
    if (path === '/api/incidents/i-1' && method === 'PATCH') return { json: incident({ ...(body as object), can_export: false }) };
    return undefined;
  });
  await page.goto('/incidents/OST-26-001');
  await expect(page.getByLabel('Sensitive')).toBeVisible();
  await expect(page.locator('details.export')).toHaveCount(0);
  await page.getByLabel('Sensitive').click();
  await expect.poll(() => api.requests.find((r) => r.method === 'PATCH')?.body).toEqual({ version: 3, sensitive: true });
});
