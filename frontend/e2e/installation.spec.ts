import { expect, test } from '@playwright/test';
import { brand, incident, licence, me, services, stubApi, stubLive } from './fixtures';

const admin = me({ role: 'admin', team: null });

test('an admin checks a licence, accepts the terms for its licensee, and installs it', async ({ page }) => {
  await stubLive(page);
  const line = 'ostraka-licence-1.payload.signature';
  const api = await stubApi(page, admin, (method, path) => {
    if (path === '/api/licence/check')
      return {
        json: { licence_id: 'lic_1', licensee: 'Northwind Ltd', edition: 'services', seats: 25, features: [], not_before: null, expires: '2027-11-30', state: 'active' },
      };
    if (path === '/api/licence/terms') return { json: { version: '1.0', text: '# Ostraka Commercial Licence\n\nVersion 1.0' } };
    if (method === 'PUT' && path === '/api/licence')
      return { json: { ...services, accepted: { by: 'Sam Analyst <sam@example.org>', at: '2026-11-11T10:00:00Z', terms_version: '1.0' } } };
    return undefined;
  });
  await page.goto('/installation');
  await expect(page.getByRole('heading', { name: 'Free edition' })).toBeVisible();
  await expect(page.getByText('2 of 4')).toBeVisible();
  await page.locator('#licence-text').fill(line);
  await page.getByRole('button', { name: 'Check' }).click();

  await expect(page.getByLabel('Commercial licence, version 1.0', { exact: true })).toContainText('Ostraka Commercial Licence');
  const install = page.getByRole('button', { name: 'Install' });
  await expect(install).toBeDisabled();
  await page.getByLabel('I accept the Ostraka Commercial Licence, version 1.0, on behalf of Northwind Ltd').check();
  await install.click();

  await expect(page.getByText('Installed: Services for Northwind Ltd.')).toBeVisible();
  await expect(page.getByText('Commercial licence v1.0 accepted by Sam Analyst <sam@example.org> on 11 November 2026.')).toBeVisible();
  expect(api.requests.find((r) => r.method === 'PUT')!.body).toEqual({ text: line, accept_terms: true, terms_version: '1.0' });
});

test('only admins have the Installation page, and the free edition explains branding', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me());
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Incidents' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Installation' })).toHaveCount(0);

  await stubApi(page, admin);
  await page.goto('/installation?tab=brands');
  await expect(page.getByText('With a Pro licence they carry your colours')).toBeVisible();
  await expect(page.getByRole('form', { name: 'New brand' })).toHaveCount(0);
});

test('an Services admin edits a brand, uploads a logo and adds another brand', async ({ page }) => {
  await stubLive(page);
  const yellow = brand({ primary: '#ffeb3b', notes: ['The primary colour #ffeb3b is too light for headings on a white page.'] });
  const api = await stubApi(page, admin, (method, path) => {
    if (path === '/api/licence') return { json: services };
    if (method === 'GET' && path === '/api/brands') return { json: [yellow] };
    if (method === 'PUT' && path === '/api/brands/b-1/logos/cover') return { json: { ...yellow, logos: ['cover'], version: 2 } };
    if (method === 'POST' && path === '/api/brands') return { status: 201, json: brand({ id: 'b-2', name: 'Contoso', is_default: false }) };
    return undefined;
  });
  await page.route('**/api/brands/b-1/logos/cover', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ body: Buffer.from('89504e470d0a1a0a', 'hex'), contentType: 'image/png' })
      : route.fallback(),
  );
  await page.goto('/installation?tab=brands');
  const editor = page.getByRole('region', { name: 'Brand Northwind' });
  await expect(editor.getByText('too light for headings')).toBeVisible();
  await expect(editor.getByText('Severity and TLP colours never change')).toBeVisible();

  await editor.getByLabel('Cover logo file').setInputFiles({
    name: 'logo.png',
    mimeType: 'image/png',
    buffer: Buffer.from('89504e470d0a1a0a', 'hex'),
  });
  await expect(editor.getByText('Replace')).toBeVisible();
  expect(api.requests.find((r) => r.method === 'PUT' && r.path === '/api/brands/b-1/logos/cover')?.body).toEqual({
    bytes: 8,
  });

  const form = page.getByRole('form', { name: 'New brand' });
  await form.getByLabel('Name').fill('Contoso');
  await form.getByLabel('Primary colour', { exact: true }).fill('#107C41');
  await form.getByRole('button', { name: 'Add brand' }).click();
  await expect
    .poll(() => api.requests.find((r) => r.method === 'POST' && r.path === '/api/brands')?.body)
    .toEqual({ name: 'Contoso', primary: '#107c41', secondary: '', prepared_for: '' });
});

