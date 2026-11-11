import { expect, test } from '@playwright/test';
import { me, stubApi, stubLive } from './fixtures';

const patched = (api: Awaited<ReturnType<typeof stubApi>>) =>
  api.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/users/me').map((r) => r.body);

test('settings has become the profile, and the profile saves what it is given', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) =>
    method === 'PATCH' && path === '/api/users/me' ? { json: me(body as Record<string, unknown>) } : undefined,
  );
  await page.goto('/settings');
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole('link', { name: 'Profile' })).toHaveAttribute('aria-current', 'page');
  await page.locator('#profile-title').fill('Senior analyst');
  await page.locator('#profile-phone').fill('+44 7700 900123');
  await page.locator('#profile-timezone').selectOption('Europe/Paris');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
  expect(patched(api)[0]).toEqual({
    name: 'Sam Analyst',
    job_title: 'Senior analyst',
    phone: '+44 7700 900123',
    timezone: 'Europe/Paris',
  });

  await page.getByRole('radio', { name: 'Busy' }).click();
  await expect(page.getByRole('radio', { name: 'Busy' })).toBeChecked();
  await expect.poll(() => patched(api)[1]).toEqual({ availability: 'busy' });
});

test('the security tab lists signed-in browsers and signs one out', async ({ page }) => {
  await stubLive(page);
  const browsers = [
    { family: 'f-1', device: 'Firefox on macOS', ip: '100.64.0.2', signed_in_at: '2026-11-10T09:00:00Z', last_active_at: '2026-11-10T11:00:00Z', expires_at: '2026-11-24T09:00:00Z', current: true },
    { family: 'f-2', device: 'Safari on iPhone', ip: '100.64.0.7', signed_in_at: '2026-11-09T20:00:00Z', last_active_at: '2026-11-09T21:00:00Z', expires_at: '2026-11-23T20:00:00Z', current: false },
  ];
  const log = [
    { id: 'e-1', at: '2026-11-10T09:00:00Z', action: 'login', ip: '100.64.0.2', by: null, detail: { new_device: true } },
    { id: 'e-2', at: '2026-11-09T12:00:00Z', action: 'account_changed', ip: '100.64.0.9', by: 'Ada', detail: {} },
  ];
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/users/me/sessions') return { json: browsers };
    if (method === 'GET' && path === '/api/users/me/security-log') return { json: log };
    if (method === 'DELETE' && path === '/api/users/me/sessions/f-2') return { status: 204 };
    return undefined;
  });
  await page.goto('/profile?tab=security');
  await expect(page.getByRole('row', { name: /Firefox on macOS This browser/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign out Firefox on macOS' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Sign out Safari on iPhone' }).click();
  await expect.poll(() => api.requests.some((r) => r.method === 'DELETE' && r.path === '/api/users/me/sessions/f-2')).toBe(true);

  const securityLog = page.getByRole('table', { name: 'Security log' });
  await expect(securityLog).toContainText('Signed in from a new browser');
  await expect(securityLog.getByRole('row', { name: /Account changed Ada/ })).toBeVisible();
});

test('a new authenticator shows its QR code and is confirmed with its own code', async ({ page }) => {
  await stubLive(page);
  const enrol = { otpauth_uri: 'otpauth://totp/x', qr_svg: 'data:image/svg+xml;base64,PHN2Zy8+', secret: 'JBSWY3DPEHPK3PXP' };
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path.startsWith('/api/users/me/')) return { json: [] };
    if (method === 'POST' && path === '/api/users/me/authenticator') return { json: enrol };
    if (method === 'POST' && path === '/api/users/me/authenticator/confirm') return { status: 204 };
    return undefined;
  });
  await page.goto('/profile?tab=security');
  await page.locator('#mfa-password').fill('correct horse battery');
  await page.locator('#mfa-code').fill('123456');
  await page.getByRole('button', { name: 'Change authenticator' }).click();
  await expect(page.getByAltText('QR code for your new authenticator')).toBeVisible();
  await page.locator('#new-totp').fill('654321');
  await page.getByRole('button', { name: 'Use this authenticator' }).click();
  await expect(page.getByText('Your new authenticator is in use')).toBeVisible();
  expect(api.requests.find((r) => r.path === '/api/users/me/authenticator/confirm')!.body).toEqual({ code: '654321' });
});

test('notification kinds can be kept to the bell, and quiet hours set', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path, body) =>
    method === 'PATCH' && path === '/api/users/me' ? { json: me(body as Record<string, unknown>) } : undefined,
  );
  await page.goto('/profile?tab=notifications');
  await page.locator('#send-mention').click();
  await expect(page.locator('#send-mention')).not.toBeChecked();
  expect(patched(api)[0]).toEqual({ muted_kinds: ['mention'] });

  await page.locator('#quiet-from').fill('22:00');
  await page.locator('#quiet-until').fill('07:00');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => patched(api)[1]).toEqual({ quiet_from: '22:00', quiet_until: '07:00' });
  await expect(page.getByText('critical incident still comes through')).toBeVisible();
});

test('the directory shows job titles, availability and out-of-hours phones', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) =>
    method === 'GET' && path === '/api/users'
      ? { json: [me({ id: 'u-2', name: 'Dee', job_title: 'Forensic analyst', availability: 'away', phone: '+44 7700 900456' })] }
      : method === 'GET' && path === '/api/invitations'
        ? { json: [] }
        : undefined,
  );
  await page.goto('/people');
  const row = page.getByRole('row', { name: /Dee/ });
  await expect(row).toContainText('Forensic analyst');
  await expect(row).toContainText('Away');
  await expect(row.getByRole('link', { name: 'Out-of-hours phone for Dee' })).toHaveAttribute('href', 'tel:+447700900456');
});
