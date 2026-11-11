import { expect, test } from '@playwright/test';
import { incident, me, stubApi, stubLive, teams } from './fixtures';

const admin = me({ role: 'admin', team: null });
const JOB = '8a1f0c2e-5b7d-4c3a-9e21-0f6d2b4a7c11';

function server(product: 'rowanholt' | 'shikari', overrides: Record<string, unknown> = {}) {
  return {
    product,
    url: `https://${product}.tail180683.ts.net`,
    has_token: true,
    has_webhook_secret: product === 'shikari',
    detection_team_id: null,
    enabled: true,
    checked_at: null,
    check_ok: null,
    check_message: '',
    updated_by: null,
    updated_at: '2026-11-11T10:00:00Z',
    version: 1,
    webhook_secret: null,
    ...overrides,
  };
}

function gap(overrides: Record<string, unknown> = {}) {
  return {
    id: 'g-1',
    technique: 'T1003.001',
    technique_name: 'LSASS Memory',
    attack_url: 'https://attack.mitre.org/techniques/T1003/001/',
    status: 'open',
    finding_id: 'f-1',
    finding_ref: 'FND-1',
    task: { id: 't-9', ref: 'TSK-9', title: 'Detection for T1003.001 (LSASS Memory)', status: 'open' },
    opened_by: null,
    opened_at: '2026-11-11T10:00:00Z',
    checked_at: '2026-11-11T10:00:00Z',
    check_error: '',
    closed_at: null,
    closed_by: '',
    incident: null,
    ...overrides,
  };
}

test('an admin connects Shikari, copies the webhook secret once and tests the connection', async ({ page }) => {
  await stubLive(page);
  let saved: Record<string, unknown> | null = null;
  const api = await stubApi(page, admin, (method, path) => {
    if (method === 'GET' && path === '/api/integrations')
      return {
        json: {
          rowanholt: null,
          shikari: saved,
          shikari_webhook_url: 'https://ostraka.tail180683.ts.net/api/integrations/shikari/webhook',
          automation: false,
        },
      };
    if (method === 'PUT' && path === '/api/integrations/shikari') {
      saved = server('shikari', { detection_team_id: 't-dfir' });
      return { json: { ...saved, webhook_secret: 'whsec-once' } };
    }
    if (method === 'POST' && path === '/api/integrations/shikari/test')
      return {
        json: {
          ok: true,
          message: 'Connected.',
          steps: [
            { name: 'Shikari answers', ok: true, detail: '' },
            { name: 'The token reads coverage', ok: true, detail: 'Coverage for T1059 read.' },
          ],
        },
      };
    return undefined;
  });
  await page.goto('/installation?tab=integrations');
  const panel = page.getByRole('region', { name: 'Shikari' });
  await panel.getByLabel('Address').fill('https://shikari.tail180683.ts.net');
  await panel.getByLabel('API token').fill('shik_abc');
  await panel.getByLabel('Detection team').selectOption({ label: teams[1].name });
  await panel.getByRole('button', { name: 'Connect Shikari' }).click();

  await expect(panel.getByText('whsec-once')).toBeVisible();
  await expect(panel.getByText('part of the Pro and Services editions')).toBeVisible();
  expect(api.requests.find((r) => r.method === 'PUT')!.body).toEqual({
    url: 'https://shikari.tail180683.ts.net',
    token: 'shik_abc',
    detection_team_id: 't-dfir',
    enabled: true,
  });

  await panel.getByRole('button', { name: 'Test the connection' }).click();
  await expect(panel.getByRole('list', { name: 'Shikari connection test' })).toContainText('The token reads coverage');
});

