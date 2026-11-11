import { expect, test } from '@playwright/test';
import { dana, me, sam, stubApi, stubLive } from './fixtures';

const graph = {
  incident_id: 'i-1',
  ref: 'OST-2026-001',
  tlp: 'amber',
  restricted: false,
  masked: false,
  nodes: [
    { id: 'asset:a-1', type: 'asset', ref: 'AST-1', label: 'FIN-WS-07', entity_id: 'a-1', attrs: { patient_zero: true } },
    { id: 'asset:a-2', type: 'asset', ref: 'AST-2', label: 'FS01', entity_id: 'a-2', attrs: {} },
    { id: 'observable:o-1', type: 'observable', ref: 'OBS-7', label: 'evil.example', entity_id: 'o-1', attrs: { kind: 'domain' } },
    { id: 'finding:f-1', type: 'finding', ref: 'FND-1', label: 'Beacon to evil.example', entity_id: 'f-1', attrs: { status: 'confirmed' } },
    { id: 'evidence:x-1', type: 'evidence', ref: 'EXH-1', label: 'FIN-WS-07 disk image', entity_id: 'x-1', attrs: { holder: 'Sam Analyst' } },
    { id: 'technique:T1071.001', type: 'technique', ref: 'T1071.001', label: 'T1071.001 Web Protocols', entity_id: null, attrs: { name: 'Web Protocols', tactics: ['command-and-control'] } },
  ],
  edges: [
    { id: 'e1', source: 'observable:o-1', target: 'asset:a-1', kind: 'sighted_on', attrs: { sightings: 2 } },
    { id: 'e2', source: 'finding:f-1', target: 'observable:o-1', kind: 'cites_observable', attrs: { status: 'confirmed' } },
    { id: 'e3', source: 'finding:f-1', target: 'technique:T1071.001', kind: 'uses_technique', attrs: { status: 'confirmed' } },
    { id: 'e4', source: 'finding:f-1', target: 'evidence:x-1', kind: 'cites_evidence', attrs: { status: 'confirmed' } },
    { id: 'e5', source: 'asset:a-1', target: 'asset:a-2', kind: 'connection', attrs: { link_kind: 'lateral_movement', technique: 'T1021.002' } },
  ],
  techniques: [
    { technique_id: 'T1071.001', name: 'Web Protocols', tactics: ['command-and-control'], confirmed: 1, leads: 0, refuted: 0, connections: 0, findings: ['FND-1'], first_seen: null },
  ],
  attack_version: '16.1',
};

test('the graph draws the record, filters by kind and clicks through to the object', async ({ page }) => {
  await stubLive(page);
  await stubApi(page, me(), (method, path) => (method === 'GET' && path === '/api/incidents/i-1/graph' ? { json: graph } : undefined));
  await page.goto('/incidents/OST-2026-001?s=graph');

  const canvas = page.getByRole('group', { name: /Graph of OST-2026-001: 6 nodes, 5 links/ });
  await expect(canvas).toBeVisible();
  await expect(canvas.getByRole('button', { name: 'Exhibit EXH-1: FIN-WS-07 disk image' })).toBeVisible();

  await page.getByRole('checkbox', { name: /Techniques/ }).uncheck();
  await expect(page.getByRole('group', { name: /5 nodes, 4 links/ })).toBeVisible();

  await page.getByRole('button', { name: 'Finding FND-1: Beacon to evil.example' }).click();
  const detail = page.getByRole('complementary', { name: 'FND-1 in the graph' });
  await expect(detail).toContainText('FND-1 cites exhibit EXH-1');
  await detail.getByRole('link', { name: 'Open FND-1' }).click();
  await expect(page).toHaveURL(/s=findings#FND-1$/);
});

const offer = {
  position: 2,
  kind: 'offered',
  at: '2026-11-10T11:00:00Z',
  recorded_at: '2026-11-10T11:00:00Z',
  actor: dana,
  from_person: dana,
  to_person: sam,
  purpose: 'Timeline analysis',
  location: 'DFIR lab',
  acknowledgement: '',
  hash_check: null,
  offer_position: null,
  hash: 'b'.repeat(64),
};
const exhibit = {
  id: 'x-1',
  ref: 'EXH-1',
  label: 'FIN-WS-07 disk image (E01)',
  description: '',
  sha256: 'a'.repeat(64),
  sha1: '',
  md5: '',
  masked: false,
  size_bytes: 1024,
  location: 'Safe 2',
  acquired_at: null,
  created_by: dana,
  created_at: '2026-11-10T10:00:00Z',
  holder: dana,
  pending: offer,
  hash_mismatch: false,
  cited_by: [{ id: 'f-1', ref: 'FND-1' }],
  entries: [
    { ...offer, position: 1, kind: 'received', from_person: null, to_person: dana, purpose: 'Recorded as evidence', hash: 'c'.repeat(64) },
    offer,
  ],
  chain: { ok: true, entries: 2, broken_at: null, head: 'b'.repeat(64) },
  can_offer: false,
  can_answer: true,
  can_withdraw: false,
  version: 1,
};

test('the person offered an exhibit accepts it in their own words, with the hash they computed', async ({ page }) => {
  await stubLive(page);
  const api = await stubApi(page, me(), (method, path) => {
    if (method === 'GET' && path === '/api/incidents/i-1/evidence') return { json: [exhibit] };
    if (method === 'POST' && path === '/api/incidents/i-1/evidence/x-1/accept') return { json: exhibit };
    return undefined;
  });
  await page.goto('/incidents/OST-2026-001?s=evidence');
  const card = page.locator('#EXH-1');
  await expect(card).toContainText('Held by Dana Lead');
  await expect(card).toContainText('Chain intact · 2');
  await expect(card).toContainText('Waiting for Sam Analyst to accept');

  await card.locator('#custody-acknowledgement').fill('Seal SB-40117 intact');
  await card.getByLabel('SHA-256 you computed (optional)').fill('a'.repeat(64));
  await card.getByRole('button', { name: 'Accept custody' }).click();
  await expect
    .poll(() => api.requests.find((r) => r.method === 'POST' && r.path.endsWith('/accept'))?.body)
    .toEqual({ acknowledgement: 'Seal SB-40117 intact', sha256: 'a'.repeat(64) });
});
