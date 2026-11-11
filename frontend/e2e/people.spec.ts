import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const teams = [
  { id: 't-soc', name: 'SOC', description: '' },
  { id: 't-dfir', name: 'DFIR', description: '' },
];

function me(overrides: Record<string, unknown> = {}) {
  return {
    id: 'u-1',
    name: 'Sam Analyst',
    email: 'sam@example.org',
    role: 'responder',
    team: teams[0],
    is_active: true,
    job_title: '',
    availability: 'available',
    timezone: 'Europe/London',
    phone: '',
    theme_palette: 'cobalt',
    theme_mode: 'system',
    show_raw_iocs: false,
    notify_email: true,
    notify_push: true,
    last_login_at: null,
    backup_codes_left: 10,
    muted_kinds: [],
    quiet_from: null,
    quiet_until: null,
    new_device_alerts: true,
    ...overrides,
  };
}

async function signedOut(page: Page) {
  await page.route('**/api/incidents?*', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/auth/refresh', (r) => r.fulfill({ status: 401, json: { detail: 'Sign in to continue.' } }));
}

async function signedInAs(page: Page, who: ReturnType<typeof me>) {
  await page.route('**/api/auth/refresh', (r) => r.fulfill({ json: { access_token: 'access', expires_in: 900 } }));
  await page.route('**/api/users/me', (r) => r.fulfill({ json: who }));
  await page.route('**/api/teams', (r) => r.fulfill({ json: teams }));
  await page.route('**/api/users', (r) => r.fulfill({ json: [who] }));
  await page.route('**/api/invitations', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/tokens', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/incidents?*', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/notifications', (r) => r.fulfill({ json: { items: [], unread: 0 } }));
  await page.route('**/api/live/ticket', (r) => r.fulfill({ status: 503, json: { detail: 'Not in tests' } }));
}

test('signing in takes a password and then an authenticator code', async ({ page }) => {
  await signedOut(page);
  await page.route('**/api/auth/login', (r) => r.fulfill({ json: { mfa_required: true, ticket: 'ticket' } }));
  let codeSent = '';
  await page.route('**/api/auth/mfa', (r) => {
    codeSent = r.request().postDataJSON().code;
    return r.fulfill({ json: { access_token: 'access', expires_in: 900 } });
  });
  await page.route('**/api/users/me', (r) => r.fulfill({ json: me({ theme_palette: 'ledger' }) }));

  await page.goto('/');
  await expect(page.getByRole('img', { name: 'Ostraka' })).toBeVisible();
  await page.locator('#email').fill('sam@example.org');
  await page.locator('#password').fill('correct horse battery staple');
  await page.getByRole('button', { name: 'Continue' }).click();

  await expect(page.getByRole('heading', { name: 'Enter your authenticator code' })).toBeVisible();
  await page.locator('#code').fill('123456');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page.getByText('Hello, Sam.')).toBeVisible();
  expect(codeSent).toBe('123456');
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'ledger'); // the saved theme follows the account
});

test('with reduced motion the board holds the name and the glyphs stand still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await signedOut(page);
  await page.goto('/');
  const board = page.locator('.splitflap');
  await expect(board).toHaveText('OSTRAKA');
  await page.waitForTimeout(3600);
  await expect(board).toHaveText('OSTRAKA');
  await expect(page.locator('.cipher-field')).toBeAttached();
});

