// SIEM queries (backend ostraka/siem.py): the connected SIEMs, running a query, and every query run.
// Only the query is kept; a run's rows live in the page that ran it and nowhere else.
import { api } from './client';
import type { PersonRef } from './incidents';

export type SiemKind = 'splunk' | 'elastic' | 'defender' | 'secops';

export interface SiemField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  hint: string;
}

export interface SiemKindInfo {
  kind: SiemKind;
  name: string;
  language: string;
  window: string; // how the search window is applied
  fields: SiemField[];
}

export interface SiemConnection {
  id: string;
  name: string;
  kind: SiemKind;
  language: string;
  enabled: boolean;
  // admins only
  settings: Record<string, string> | null;
  secrets_set: string[] | null;
  verify_tls: boolean | null;
  checked_at: string | null;
  check_ok: boolean | null;
  check_message: string;
  version: number | null;
}

export interface SiemQuery {
  id: string;
  ref: string;
  text: string;
  kind: SiemKind;
  language: string;
  connection_id: string | null;
  connection_name: string;
  incident: { id: string; ref: string; codename: string | null } | null;
  window_start: string | null;
  window_end: string | null;
  outcome: 'ok' | 'failed';
  row_count: number | null;
  truncated: boolean;
  run_by: PersonRef | null;
  run_at: string;
}

export interface SiemRun {
  query: SiemQuery;
  columns: string[];
  rows: Record<string, unknown>[];
  truncated: boolean;
  problem: string;
}

export const getKinds = () => api<SiemKindInfo[]>('/api/siem/kinds');
export const getConnections = () => api<SiemConnection[]>('/api/siem/connections');

export function searchQueries(params: { q?: string; incident_id?: string; kind?: string; mine?: boolean }) {
  const search = new URLSearchParams();
  if (params.q) search.set('q', params.q);
  if (params.incident_id) search.set('incident_id', params.incident_id);
  if (params.kind) search.set('kind', params.kind);
  if (params.mine) search.set('mine', 'true');
  const query = search.toString();
  return api<SiemQuery[]>(`/api/siem/queries${query ? `?${query}` : ''}`);
}

// A cell as text: SIEMs return numbers, lists and nested objects as well as strings
export function cell(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
