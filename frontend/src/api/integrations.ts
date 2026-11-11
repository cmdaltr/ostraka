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