test('an incident shows its Rowanholt case status, and says when Rowanholt is down', async ({ page }) => {
  await stubLive(page);
  let down = false;
  let linked = false;
  const link = {
    id: 'l-1',
    job_id: JOB,
    case_number: '2026-117',
    status: 'running',
    progress: 40,
    jobs: [],
    checked_at: '2026-11-11T10:00:00Z',
    url: `https://rowanholt.tail180683.ts.net/jobs/${JOB}/case`,
    linked_by: null,
    linked_at: '2026-11-11T10:00:00Z',
  };
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/rowanholt')
      return {
        json: {
          configured: true,
          reachable: !down,
          message: down ? 'Rowanholt can’t be reached at https://rowanholt.tail180683.ts.net. Showing the status as last seen.' : '',
          links: linked ? [link] : [],
          citations: [],
        },
      };
    if (method === 'POST' && path === '/api/incidents/i-1/rowanholt/links') {
      linked = true;
      return { status: 201, json: { configured: true, reachable: true, message: '', links: [link], citations: [] } };
    }
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=rowanholt');
  await expect(page.getByText('Not linked to Rowanholt yet.')).toBeVisible();
  await page.getByLabel('Rowanholt job id or case number').fill(JOB);
  await page.getByRole('button', { name: 'Link' }).click();
  await expect(page.getByRole('link', { name: 'Job 8a1f0c2e · case 2026-117' })).toHaveAttribute(
    'href',
    `https://rowanholt.tail180683.ts.net/jobs/${JOB}/case`,
  );
  await expect(page.getByText('Running, 40%')).toBeVisible();
  expect(api.requests.find((r) => r.path === '/api/incidents/i-1/rowanholt/links')!.body).toEqual({ job_id: JOB });

  down = true;
  await page.reload();
  await expect(page.getByText('Showing the status as last seen.')).toBeVisible();
  await expect(page.getByText('Running, 40%')).toBeVisible();
});

test('detection gaps close only when Shikari says so, and are listed across incidents', async ({ page }) => {
  await stubLive(page);
  let checked = false;
  const api = await stubApi(page, me(), (method, path) => {
    const gaps = checked ? [gap({ status: 'closed', closed_by: 'Checked with Shikari by Sam Analyst', task: { ...gap().task, status: 'done' } })] : [gap()];
    if (method === 'GET' && path === '/api/incidents/i-1/detection-gaps')
      return { json: { configured: true, automation: false, coverage_url: 'https://shikari.tail180683.ts.net/coverage', message: '', gaps } };
    if (method === 'POST' && path === '/api/incidents/i-1/detection-gaps/check') {
      checked = true;
      return {
        json: {
          configured: true,
          automation: false,
          coverage_url: null,
          message: 'Shikari has detections for 1 of them: closed.',
          gaps: [gap({ status: 'closed' })],
        },
      };
    }
    if (method === 'GET' && path === '/api/detection-gaps')
      return { json: [gap({ incident: { id: 'i-1', ref: 'OST-2026-001', title: 'Phishing leads to a foothold', severity: 'high', tlp: 'amber' } })] };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=detection-gaps');
  const section = page.getByRole('region', { name: 'Detection gaps' });
  await expect(section.getByText('Waiting for a detection')).toBeVisible();
  await expect(section.getByRole('link', { name: 'T1003.001' })).toHaveAttribute(
    'href',
    'https://attack.mitre.org/techniques/T1003/001/',
  );
  await section.getByRole('button', { name: 'Ask Shikari again' }).click();
  await expect(section.getByText('Shikari has detections for 1 of them: closed.')).toBeVisible();
  await expect(section.getByText('Detected in Shikari')).toBeVisible();
  expect(api.requests.filter((r) => r.path === '/api/incidents/i-1/detection-gaps/check')).toHaveLength(1);

  await page.getByRole('link', { name: 'Detection gaps' }).click();
  await expect(page.getByRole('heading', { name: 'Detection gaps' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'OST-2026-001' })).toBeVisible();
  await expect(page.getByText('LSASS Memory')).toBeVisible();
});

test('an observer reads the gaps but cannot ask Shikari or give tasks', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me({ role: 'observer' }), (method, path) => {
    if (path === '/api/incidents/i-1' || path === '/api/incidents/by-ref/OST-2026-001')
      return { json: incident({ can_edit: false, can_manage: false }) };
    if (method === 'GET' && path === '/api/incidents/i-1/detection-gaps')
      return { json: { configured: true, automation: true, coverage_url: null, message: '', gaps: [gap({ task: null })] } };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=detection-gaps');
  const section = page.getByRole('region', { name: 'Detection gaps' });
  await expect(section.getByText('No task yet')).toBeVisible();
  await expect(section.getByRole('button')).toHaveCount(0);
});
