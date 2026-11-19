import { expect, test } from '@playwright/test';
import { dana, me, stubApi, stubLive, teams } from './fixtures';

// Playbooks on the attack's anatomy: written part by part, applied part by part

const phishing = {
  id: 'pb-1',
  name: 'Phishing and BEC',
  description: 'A reported lure',
  trigger_tags: ['phishing'],
  applies_to: 'incident',
  is_active: true,
  conditions: { credentials: 'someone entered credentials' },
  checks: [
    { id: 'c-1', part: 'phishing', title: 'Pull the message and its headers', team: teams[0] },
    { id: 'c-2', part: 'phishing', title: 'Find every recipient', team: teams[0] },
    { id: 'c-3', part: 'credentials', title: 'Reset the password', team: teams[1] },
    { id: 'c-4', part: 'hides', title: 'Inspect mailbox rules', team: null },
    { id: 'c-5', part: 'any', title: 'Tell the line manager', team: null },
  ],
  updated_at: '2026-11-18T09:00:00Z',
  version: 3,
};

test('a team lead writes a playbook on the anatomy: a part, its checks and teams, an only if', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me({ role: 'team_lead' }), (method, path) => {
    if (method === 'GET' && path === '/api/playbooks') return { json: [] };
    if (method === 'POST' && path === '/api/playbooks') return { status: 201, json: phishing };
  });
  await page.goto('/playbooks');
  await page.getByRole('button', { name: 'New playbook' }).click();
  const form = page.getByRole('form', { name: 'New playbook' });
  await form.locator('#playbook-name').fill('Phishing and BEC');
  await form.locator('#playbook-tags').fill('phishing');

  // every part of the anatomy is drawn; one without checks says so
  const drawing = form.getByRole('group', { name: 'The playbook on the attack’s anatomy' });
  await expect(drawing.getByRole('button', { name: 'Exploit, not in this playbook' })).toBeVisible();
  await expect(drawing.getByRole('button', { name: 'Infrastructure, not in this playbook' })).toBeVisible();

  // Phishing is selected to start with: two checks, Enter adds each, the first for the SOC
  const add = form.getByLabel('Add a check to Phishing');
  await add.fill('Pull the message and its headers');
  await add.press('Enter');
  await add.fill('Find every recipient');
  await add.press('Enter');
  await form.getByLabel('Team for check 1').selectOption(teams[0].id);
  await expect(drawing.getByRole('button', { name: 'Phishing, 2 checks' })).toBeVisible();
  await form.getByRole('button', { name: 'Move check 2 up' }).click();
  await expect(form.getByLabel('Check 1', { exact: true })).toHaveValue('Find every recipient');

  // Credentials, only if someone entered them
  await drawing.getByRole('button', { name: 'Credentials, not in this playbook' }).click();
  await form.getByLabel('Add a check to Credentials').fill('Reset the password');
  await form.getByLabel('Add a check to Credentials').press('Enter');
  await form.getByRole('button', { name: 'Only if…' }).click();
  await form.getByLabel('Only if').fill('someone entered credentials');

  // an action, chosen from the Actions node's list
  await drawing.getByRole('button', { name: 'Actions', exact: true }).click();
  await form.getByRole('group', { name: 'Actions' }).getByRole('button', { name: 'Hides its tracks' }).click();
  await form.getByLabel('Add a check to Hides its tracks').fill('Inspect mailbox rules');
  await form.getByLabel('Add a check to Hides its tracks').press('Enter');
  await expect(drawing.getByRole('button', { name: 'Hides its tracks, 1 check' })).toBeVisible();

  // and one not tied to a step
  await form.getByRole('button', { name: '0 checks not tied to a step' }).click();
  await form.getByLabel('Add a check to Not tied to a step').fill('Tell the line manager');
  await form.getByLabel('Add a check to Not tied to a step').press('Enter');
  await expect(form.getByText('4 checks in 3 parts of the attack')).toBeVisible();

  await form.getByRole('button', { name: 'Create playbook' }).click();
  await expect(page.getByRole('form', { name: 'New playbook' })).toHaveCount(0);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/playbooks')!.body;
  expect(sent).toEqual({
    name: 'Phishing and BEC',
    description: '',
    trigger_tags: ['phishing'],
    applies_to: 'incident',
    is_active: true,
    conditions: { credentials: 'someone entered credentials' },
    checks: [
      { part: 'phishing', title: 'Find every recipient', team_id: null },
      { part: 'phishing', title: 'Pull the message and its headers', team_id: teams[0].id },
      { part: 'credentials', title: 'Reset the password', team_id: null },
      { part: 'hides', title: 'Inspect mailbox rules', team_id: null },
      { part: 'any', title: 'Tell the line manager', team_id: null },
    ],
  });
});

