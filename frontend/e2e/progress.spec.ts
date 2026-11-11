// Waiting on work: the sherd's fragments assemble while it runs, then it mends and glows once
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { finding, licence, me, services, stubApi, stubLive } from './fixtures';

const admin = me({ role: 'admin', team: null });

/** Holds every request matching `url` until `open()`, then lets the stubs answer it. */
async function hold(page: Page, url: string) {
  let open!: () => void;
  const gate = new Promise<void>((resolve) => (open = resolve));
  await page.route(url, async (route) => {
    await gate;
    await route.fallback();
  });
  return open;
}

const sherd = (page: Page, label: string) => page.getByRole('status').filter({ hasText: label });

test('opening on the list: one sherd covers signing in and the list, never two', async ({ page }) => {
  await stubLive(page);
  await stubApi(page);
  // Notes every sherd label that ever appears, however briefly
  await page.addInitScript(() => {
    const seen = new Set<string>();
    (window as unknown as { sherds: Set<string> }).sherds = seen;
    new MutationObserver(() => {
      for (const el of document.querySelectorAll('[role="status"]')) seen.add(el.textContent ?? '');
    }).observe(document, { childList: true, subtree: true, characterData: true });
  });
  const open = await hold(page, '**/api/incidents?*');
  await page.goto('/');
  const opening = sherd(page, 'Opening Ostraka');
  await expect(opening.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');

  open();
  await expect(opening.locator('.ostrakon')).toHaveClass(/ostrakon-glow/);
  await expect(page.getByText('Phishing leads to a foothold on FIN-WS-07')).toBeVisible();
  const labels = await page.evaluate(() => [...(window as unknown as { sherds: Set<string> }).sherds]);
  expect(labels.some((l) => l.startsWith('Loading open'))).toBe(false);
});

test('arriving at the list from elsewhere assembles, glows once mended, then shows the list', async ({ page }) => {
  await stubLive(page);
  await stubApi(page);
  await page.goto('/work');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const open = await hold(page, '**/api/incidents?*');
  await page.getByRole('link', { name: 'Incidents' }).click();
  const loading = sherd(page, 'Loading open incidents or hunts');
  await expect(loading.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');
  await expect(page.getByRole('list', { name: 'open incidents or hunts' })).toHaveCount(0);

  open();
  await expect(loading.locator('.ostrakon')).toHaveAttribute('data-state', 'whole');
  await expect(loading.locator('.ostrakon')).toHaveClass(/ostrakon-glow/);
  await expect(page.getByText('Phishing leads to a foothold on FIN-WS-07')).toBeVisible();
  await expect(loading).toHaveCount(0);
});

test('a filter that isn’t cached yet assembles afresh', async ({ page }) => {
  await stubLive(page);
  await stubApi(page);
  await page.goto('/');
  await expect(page.getByText('Phishing leads to a foothold on FIN-WS-07')).toBeVisible();
  const open = await hold(page, '**/api/incidents?status=closed*');
  await page.getByRole('button', { name: 'Closed' }).click();
  await expect(sherd(page, 'Loading closed incidents or hunts').locator('.ostrakon')).toHaveAttribute(
    'data-state',
    'assembling',
  );
  open();
  await expect(sherd(page, 'Loading closed incidents or hunts')).toHaveCount(0);
});

test('your work and the playbooks assemble while they load', async ({ page }) => {
  await stubLive(page);
  await stubApi(page);
  const work = await hold(page, '**/api/tasks/mine');
  await page.goto('/work');
  await expect(sherd(page, 'Loading your work')).toBeVisible();
  work();
  await expect(page.getByText('Nothing assigned to you.')).toBeVisible();

  const playbooks = await hold(page, '**/api/playbooks');
  await page.goto('/playbooks');
  await expect(sherd(page, 'Loading playbooks')).toBeVisible();
  playbooks();
  await expect(page.getByText('No playbooks yet.')).toBeVisible();
});

test('report brands assemble while they load', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, admin, (_method, path) => (path === '/api/licence' ? { json: services } : undefined));
  const open = await hold(page, '**/api/brands');
  await page.goto('/installation?tab=brands');
  await expect(sherd(page, 'Loading report brands')).toBeVisible();
  open();
  await expect(page.getByRole('form', { name: 'New brand' })).toBeVisible();
  await expect(sherd(page, 'Loading report brands')).toHaveCount(0);
});

test('the webhook list mends and glows before it shows', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, admin, (_method, path) =>
    path === '/api/webhooks' ? { json: { events: ['incident.created'], sending: false, webhooks: [] } } : undefined,
  );
  const open = await hold(page, '**/api/webhooks');
  await page.goto('/installation?tab=webhooks');
  const loading = sherd(page, 'Loading webhooks');
  await expect(loading.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');
  open();
  await expect(loading.locator('.ostrakon')).toHaveClass(/ostrakon-glow/);
  await expect(page.getByRole('form', { name: 'New webhook' })).toBeVisible();
});

test('checking a licence assembles in the button while it runs', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, admin, (_method, path) => {
    if (path === '/api/licence/check')
      return {
        json: { licence_id: 'lic_1', licensee: 'Northwind Ltd', edition: 'pro', seats: 10, features: [], not_before: null, expires: null, state: 'active' },
      };
    if (path === '/api/licence/terms') return { json: { version: '1.0', text: 'Terms' } };
    if (path === '/api/licence') return { json: licence() };
    return undefined;
  });
  const open = await hold(page, '**/api/licence/check');
  await page.goto('/installation');
  await page.locator('#licence-text').fill('ostraka-licence-1.payload.signature');
  await page.getByRole('button', { name: 'Check' }).click();
  const button = page.getByRole('button', { name: /Checking…/ });
  await expect(button).toBeDisabled();
  await expect(button.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');
  open();
  await expect(page.getByRole('button', { name: 'Install' })).toBeVisible();
});

