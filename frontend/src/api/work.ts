// Task, playbook and notification shapes (backend/ostraka/work_schemas.py)

import type { RecordKind, Severity, Tlp } from '../brand';
import type { PersonRef } from './incidents';

export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled';

export const taskStatusNames: Record<TaskStatus, string> = {
  open: 'Open',
  in_progress: 'In progress',
  blocked: 'Blocked',
  done: 'Done',
  cancelled: 'Cancelled',
};

export const finished = (s: TaskStatus) => s === 'done' || s === 'cancelled';

export interface ChecklistItem {
  id: string;
  text: string;
  done: boolean;
  done_by: PersonRef | null;
  done_at: string | null;
  version: number;
}

// Another task this one waits on, or that waits on it, possibly in another incident
export interface TaskLink {
  id: string;
  ref: string;
  title: string;
  status: TaskStatus;
  incident_ref: string;
}

export interface Task {
  id: string;
  ref: string;
  incident_id: string;
  parent_id: string | null;
  title: string;
  description: string;
  team: PersonRef | null;
  assignee: PersonRef | null;
  status: TaskStatus;
  blocked_reason: string;
  due_at: string | null;
  stage?: string | null; // the attack-flow stage it answers
  overdue: boolean;
  checklist: ChecklistItem[];
  blocked_by: TaskLink[];
  blocks: TaskLink[];
  hidden_links: number;
  playbook_id: string | null;
  // A decision: finished by choosing one of these
  created_by: PersonRef | null;
  created_at: string;
  updated_at: string;
  completed_by: PersonRef | null;
  completed_at: string | null;
  version: number;
}

export interface MyTask extends Task {
  incident: { id: string; kind: RecordKind; ref: string; title: string; severity: Severity; tlp: Tlp };
  queued: boolean;
}

// A playbook's checks hang on the parts of the attack (api/flow.ts anatomyParts) or on none ('any')
export const ANY = 'any';

export interface PlaybookCheck {
  id?: string;
  part: string;
  title: string;
  team: PersonRef | null;
}

export interface Playbook {
  id: string;
  name: string;
  description: string;
  trigger_tags: string[];
  applies_to: RecordKind;
  is_active: boolean;
  conditions: Record<string, string>; // part: only if…
  checks: PlaybookCheck[];
  updated_at: string;
  version: number;
}

// A playbook on a record: the parts taken, and those it has checks for that haven't been
export interface AppliedPlaybook {
  playbook_id: string;
  name: string;
  applied_by: PersonRef | null;
  applied_at: string;
  parts: string[];
  remaining: string[];
  conditions: Record<string, string>;
}

// The parts a playbook has checks in
export function partsOf(playbook: Pick<Playbook, 'checks'>): Set<string> {
  return new Set(playbook.checks.map((c) => c.part));
}

// What applying takes unless someone chooses: every part with checks and no "only if"
export function defaultParts(playbook: Pick<Playbook, 'checks' | 'conditions'>): Set<string> {
  return new Set([...partsOf(playbook)].filter((p) => !playbook.conditions[p]));
}

export type NotificationKind =
  | 'assigned'
  | 'team_task'
  | 'due_soon'
  | 'overdue'
  | 'escalated'
  | 'nudge'
  | 'mention'
  | 'reply'
  | 'commander'
  | 'unblocked'
  | 'clock_due'
  | 'clock_breached'
  | 'update_due'
  | 'stakeholder_update';

export interface Notification {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  link: string;
  actor: PersonRef | null;
  created_at: string;
  read_at: string | null;
}

export interface Notifications {
  items: Notification[];
  unread: number;
}

// Playbooks whose trigger tags overlap the incident's tags: pre-ticked in the declare form
export function suggested(playbooks: Playbook[], tags: string[]): string[] {
  const wanted = new Set(tags.map((t) => t.trim().toLowerCase().replace(/ /g, '-')));
  return playbooks.filter((p) => p.is_active && p.trigger_tags.some((t) => wanted.has(t))).map((p) => p.id);
}

// How long until (or since) a due time, in the shortest honest words
export function dueWords(due: string | null, now = Date.now()): string {
  if (!due) return 'No due date';
  const minutes = Math.round((new Date(due).getTime() - now) / 60_000);
  const span = (m: number) => (m < 60 ? `${m} min` : m < 48 * 60 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} days`);
  if (minutes >= 0) return minutes === 0 ? 'Due now' : `Due in ${span(minutes)}`;
  return `${span(-minutes)} overdue`;
}
