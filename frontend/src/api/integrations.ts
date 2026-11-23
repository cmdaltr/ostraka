// The suite links: the Rowanholt and Shikari servers, Rowanholt on an incident, and detection gaps

import { api } from './client';
import type { Severity, Tlp } from '../brand';

export type Product = 'rowanholt' | 'shikari';

interface Person {
  id: string;
  name: string;
}

export interface Integration {
  product: Product;
  url: string;
  has_token: boolean;
  has_webhook_secret: boolean;
  detection_team_id: string | null;
  enabled: boolean;
  checked_at: string | null;
  check_ok: boolean | null;
  check_message: string;
  updated_by: Person | null;
  updated_at: string;
  version: number;
  webhook_secret: string | null; // Shikari: only when it's made or replaced
}

export interface Integrations {
  rowanholt: Integration | null;
  shikari: Integration | null;
  shikari_webhook_url: string;
  rowanholt_webhook_url?: string;
  automation: boolean;
}

export interface ConnectionTest {
  ok: boolean;
  message: string;
  steps: { name: string; ok: boolean; detail: string }[];
}

export interface RemoteJob {
  id: string;
  status: string;
  created_at: string | null;
  url: string;
}

export interface RowanholtLink {
  id: string;
  job_id: string;
  case_number: string;
  status: string;
  progress: number | null;
  jobs: RemoteJob[];
  checked_at: string | null;
  url: string;
  linked_by: Person | null;
  linked_at: string;
}

export interface Citation {
  id: string;
  finding_id: string;
  finding_ref: string;
  job_id: string;
  case_number: string;
  host: string;
  artefact: string;
  row_ref: string;
  row_hash: string;
  cite: string;
  excerpt: Record<string, string>;
  remote_finding_id: string | null;
  remote_status: string;
  verification_id: string | null;
  url: string;
  cited_by: Person | null;
  cited_at: string;
}

export interface RowanholtView {
  configured: boolean;
  reachable: boolean | null;
  message: string;
  links: RowanholtLink[];
  citations: Citation[];
}

export interface RemoteFinding {
  id: string;
  job_id: string;
  host: string;
  artefact: string;
  row_ref: string;
  row_hash: string;
  title: string;
  technique: string;
  status: string;
  intact: boolean | null;
  excerpt: string;
  imported_as: string | null;
  cite: string;
}

export interface CaseTables {
  job_id: string;
  hosts: { host: string; files: string[] }[];
}

export interface Rows {
  columns: string[];
  rows: { ref: string; hash: string; cells: Record<string, string> }[];
  total: number;
  exact: boolean;
}

export interface Gap {
  id: string;
  technique: string;
  technique_name: string;
  attack_url: string;
  status: 'open' | 'closed';
  finding_id: string | null;
  finding_ref: string | null;
  task: { id: string; ref: string; title: string; status: string } | null;
  opened_by: Person | null;
  opened_at: string;
  checked_at: string | null;
  check_error: string;
  closed_at: string | null;
  closed_by: string;
  incident: { id: string; ref: string; title: string; severity: Severity; tlp: Tlp } | null;
}

export interface Gaps {
  configured: boolean;
  automation: boolean;
  coverage_url: string | null;
  message: string;
  gaps: Gap[];
}

export const names: Record<Product, string> = { rowanholt: 'Rowanholt', shikari: 'Shikari' };

export const getIntegrations = () => api<Integrations>('/api/integrations');

// A Rowanholt job's or case's status, in words
export function statusWords(status: string, progress: number | null = null): string {
  if (!status) return 'Not seen yet';
  const words = status.replace(/_/g, ' ');
  const sentence = words[0].toUpperCase() + words.slice(1);
  return status === 'running' && progress != null ? `${sentence}, ${progress}%` : sentence;
}

// What a gap is waiting for, in a few words
export function gapState(gap: Gap): string {
  if (gap.status === 'closed') return 'Detected in Shikari';
  if (gap.check_error) return 'Not checked: Shikari didn’t answer';
  if (!gap.task) return 'No task yet';
  if (gap.task.status === 'done') return 'Task done, Shikari has no detection yet';
  return 'Waiting for a detection';
}

// Pairing: a one-use link made in Rowanholt or Shikari (docs/suite/INTEGRATION.md)
export interface Paired {
  integration: Integration;
  test: ConnectionTest;
  events: string[];
}

export const pairWith = (link: string) => api<Paired>('/api/integrations/pair', { method: 'POST', json: { link } });

export const pairingLink = /^https?:\/\/[^#\s]+#pair_[A-Za-z0-9_-]{20,100}$/;

export type RunKind = 'group' | 'cve' | 'techniques';

export interface RunCount {
  file: string;
  technique: string;
  platform: string;
  query_index: number;
  hits: number | null;
  error: string;
  skipped: boolean;
}

export interface ShikariRun {
  id: string;
  run_id: string;
  kind: RunKind;
  target: string;
  execute: boolean;
  window_days: number;
  status: string;
  error: string;
  detections: { file: string; name: string; techniques: string[]; platforms: string[]; queries: { platform: string; query: string }[] }[];
  counts: RunCount[];
  totals: { queries?: number; run?: number; hits?: number; errors?: number; skipped?: number };
  started_by: Person | null;
  started_at: string;
  checked_at: string | null;
  link: string | null;
}

// What a run's totals say, in a line
export function runSummary(run: ShikariRun): string {
  const t = run.totals;
  if (run.status === 'failed') return run.error ? `Failed: ${run.error}` : 'Failed';
  if (run.status === 'executed') {
    const parts = [`${t.run ?? 0} of ${t.queries ?? 0} queries run`, `${t.hits ?? 0} results`];
    if (t.errors) parts.push(`${t.errors} failed`);
    if (t.skipped) parts.push(`${t.skipped} had no SIEM connection`);
    return parts.join(' · ');
  }
  const found = run.detections.length;
  return found ? `${run.status}: ${found} detection${found === 1 ? '' : 's'}` : run.status;
}