test('a playbook card draws only its own parts, and says its size', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me({ role: 'team_lead' }), (_method, path) => (path === '/api/playbooks' ? { json: [phishing] } : undefined));
  await page.goto('/playbooks');
  await expect(page.locator('.playbook-summary')).toHaveText(/5 checks.*3 parts of the attack.*1 only if….*1 not tied to a step.*2 teams/);
  const drawing = page.getByRole('group', { name: 'Phishing and BEC on the attack’s anatomy' });
  await expect(drawing.getByText('Phishing', { exact: true })).toBeVisible();
  await expect(drawing.getByText('Exploit', { exact: true })).toHaveCount(0);
});

const run = (parts: string[], remaining: string[]) => ({
  playbook_id: 'pb-1',
  name: 'Phishing and BEC',
  applied_by: dana,
  applied_at: '2026-11-18T09:00:00Z',
  parts,
  remaining,
  conditions: phishing.conditions,
});

test('applying shows every part of the anatomy; one, all or none can be selected, an only if starts out', async ({ page }) => {
  await stubLive(page);
  let applied: unknown[] = [];
  const api = await stubApi(page, me(), (method, path) => {
    if (path === '/api/playbooks') return { json: [phishing] };
    if (method === 'GET' && path === '/api/incidents/i-1/playbooks') return { json: applied };
    if (method === 'POST' && path === '/api/incidents/i-1/playbooks/pb-1') {
      applied = [run(['phishing', 'hides', 'any'], ['credentials'])];
      return { status: 201, json: [] };
    }
  });
  await page.goto('/incidents/OST-26-001?s=tasks');
  await page.getByLabel('Apply a playbook').selectOption('pb-1');
  const panel = page.getByRole('region', { name: 'Apply Phishing and BEC' });
  const drawing = panel.getByRole('group', { name: 'Phishing and BEC: the parts to apply' });

  // all of the anatomy is drawn; only the parts with checks can be chosen
  await expect(drawing.getByText('Exploit', { exact: true })).toBeVisible();
  await expect(drawing.getByText('Infrastructure', { exact: true })).toBeVisible();
  await expect(drawing.getByRole('button', { name: /^Exploit/ })).toHaveCount(0);
  const phish = drawing.getByRole('button', { name: 'Phishing, 2 checks' });
  const creds = drawing.getByRole('button', { name: 'Credentials, 1 check' });
  await expect(phish).toHaveAttribute('aria-pressed', 'true');
  await expect(creds).toHaveAttribute('aria-pressed', 'false'); // only if someone entered credentials
  await expect(panel.getByText('Credentials (only if someone entered credentials)')).toBeVisible();
  await expect(panel.getByText('Adds 4 tasks for 2 parts of the attack, including the 1 check not tied to a step.')).toBeVisible();

  await panel.getByRole('button', { name: 'Select all' }).click();
  await expect(creds).toHaveAttribute('aria-pressed', 'true');
  await expect(panel.getByText(/Adds 5 tasks for 3 parts/)).toBeVisible();
  await panel.getByRole('button', { name: 'Select none' }).click();
  await expect(panel.getByRole('button', { name: 'Apply', exact: true })).toBeDisabled();
  await phish.click();
  await drawing.getByRole('button', { name: 'Hides its tracks, 1 check' }).click();
  await panel.getByLabel('The 1 check not tied to a step').check();
  await panel.getByRole('button', { name: 'Apply', exact: true }).click();
  await expect(panel).toHaveCount(0);
  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/playbooks/pb-1')!.body;
  expect(sent).toEqual({ parts: ['phishing', 'hides', 'any'] });

  // what's left can be applied later, the parts already applied ticked and no longer chosen
  await expect(page.getByText(/1 part left: Credentials/)).toBeVisible();
  await page.getByRole('button', { name: 'apply more' }).click();
  const again = page.getByRole('region', { name: 'Apply Phishing and BEC' });
  await expect(again.getByRole('button', { name: /^Phishing/ })).toHaveCount(0);
  await expect(again.getByText('Applied').first()).toBeVisible();
  await again.getByRole('button', { name: 'Credentials, 1 check' }).click();
  await again.getByRole('button', { name: 'Apply', exact: true }).click();
  expect(api.requests.filter((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/playbooks/pb-1').at(-1)!.body).toEqual({
    parts: ['credentials'],
  });
});