test('sending a webhook test assembles, then glows once it’s delivered', async ({ page }) => {
  await stubLive(page);
  const hook = {
    id: 'w-1',
    url: 'https://soar.example.org/ostraka',
    events: ['incident.created'],
    description: '',
    enabled: true,
    has_secret: true,
    created_at: '2026-11-11T10:00:00Z',
    version: 1,
    deliveries: [],
    secret: null,
  };
  await stubApi(page, admin, (method, path) => {
    if (method === 'GET' && path === '/api/webhooks') return { json: { events: hook.events, sending: true, webhooks: [hook] } };
    if (method === 'POST' && path === '/api/webhooks/w-1/test')
      return { json: { event: 'incident.created', at: '2026-11-11T11:00:00Z', ok: true, status: 204, error: '', tries: 1 } };
    return undefined;
  });
  const open = await hold(page, '**/api/webhooks/w-1/test');
  await page.goto('/installation?tab=webhooks');
  const panel = page.getByRole('region', { name: `Webhook ${hook.url}` });
  await panel.getByRole('button', { name: 'Send a test' }).click();
  const sending = panel.getByRole('button', { name: /Sending…/ });
  await expect(sending.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');

  open();
  const sent = panel.getByRole('button', { name: /Sent/ });
  await expect(sent.locator('.ostrakon')).toHaveClass(/ostrakon-glow/);
  await expect(panel.getByText('Test delivered (204).')).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Send a test' })).toBeVisible();
});

test('a failed webhook test doesn’t glow', async ({ page }) => {
  await stubLive(page);
  const hook = { id: 'w-1', url: 'https://soar.example.org/ostraka', events: [], description: '', enabled: true, has_secret: true, created_at: '2026-11-11T10:00:00Z', version: 1, deliveries: [], secret: null };
  await stubApi(page, admin, (method, path) => {
    if (method === 'GET' && path === '/api/webhooks') return { json: { events: [], sending: true, webhooks: [hook] } };
    if (method === 'POST' && path === '/api/webhooks/w-1/test')
      return { json: { event: 'incident.created', at: '2026-11-11T11:00:00Z', ok: false, status: 503, error: 'HTTP 503', tries: 1 } };
    return undefined;
  });
  await page.goto('/installation?tab=webhooks');
  const panel = page.getByRole('region', { name: `Webhook ${hook.url}` });
  await panel.getByRole('button', { name: 'Send a test' }).click();
  await expect(panel.getByText('Test failed: HTTP 503.')).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Send a test' })).toBeVisible();
  await expect(panel.locator('.ostrakon-glow')).toHaveCount(0);
});

test('the sign-in button assembles while the password is checked', async ({ page }) => {
  let open!: () => void;
  const gate = new Promise<void>((resolve) => (open = resolve));
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/login') {
      await gate;
      return route.fulfill({ json: { mfa_required: true, ticket: 'ticket' } });
    }
    return route.fulfill({ status: 401, json: { detail: 'Sign in to continue.' } });
  });
  await page.goto('/');
  await page.locator('#email').fill('sam@example.org');
  await page.locator('#password').fill('correct horse battery staple');
  await page.getByRole('button', { name: 'Continue' }).click();
  const button = page.getByRole('button', { name: /Checking…/ });
  await expect(button).toBeDisabled();
  await expect(button.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');
  open();
  await expect(page.getByRole('heading', { name: 'Enter your authenticator code' })).toBeVisible();
});

test('the assessment check assembles while it runs, then glows', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path, body) => {
    if (path === '/api/incidents/i-1/findings') return { json: [finding({ status: 'confirmed' })] };
    if (method === 'GET' && path === '/api/incidents/i-1/assessments') return { json: [] };
    if (path === '/api/incidents/i-1/assessments/lint')
      return { json: { flags: [], sentences: [`We assess it is likely (55–75%) that ${(body as { judgement: string }).judgement}.`] } };
    return undefined;
  });
  const open = await hold(page, '**/api/incidents/i-1/assessments/lint');
  await page.goto('/incidents/OST-2026-001?s=assessments');
  await page.getByRole('button', { name: 'Make an assessment' }).click();
  await page.locator('#assessment-judgement').fill('the actor staged the payroll export');

  const preview = page.getByLabel('What Ostraka will write');
  await expect(preview.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');
  open();
  await expect(preview).toContainText('We assess it is likely (55–75%) that the actor staged the payroll export.');
  await expect(preview.locator('.ostrakon')).toHaveClass(/ostrakon-glow/);
  await expect(preview.locator('.ostrakon')).toHaveCount(0);
});

test('accepting an invitation assembles in the button while the password is set', async ({ page }) => {
  await page.route('**/api/invitations/accept/tok', (r) =>
    r.fulfill({ json: { email: 'new@example.org', role: 'responder', team: 'DFIR', is_reset: false } }),
  );
  // The newest route runs first, so the hold goes on top of the answer
  await page.route('**/api/invitations/accept/tok/start', (r) => r.fulfill({ status: 400, json: { detail: 'Too weak' } }));
  const open = await hold(page, '**/api/invitations/accept/tok/start');
  await page.goto('/invite/tok');
  await page.getByLabel('Your name').fill('New Person');
  await page.getByLabel('Password', { exact: true }).fill('correct horse battery staple');
  await page.getByLabel('Password again').fill('correct horse battery staple');
  await page.getByRole('button', { name: /Continue/ }).click();
  const button = page.getByRole('button', { name: /Setting your password/ });
  await expect(button.locator('.ostrakon')).toHaveAttribute('data-state', 'assembling');
  await expect(button).toBeDisabled();
  open();
  await expect(page.getByText('Too weak')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled();
});
