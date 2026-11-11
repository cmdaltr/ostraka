// Shared stubs for the e2e specs: one handler answers the whole API, and the live socket is faked

import type { Page, Route, WebSocketRoute } from '@playwright/test';

export const teams = [
  { id: 't-soc', name: 'SOC', description: '' },
  { id: 't-dfir', name: 'DFIR', description: '' },
];
export const sam = { id: 'u-1', name: 'Sam Analyst' };
export const dana = { id: '0b5e2c1a-7d3f-4e8a-9c21-5f6d7e8a9b02', name: 'Dana Lead' };

export function me(overrides: Record<string, unknown> = {}) {
  return {
    ...sam,
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

export function incident(overrides: Record<string, unknown> = {}) {
  return {
    id: 'i-1',
    kind: 'incident',
    ref: 'OST-2026-001',
    title: 'Phishing leads to a foothold on FIN-WS-07',
    severity: 'high',
    tlp: 'amber',
    phase: 'scope',
    status: 'open',
    commander: sam,
    teams: [teams[0]],
    declared_at: '2026-11-10T09:00:00Z',
    updated_at: '2026-11-10T10:00:00Z',
    tags: ['phishing'],
    summary: 'A user opened an attachment.',
    aware_at: '2026-11-10T08:40:00Z',
    closed_at: null,
    members: [{ user: sam, role: 'commander' }],
    version: 3,
    can_edit: true,
    can_manage: true,
    sensitive: false,
    can_export: true,
    brand_id: null,
    counts: { observables: 1, assets: 0, findings: 1, comments: 0 },
    ...overrides,
  };
}

export function licence(overrides: Record<string, unknown> = {}) {
  return {
    edition: 'free',
    state: 'free',
    organisation: '',
    seats: 4,
    features: [],
    places_held: 2,
    not_before: null,
    expires: null,
    grace_ends: null,
    custom_branding: false,
    brand_library: false,
    terms_version: '1.0',
    accepted: null,
    ...overrides,
  };
}

export const services = licence({
  edition: 'services',
  state: 'active',
  organisation: 'Northwind Ltd',
  seats: 25,
  features: ['brand-library', 'managed-service', 'report-branding'],
  places_held: 9,
  expires: '2027-11-30',
  grace_ends: '2027-12-30',
  custom_branding: true,
  brand_library: true,
});

export function brand(overrides: Record<string, unknown> = {}) {
  return {
    id: 'b-1',
    name: 'Northwind',
    primary: '#1a3d8f',
    secondary: '',
    prepared_for: '',
    is_default: true,
    logos: [],
    palette: { ink: '#1b2230', clay: '#1a3d8f', muted: '#56606f', quiet: '#b9c0cc', limestone: '#e9ecf2', band: '#f3f5f8', paper: '#f9fafc' },
    notes: [],
    updated_at: '2026-11-11T09:00:00Z',
    version: 1,
    ...overrides,
  };
}

export const observable = {
  id: 'o-1',
  ref: 'OBS-7',
  kind: 'domain',
  value: 'evil.example',
  masked: false,
  verdict: 'malicious',
  note: '',
  tags: [],
  sightings: [],
  added_at: '2026-11-10T09:10:00Z',
  version: 1,
  elsewhere: { visible: [], hidden: 1, ask: ['DFIR (lead: Dana Lead)'] },
};

export function finding(overrides: Record<string, unknown> = {}) {
  return {
    id: 'f-1',
    ref: 'FND-1',
    kind: 'evidence',
    title: 'PowerShell download cradle on FIN-WS-07',
    statement: 'Based on the process tree, we determine a download cradle ran.',
    status: 'lead',
    occurred_at: '2026-11-10T08:31:00Z',
    asset_id: null,
    techniques: ['T1059.001'],
    tags: [],
    observable_ids: [],
    source_reliability: null,
    information_credibility: null,
    handling_code: null,
    handling_conditions: '',
    created_by: sam,
    created_at: '2026-11-10T09:20:00Z',
    decided_by: null,
    decided_at: null,
    decision_note: '',
    verifications: [],
    version: 1,
    ...overrides,
  };
}

export const noLinks = {
  parent: null,
  children: [],
  related: [],
  duplicate_of: null,
  duplicates: [],
  promoted_from: null,
  promoted_to: [],
  hidden: 0,
};

export interface Api {
  requests: { method: string; path: string; body: unknown }[];
  handle: (method: string, path: string, body: unknown) => { status?: number; json?: unknown } | undefined;
}

// One stub for the whole API: per-test answers first, then the defaults below
export async function stubApi(page: Page, who = me(), handle: Api['handle'] = () => undefined): Promise<Api> {
  const api: Api = { requests: [], handle };
  const defaults: Record<string, unknown> = {
    'GET /api/users/me': who,
    'GET /api/teams': teams,
    'GET /api/users': [{ ...me(), ...sam }, { ...me(), ...dana, team: teams[1], role: 'team_lead' }],
    'GET /api/incidents': [incident()],
    'GET /api/incidents/by-ref/OST-2026-001': incident(),
    'GET /api/incidents/i-1': incident(),
    'GET /api/incidents/i-1/observables': [observable],
    'GET /api/incidents/i-1/assets': [],
    'GET /api/incidents/i-1/asset-links': [],
    'GET /api/incidents/i-1/findings': [finding()],
    'GET /api/incidents/i-1/comments': [],
    'GET /api/incidents/i-1/timeline': [],
    'GET /api/incidents/i-1/activity': [],
    'GET /api/incidents/i-1/tasks': [],
    'GET /api/incidents/i-1/playbooks': [],
    'GET /api/incidents/i-1/links': noLinks,
    'GET /api/playbooks': [],
    'GET /api/tasks/mine': [],
    'GET /api/clocks/mine': [],
    'GET /api/incidents/i-1/clocks': [],
    'GET /api/notifications': { items: [], unread: 0 },
    'GET /api/licence': licence(),
    'GET /api/brands': [],
    'POST /api/live/ticket': { ticket: 'tk' },
  };
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    // An upload (a logo) isn't JSON; keep its size so a test can see it was sent
    let body: unknown = null;
    try {
      body = request.postData() ? request.postDataJSON() : null;
    } catch {
      body = { bytes: request.postDataBuffer()?.length ?? 0 };
    }
    api.requests.push({ method: request.method(), path: url.pathname, body });
    if (url.pathname === '/api/auth/refresh') return route.fulfill({ json: { access_token: 'a', expires_in: 900 } });
    const answer = api.handle(request.method(), url.pathname, body);
    if (answer) return route.fulfill({ status: answer.status ?? 200, json: answer.json ?? {} });
    const known = defaults[`${request.method()} ${url.pathname}`];
    if (known !== undefined) return route.fulfill({ json: known });
    return route.fulfill({ status: 404, json: { detail: `No stub for ${request.method()} ${url.pathname}` } });
  });
  return api;
}

// The live socket, answered in the page: a join gets a presence list back
export async function stubLive(page: Page): Promise<() => WebSocketRoute> {
  let socket: WebSocketRoute | undefined;
  await page.routeWebSocket('**/ws**', (ws) => {
    socket = ws;
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === 'join')
        ws.send(
          JSON.stringify({
            type: 'presence',
            incident: message.incident,
            people: [
              { user_id: sam.id, name: sam.name, focus: '', editing: false },
              { user_id: dana.id, name: dana.name, focus: 'findings', editing: true },
            ],
          }),
        );
    });
  });
  return () => socket!;
}

export const count = (api: Api, method: string, path: string) =>
  api.requests.filter((r) => r.method === method && r.path === path).length;

