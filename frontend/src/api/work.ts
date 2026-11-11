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
  overdue: boolean;
  checklist: ChecklistItem[];
  blocked_by: TaskLink[];
  blocks: TaskLink[];
  hidden_links: number;
  playbook_id: string | null;
  // A decision: finished by choosing one of these
  outcomes: string[];
  outcome: string | null;
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

export type OutcomeAction = 'none' | 'close' | 'phase' | 'promote';

// One way a decision can go: what it does to the record, and the steps it opens (an earlier one runs again)
export interface Outcome {
  label: string;
  action: OutcomeAction;
  phase: string | null;
  opens: number[];
}

export interface PlaybookStep {
  id?: string;
  title: string;
  description: string;
  team: PersonRef | null;
  due_minutes: number | null;
  checklist: string[];
  after: number[];
  phase: string | null;
  outcomes: Outcome[];
}

export type StepState = 'waiting' | 'open' | 'done' | 'skipped';

export interface WorkflowStep {
  position: number;
  title: string;
  team: PersonRef | null;
  after: number[];
  phase: string | null;
  outcomes: Outcome[];
  state: StepState;
  conditional: boolean;
  task_id: string | null;
  task_ref: string | null;
  outcome: string | null;
  runs: number;
}

export interface Playbook {
  id: string;
  name: string;
  description: string;
  trigger_tags: string[];
  applies_to: RecordKind;
  is_active: boolean;
  steps: PlaybookStep[];
  updated_at: string;
  version: number;
}

export interface AppliedPlaybook {
  playbook_id: string;
  name: string;
  applied_by: PersonRef | null;
  applied_at: string;
  steps: WorkflowStep[];
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
  | 'unblocked';

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

// Where each step sits in a swimlane drawing: its lane (team) and its column (how many steps lead to it)
export function layout(steps: Pick<WorkflowStep, 'after' | 'outcomes' | 'team'>[]): { lane: number; column: number }[] {
  const openedBy = new Map<number, number[]>();
  steps.forEach((s, i) =>
    s.outcomes.forEach((o) => o.opens.filter((p) => p > i).forEach((p) => openedBy.set(p, [...(openedBy.get(p) ?? []), i]))),
  );
  const column: number[] = [];
  steps.forEach((s, i) => {
    const before = [...s.after, ...(openedBy.get(i) ?? [])];
    column[i] = before.length ? Math.max(...before.map((b) => column[b] + 1)) : 0;
  });
  const lanes: string[] = [];
  return steps.map((s, i) => {
    const name = s.team?.name ?? '';
    if (!lanes.includes(name)) lanes.push(name);
    return { lane: lanes.indexOf(name), column: column[i] };
  });
}
