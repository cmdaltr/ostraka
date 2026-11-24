import { expect, test } from '@playwright/test';
import { incident, sam, stubApi, stubLive } from './fixtures';

const worded = [
  'OST-26-001 Folded Lantern is an open high-severity incident about forwarded finance mail.',
  'The attacker created an inbox rule, confirmed as FND-3.',
  'A beacon to [ip address] is still a lead, FND-4.',
  'No judgement has been made yet.',
];
const own = [
  'OST-26-001 Folded Lantern is a high-severity incident at TLP:AMBER, “Phish”, declared on 3 November 2026.',
  '1 finding has been confirmed: FND-3 (Inbox rule created by the attacker).',
  '1 lead remains to be confirmed or refuted.',
  'No assessment has been made yet, so this carries no judgement of what it means.',
];
const draftOf = (sentences: string[], extra: Record<string, unknown> = {}) => ({
  sentences,
  text: sentences.join(' '),
  worded: 'ollama',
  set_aside: '',
  can_ask: true,
  why_not: '',
  ...extra,
});

test('a responder generates the executive summary from the record and saves it', async ({ page }) => {
  await stubLive(page);
  let current: Record<string, unknown> = incident();
  const api = await stubApi(page, undefined, (method, path, body) => {
    if (path === '/api/incidents/by-ref/OST-26-001' || path === '/api/incidents/i-1') return { json: current };
    if (method === 'POST' && path === '/api/incidents/i-1/executive-summary/draft') {
      const ai = (body as { ai: boolean }).ai;
      return { json: ai ? draftOf(worded) : draftOf(own, { worded: 'ostraka' }) };
    }
    if (method === 'PUT' && path === '/api/incidents/i-1/executive-summary') {
      const sent = body as { text: string; worded: string };
      current = {
        ...incident(),
        version: 2,
        executive: { text: sent.text, by: sam, at: '2026-11-24T10:00:00Z', worded: sent.worded },
      };
      return { json: current };
    }
  });
  await page.goto('/incidents/OST-26-001');
  const panel = page.getByRole('region', { name: 'Executive summary' });
  await expect(panel).toContainText('None yet.');
  await panel.getByRole('button', { name: 'Generate' }).click();

  const box = panel.getByRole('textbox');
  await expect(box).toHaveValue(worded.join(' '));
  await expect(panel).toContainText('worded by Ollama, checked by Ostraka');
  // Ostraka's own draft, without the model
  await panel.getByRole('button', { name: 'Use Ostraka’s own' }).click();
  await expect(box).toHaveValue(own.join(' '));
  await expect(panel).toContainText('Four sentences from the record, worded by Ostraka');

  // an edit makes it a person's wording
  await box.fill(`${own.join(' ')} Reviewed.`);
  await expect(panel).toContainText('worded by hand');
  await panel.getByRole('button', { name: 'Save' }).click();
  await expect(panel).toContainText('Reviewed.');
  await expect(panel).toContainText('Saved by Sam Analyst');
  const put = api.requests.find((r) => r.method === 'PUT');
  expect(put?.body).toEqual({ version: incident().version, text: `${own.join(' ')} Reviewed.`, worded: 'person' });
});

test('a model’s wording set aside says why, and an observer only reads it', async ({ page }) => {
  await stubLive(page);
  let current: Record<string, unknown> = incident();
  await stubApi(page, undefined, (method, path) => {
    if (path === '/api/incidents/by-ref/OST-26-001' || path === '/api/incidents/i-1') return { json: current };
    if (method === 'POST' && path === '/api/incidents/i-1/executive-summary/draft')
      return { json: draftOf(own, { worded: 'ostraka', set_aside: 'It calls FND-4 confirmed, but it is still a lead.' }) };
  });
  await page.goto('/incidents/OST-26-001');
  const panel = page.getByRole('region', { name: 'Executive summary' });
  await panel.getByRole('button', { name: 'Generate' }).click();
  await expect(panel).toContainText('The model’s wording was set aside: It calls FND-4 confirmed');
  await expect(panel.getByRole('textbox')).toHaveValue(own.join(' '));

  current = {
    ...incident({ can_edit: false }),
    executive: { text: own.join(' '), by: sam, at: '2026-11-24T10:00:00Z', worded: 'ostraka' },
  };
  await page.reload();
  await expect(panel).toContainText('1 finding has been confirmed');
  await expect(panel).toContainText('worded by Ostraka');
  await expect(panel.getByRole('button')).toHaveCount(0);
});