test('sign-in shows the clay sherd in light mode and the limestone one in dark', async ({ page }) => {
  await signedOut(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/');
  const piece = page.locator('.brandmark .ostrakon-piece').first();
  const artwork = () => piece.evaluate((el) => getComputedStyle(el).backgroundImage);
  await expect.poll(artwork).toContain('ostrakon-clay.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect.poll(artwork).toContain('ostrakon-limestone.png');
});

test('clicking the sherd breaks it apart, and it mends itself', async ({ page }) => {
  await signedOut(page);
  await page.goto('/');
  const sherd = page.getByRole('button', { name: 'Ostraka: the mended sherd' });
  await expect(sherd.locator('.ostrakon')).toHaveAttribute('data-state', 'whole');
  await sherd.click();
  await expect(sherd.locator('.ostrakon')).toHaveAttribute('data-state', 'scattered');
  await expect(sherd.locator('.ostrakon')).toHaveAttribute('data-state', 'whole', { timeout: 3000 });
});

test('a wrong password shows the server’s message and stays on the first step', async ({ page }) => {
  await signedOut(page);
  await page.route('**/api/auth/login', (r) =>
    r.fulfill({ status: 401, json: { detail: "That email and password don't match." } }),
  );
  await page.goto('/');
  await page.locator('#email').fill('sam@example.org');
  await page.locator('#password').fill('wrong password here');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('alert')).toHaveText("That email and password don't match.");
  await expect(page.locator('#password')).toBeVisible();
});

test('accepting an invitation: password, authenticator, then backup codes that must be saved', async ({ page }) => {
  await signedOut(page);
  const svg = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>').toString('base64');
  await page.route('**/api/invitations/accept/tok', (r) =>
    r.fulfill({ json: { email: 'new@example.org', role: 'responder', team: 'DFIR', is_reset: false, expires_at: '2026-11-17T12:00:00Z' } }),
  );
  await page.route('**/api/invitations/accept/tok/start', (r) =>
    r.fulfill({ json: { otpauth_uri: 'otpauth://totp/x', qr_svg: svg, secret: 'JBSWY3DPEHPK3PXP' } }),
  );
  const codes = Array.from({ length: 10 }, (_, i) => `ABCD${i}-EFGH${i}`);
  await page.route('**/api/invitations/accept/tok/complete', (r) =>
    r.fulfill({ json: { access_token: 'access', expires_in: 900, backup_codes: codes } }),
  );
  await page.route('**/api/users/me', (r) => r.fulfill({ json: me({ name: 'New Person' }) }));

  await page.goto('/invite/tok');
  await expect(page.getByText('invited as responder in DFIR')).toBeVisible();
  await page.locator('#name').fill('New Person');
  await page.locator('#password').fill('a long enough passphrase');
  await page.locator('#confirm').fill('a different passphrase!');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('alert')).toHaveText('The two passwords don’t match.');

  await page.locator('#confirm').fill('a long enough passphrase');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByAltText('QR code for your authenticator app')).toBeVisible();
  await page.locator('#code').fill('654321');
  await page.getByRole('button', { name: 'Confirm' }).click();

  await expect(page.getByRole('list', { name: 'Backup codes' }).getByRole('listitem')).toHaveCount(10);
  const continueButton = page.getByRole('button', { name: 'Continue' });
  await expect(continueButton).toBeDisabled();
  await page.getByText('I’ve saved these codes').click();
  await continueButton.click();
  await expect(page.getByText('Hello, New.')).toBeVisible();
});

test('a team lead invites only responders and observers, into their own team', async ({ page }) => {
  await signedInAs(page, me({ role: 'team_lead' }));
  let sent: Record<string, unknown> = {};
  await page.route('**/api/invitations', (r) => {
    if (r.request().method() === 'POST') {
      sent = r.request().postDataJSON();
      return r.fulfill({
        status: 201,
        json: {
          id: 'i-1', email: 'a@example.org', role: 'observer', team: teams[0], created_at: '2026-11-10T12:00:00Z',
          expires_at: '2026-11-17T12:00:00Z', is_reset: false, link: 'http://ostraka.test/invite/abc',
        },
      });
    }
    return r.fulfill({ json: [] });
  });

  await page.goto('/people');
  await expect(page.locator('#invite-role option')).toHaveText(['Responder', 'Observer']);
  await expect(page.locator('#invite-team')).toHaveCount(0);
  await page.locator('#invite-email').fill('a@example.org');
  await page.locator('#invite-role').selectOption('observer');
  await page.getByRole('button', { name: 'Create invitation' }).click();

  await expect(page.getByLabel('Invitation link')).toHaveText('http://ostraka.test/invite/abc');
  expect(sent).toEqual({ email: 'a@example.org', role: 'observer', team_id: 't-soc' });
});

test('responders see the directory without invite or admin controls', async ({ page }) => {
  await signedInAs(page, me());
  await page.goto('/people');
  await expect(page.getByRole('link', { name: 'Directory' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Invite someone' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Deactivate' })).toHaveCount(0);
});

test('the raw-IOC switch appears for observers only, and starts off', async ({ page }) => {
  await signedInAs(page, me({ role: 'observer', team: { id: 't-l', name: 'Leadership', description: '' } }));
  await page.goto('/profile?tab=appearance');
  await expect(page.locator('#show-raw-iocs')).not.toBeChecked();
});

test('a new API token comes with the command that connects an MCP client', async ({ page }) => {
  await signedInAs(page, me());
  const made = { id: 't-1', name: 'laptop', scope: 'mcp', prefix: 'ost_abc123', token: 'ost_abc123secret' };
  await page.route('**/api/tokens', (r) =>
    r.request().method() === 'POST' ? r.fulfill({ status: 201, json: made }) : r.fulfill({ json: [] }),
  );
  await page.goto('/profile?tab=tokens');
  await page.locator('#token-name').fill('laptop');
  await page.getByRole('button', { name: 'Create token' }).click();
  const origin = new URL(page.url()).origin;
  await expect(page.getByLabel('MCP command')).toHaveText(
    `claude mcp add --transport http ostraka ${origin}/mcp --header "Authorization: Bearer ost_abc123secret"`,
  );
});
