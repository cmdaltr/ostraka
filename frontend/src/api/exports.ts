import { api } from './client';

// What an incident's Export menu offers this person, beyond the reports
export interface Exports {
  downloads: boolean;
  reason: string | null;
  misp: { available: boolean; reason: string | null };
}

export interface Pushed {
  action: 'added' | 'updated';
  event_id: string;
  uuid: string;
  url: string;
}

export interface MispSettings {
  configured: boolean;
  url: string;
  verify_tls: boolean;
  distribution: number;
  has_key: boolean;
  sending: boolean; // false: the edition doesn't include automation
  distributions: Record<string, string>;
  version: number;
}

export interface MispCheck {
  ok: boolean;
  version: string;
  error: string;
}

export const intelFormats = [
  ['stix', 'STIX 2.1 bundle'],
  ['misp', 'MISP event'],
] as const;

export const intelPath = (incidentId: string, format: 'stix' | 'misp' | 'navigator', leads = false) =>
  `/api/incidents/${incidentId}/export/${format}${leads ? '?leads=true' : ''}`;

export const getExports = (incidentId: string) => api<Exports>(`/api/incidents/${incidentId}/exports`);
export const pushToMisp = (incidentId: string) => api<Pushed>(`/api/incidents/${incidentId}/misp`, { method: 'POST' });
export const getMisp = () => api<MispSettings>('/api/misp');

// A pushed event's result as one sentence
export const pushedWords = (p: Pushed) => (p.action === 'added' ? 'Sent to MISP' : 'Updated in MISP') + (p.event_id ? ` (event ${p.event_id}).` : '.');
