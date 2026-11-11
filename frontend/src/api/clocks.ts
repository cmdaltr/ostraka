// Regulator clock shapes (backend/ostraka/clock_schemas.py) and the countdown arithmetic the page
// runs between fetches. The server works out every deadline; the page only counts down to it, with
// the same thresholds as the nudges (backend/ostraka/clocks/regimes.py).

import type { Severity, Tlp } from '../brand';
import type { PersonRef } from './incidents';

export type RegimeKey = 'uk_gdpr' | 'uk_nis' | 'eu_nis2' | 'dora' | 'custom';
export type ClockState = 'waiting' | 'running' | 'near' | 'closing' | 'breached' | 'met' | 'met_late';
export type Unit = 'hours' | 'days' | 'months';
export type Basis = 'aware' | 'classified' | 'previous';

export interface CustomMilestone {
  name: string;
  amount: number;
  unit: Unit;
  basis: Basis;
}

export interface ClockMilestone {
  key: string;
  name: string;
  due_at: string | null;
  starts_at: string | null;
  provisional: boolean;
  counted: string;
  state: ClockState;
  met_at: string | null;
  met_by: PersonRef | null;
  reference: string;
  note: string;
}

export interface Clock {
  id: string;
  ref: string;
  regime: RegimeKey;
  regime_name: string;
  authority: string;
  title: string;
  owner: PersonRef | null;
  aware_at: string;
  starts_at: string | null;
  classified_at: string | null;
  needs_classification: boolean;
  timezone: string;
  custom_milestones: CustomMilestone[];
  milestones: ClockMilestone[];
  closed_at: string | null;
  closed_by: PersonRef | null;
  close_reason: string;
  created_by: PersonRef | null;
  created_at: string;
  version: number;
  can_manage: boolean;
  can_mark: boolean;
}

export interface Regime {
  key: RegimeKey;
  name: string;
  authority: string;
  timezone: string;
  source: string;
  custom: boolean;
  needs_classification: boolean;
  milestones: { key: string; name: string; counted: string }[];
}

export interface MyDeadline {
  clock_id: string;
  clock_ref: string;
  regime_name: string;
  title: string;
  key: string;
  name: string;
  due_at: string;
  starts_at: string;
  provisional: boolean;
  state: ClockState;
  owner: PersonRef | null;
  as_owner: boolean;
  incident: { id: string; ref: string; title: string; severity: Severity; tlp: Tlp };
}

export const NUDGE_AT = [0.5, 0.75, 0.9];

// The state at `now`, from the deadline and when its time started: what the server says, moved on
export function stateAt(m: Pick<ClockMilestone, 'due_at' | 'starts_at' | 'met_at'>, now: number): ClockState {
  if (m.met_at) return m.due_at && Date.parse(m.met_at) > Date.parse(m.due_at) ? 'met_late' : 'met';
  if (!m.due_at || !m.starts_at) return 'waiting';
  const due = Date.parse(m.due_at);
  const start = Date.parse(m.starts_at);
  if (now >= due) return 'breached';
  const gone = due > start ? (now - start) / (due - start) : 0;
  if (gone >= NUDGE_AT[2]) return 'closing';
  if (gone >= NUDGE_AT[1]) return 'near';
  return 'running';
}

// How much of the time has gone, 0 to 1, for the bar under a countdown
export function gone(m: Pick<ClockMilestone, 'due_at' | 'starts_at'>, now: number): number {
  if (!m.due_at || !m.starts_at) return 0;
  const due = Date.parse(m.due_at);
  const start = Date.parse(m.starts_at);
  if (due <= start) return now >= due ? 1 : 0;
  return Math.min(1, Math.max(0, (now - start) / (due - start)));
}

// "2 d 04:12:09" to go, or the same length overdue
export function countdown(dueAt: string, now: number): string {
  let seconds = Math.floor(Math.abs(Date.parse(dueAt) - now) / 1000);
  const days = Math.floor(seconds / 86_400);
  seconds -= days * 86_400;
  const pad = (n: number) => String(n).padStart(2, '0');
  const clock = `${pad(Math.floor(seconds / 3600))}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;
  return days ? `${days} d ${clock}` : clock;
}

export function countdownWords(dueAt: string, now: number): string {
  const late = now >= Date.parse(dueAt);
  return late ? `${countdown(dueAt, now)} overdue` : `${countdown(dueAt, now)} to go`;
}

export const stateNames: Record<ClockState, string> = {
  waiting: 'Waiting',
  running: 'Running',
  near: 'Due soon',
  closing: 'Closing',
  breached: 'Breached',
  met: 'Met',
  met_late: 'Met late',
};

export const unitNames: Record<Unit, string> = { hours: 'hours', days: 'days', months: 'months' };
export const basisNames: Record<Basis, string> = {
  aware: 'from awareness',
  classified: 'from classification',
  previous: 'from the milestone before',
};
