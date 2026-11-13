import { expect, test } from '@playwright/test';
import { me, stubApi, stubLive } from './fixtures';

const legal = {
  licence: 'Elastic License\n\n## Acceptance\n\nBy using the software, you agree to all of the terms and conditions below.',
  terms_version: '1.1',
  terms: '# Ostraka Commercial Licence\n\n**Version 1.1**, 11 November 2026',
};

test('Help → Licence opens without signing in and shows both documents', async ({ page }) => {
  let refreshed = false;
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/refresh') refreshed = true;
    if (path === '/api/legal') return route.fulfill({ json: legal });
    return route.fulfill({ status: 401, json: { detail: 'Sign in' } });
  });
  await page.goto('/help/licence');
  await expect(page.getByText('By downloading, installing or using Ostraka, you agree')).toBeVisible();
  await expect(page.getByLabel('Elastic License 2.0', { exact: true })).toContainText('By using the software');
  await page.getByRole('button', { name: 'Commercial licence, version 1.1' }).click();
  await expect(page.getByLabel('Commercial licence, version 1.1')).toContainText('Ostraka Commercial Licence');
  await expect(page).toHaveURL(/show=terms/);
  await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible();
  expect(refreshed).toBe(false); // a public page doesn't try to restore a session
});

test('signed in, Help is in the navigation and leads back', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (_method, path) => (path === '/api/legal' ? { json: legal } : undefined));
  await page.goto('/');
  await page.getByRole('link', { name: 'Help' }).click();
  await expect(page.getByLabel('Elastic License 2.0', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Back to Ostraka' }).click();
  await expect(page.getByRole('link', { name: 'Incidents' })).toBeVisible();
});

test('the sign-in page links to the licence', async ({ page }) => {
  await page.route('**/api/**', (route) => route.fulfill({ status: 401, json: { detail: 'Sign in' } }));
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Licence' })).toHaveAttribute('href', '/help/licence');
});

test('the sign-in page and every signed-in page carry the trademark notice', async ({ page }) => {
  const notice = 'Ostraka™ and its logo are trademarks of cmdaltr.';
  await page.route('**/api/**', (route) => route.fulfill({ status: 401, json: { detail: 'Sign in' } }));
  await page.goto('/');
  await expect(page.getByText(notice)).toBeVisible();
  await page.unrouteAll();
  await stubLive(page);
  await stubApi(page, me());
  await page.goto('/');
  await expect(page.getByRole('contentinfo')).toContainText(notice);
});