test('under Services an incident’s commander chooses its report brand', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) => {
    if (path === '/api/licence') return { json: { ...services, places_held: null } };
    if (method === 'GET' && path === '/api/brands')
      return { json: [brand(), brand({ id: 'b-2', name: 'Contoso', is_default: false })] };
    if (method === 'PATCH' && path === '/api/incidents/i-1') return { json: incident(body as Record<string, unknown>) };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001');
  const picker = page.getByLabel('Report brand');
  await expect(picker.locator('option')).toHaveText(['Northwind (default)', 'Contoso']);
  await picker.selectOption('b-2');
  await expect.poll(() => api.requests.find((r) => r.method === 'PATCH')?.body).toEqual({ version: 3, brand_id: 'b-2' });
});

test('admins are told when the licence has run out, wherever they are', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, admin, (_method, path) =>
    path === '/api/licence' ? { json: { ...services, state: 'grace', expires: '2026-11-01', grace_ends: '2026-12-01' } } : undefined,
  );
  await page.goto('/');
  await expect(page.getByRole('status').filter({ hasText: 'keeps working until 1 December 2026' })).toBeVisible();
  await page.getByRole('link', { name: 'Install the renewal' }).click();
  await expect(page).toHaveURL(/\/installation$/);
});

test('people managers see how many places are taken', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, admin, (method, path) => {
    if (path === '/api/licence') return { json: licence({ places_held: 4 }) };
    if (path === '/api/invitations' && method === 'GET') return { json: [] };
    return undefined;
  });
  await page.goto('/people');
  await expect(page.getByLabel('Places')).toHaveText('4 of 4 places taken (Free edition)');
});

const events = ['incident.created', 'incident.status_changed', 'incident.closed', 'task.assigned', 'hunt.completed'];
const hook = {
  id: 'w-1',
  url: 'https://soar.example.org/ostraka',
  events: ['incident.created'],
  description: 'SOAR',
  enabled: true,
  has_secret: true,
  created_at: '2026-11-11T10:00:00Z',
  version: 1,
  deliveries: [
    { event: 'incident.created', at: '2026-11-11T10:05:00Z', ok: true, status: 200, error: '', tries: 1 },
    { event: 'incident.created', at: '2026-11-11T10:01:00Z', ok: false, status: 503, error: 'HTTP 503', tries: 3 },
  ],
  secret: null,
};

test('on Free, webhooks are kept but the page says they are not sent', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, admin, (_method, path) => {
    if (path === '/api/webhooks') return { json: { events, sending: false, webhooks: [hook] } };
    return undefined;
  });
  await page.goto('/installation?tab=webhooks');
  await expect(page.getByText('Webhooks are part of the Pro and Services editions.')).toBeVisible();
  await expect(page.getByRole('region', { name: `Webhook ${hook.url}` })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send a test' })).toBeDisabled();
});

test('a Pro admin adds a webhook, sees its secret once, and reads its deliveries', async ({ page }) => {
  await stubLive(page);
  let list = [hook];
  const api = await stubApi(page, admin, (method, path) => {
    if (path === '/api/licence') return { json: licence({ edition: 'pro', state: 'active', features: ['automation', 'report-branding'] }) };
    if (method === 'GET' && path === '/api/webhooks') return { json: { events, sending: true, webhooks: list } };
    if (method === 'POST' && path === '/api/webhooks') {
      const made = { ...hook, id: 'w-2', url: 'https://hooks.example.org/in', events: ['incident.closed', 'task.assigned'], deliveries: [] };
      list = [hook, made];
      return { status: 201, json: { ...made, secret: 'shown-once-secret-value' } };
    }
    if (method === 'POST' && path === '/api/webhooks/w-1/test')
      return { json: { event: 'incident.created', at: '2026-11-11T11:00:00Z', ok: true, status: 204, error: '', tries: 1 } };
    return undefined;
  });
  await page.goto('/installation?tab=webhooks');
  const existing = page.getByRole('region', { name: `Webhook ${hook.url}` });
  await expect(existing.getByText('Delivered (200)')).toBeVisible();
  await expect(existing.getByText('HTTP 503, after 3 tries')).toBeVisible();
  await existing.getByRole('button', { name: 'Send a test' }).click();
  await expect(existing.getByText('Test delivered (204).')).toBeVisible();

  const form = page.getByRole('form', { name: 'New webhook' });
  await form.getByLabel('URL').fill('https://hooks.example.org/in');
  await form.getByLabel('An incident is closed').check();
  await form.getByLabel('A task is given to someone or claimed').check();
  await form.getByRole('button', { name: 'Add webhook' }).click();

  await expect(page.getByText('shown-once-secret-value')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Webhook https://hooks.example.org/in' })).toBeVisible();
  expect(api.requests.find((r) => r.method === 'POST' && r.path === '/api/webhooks')!.body).toEqual({
    url: 'https://hooks.example.org/in',
    description: '',
    events: ['incident.closed', 'task.assigned'],
  });
});
