import { expect, test } from '@playwright/test';
import { dana, me, observable, sam, stubApi, stubLive } from './fixtures';

const since = {
  first_visit: false,
  last_viewed_at: '2026-11-10T18:00:00Z',
  from_position: 4,
  through_position: 7,
  total: 2,
  changes: [
    { entity_type: 'finding', count: 1 },
    { entity_type: 'task', count: 1 },
  ],
  people: ['Dana Lead'],
  events: [
    { position: 6, at: '2026-11-10T21:00:00Z', actor: dana, action: 'finding_added', entity_type: 'finding', entity_id: 'f-1', summary: 'Dana Lead added FND-1' },
    { position: 7, at: '2026-11-10T22:00:00Z', actor: dana, action: 'task_claimed', entity_type: 'task', entity_id: 'k-1', summary: 'Dana Lead claimed TSK-1' },
  ],
};

test('what changed since you left is shown once, and your place moves on', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/since') return { json: since };
    if (method === 'POST' && path === '/api/incidents/i-1/seen') return { status: 204 };
  });
  await page.goto('/incidents/OST-2026-001');

  const banner = page.getByRole('region', { name: 'Since you left' });
  await expect(banner).toContainText('1 finding, 1 task by Dana Lead');
  await banner.getByRole('button', { name: 'Show all 2' }).click();
  await expect(page.getByRole('list', { name: 'Changes since you left' })).toContainText('Dana Lead claimed TSK-1');
  await expect
    .poll(() => api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/seen')?.body)
    .toEqual({ position: 7 });
  await banner.getByRole('button', { name: 'Dismiss' }).click();
  await expect(banner).toBeHidden();
});

test('the handover is drafted by Ostraka, edited, and posted with the stretch of log it covers', async ({ page }) => {
  await stubLive(page);
  const posted: unknown[] = [];
  const draft = {
    from_position: 3,
    through_position: 9,
    since: '2026-11-10T09:00:00Z',
    sections: [{ title: 'Open tasks (1)', lines: ['TSK-1 Pull the mailbox log: SOC queue, unclaimed'] }],
    text: 'Handover: OST-2026-001\n\nOpen tasks (1)\n- TSK-1 Pull the mailbox log: SOC queue, unclaimed',
  };
  const api = await stubApi(page, me(), (method, path, body) => {
    if (path === '/api/incidents/i-1/handover/draft') return { json: draft };
    if (path === '/api/incidents/i-1/handover' && method === 'GET') return { json: posted };
    if (path === '/api/incidents/i-1/handover' && method === 'POST') {
      const note = { id: 'h-1', ref: 'HND-1', author: sam, from_position: 3, through_position: 9, body: (body as { body: string }).body, edited: true, created_at: '2026-11-11T18:00:00Z' };
      posted.push(note);
      return { status: 201, json: note };
    }
  });
  await page.goto('/incidents/OST-2026-001?s=handover');
  await expect(page.getByText('No handovers posted yet.')).toBeVisible();

  await page.getByRole('button', { name: 'Write the handover' }).click();
  const text = page.getByRole('textbox', { name: 'Handover note' });
  await expect(text).toHaveValue(draft.text);
  await text.fill(`${draft.text}\n\nRing the vendor at 07:00.`);
  await page.getByRole('button', { name: 'Post the handover' }).click();

  const sent = api.requests.find((r) => r.method === 'POST' && r.path === '/api/incidents/i-1/handover')!.body;
  expect(sent).toEqual({ body: `${draft.text}\n\nRing the vendor at 07:00.`, from_position: 3, through_position: 9 });
  const list = page.getByRole('list', { name: 'Handovers' });
  await expect(list).toContainText('HND-1');
  await expect(list).toContainText('Ring the vendor at 07:00.');
  await expect(list).toContainText('edited from the draft');
});

test('a record shows who else has it open, and an indicator worked elsewhere is flagged', async ({ page }) => {
  const sent: { type: string; focus?: string; editing?: boolean }[] = [];
  await page.routeWebSocket('**/ws**', (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      sent.push(message);
      if (message.type === 'join')
        ws.send(
          JSON.stringify({
            type: 'presence',
            incident: message.incident,
            people: [{ user_id: dana.id, name: dana.name, focus: 'observable:o-1', editing: true }],
          }),
        );
    });
  });
  await stubApi(page, me(), (_method, path) => {
    if (path === '/api/incidents/i-1/observables/working')
      return {
        json: [
          {
            item_id: 'o-1',
            visible: [{ incident_id: 'i-2', ref: 'OST-2026-004', title: 'Beaconing', people: ['Dee Dfir'], editing: false, last_at: '2026-11-11T17:40:00Z' }],
            hidden: 1,
            ask: ['DFIR'],
          },
        ],
      };
  });
  await page.goto('/incidents/OST-2026-001?s=observables');

  const row = page.locator(`[id="${observable.ref}"]`);
  await expect(row.getByRole('status', { name: 'Also on this record' })).toContainText('Dana Lead editing');
  const warning = row.getByRole('note', { name: 'Being worked elsewhere' });
  await expect(warning).toContainText('Dee Dfir working this in #OST-2026-004');
  await expect(warning).toContainText('1 incident you can’t open; ask DFIR');

  // Recording a sighting is editing the observable: the focus goes out by id, never its value
  await row.getByRole('button', { name: 'Record a sighting' }).click();
  await expect.poll(() => sent.some((m) => m.type === 'presence' && m.focus === 'observable:o-1' && m.editing)).toBe(true);
  expect(sent.some((m) => JSON.stringify(m).includes('evil.example'))).toBe(false);
});
