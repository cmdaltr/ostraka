// Stakeholder updates and the observers' briefing (backend/ostraka/update_schemas.py)

import type { LintFlag } from './analysis';
import type { PersonRef } from './incidents';

export type RhythmMode = 'severity' | 'every' | 'on_change';

export interface Plan {
  mode: RhythmMode;
  every_minutes: number | null;
  words: string;
  by_severity: { severity: string; every_minutes: number | null; words: string }[];
  last_sent_at: string | null;
  next_due_at: string | null;
  set_by: PersonRef | null;
  version: number;
}

export interface Stakeholder {
  id: string;
  kind: 'person' | 'email' | 'teams';
  name: string;
  email: string | null;
  can_receive: boolean;
  why_not: string;
}

export interface UpdateLine {
  section: 'assessment' | 'what' | 'position' | 'note' | 'next';
  kind: string;
  ref: string;
  text: string;
  rephrased?: string | null;
}

export interface Revision {
  version: number;
  change: string;
  by: PersonRef | null;
  at: string;
  lines: string[];
}

export interface StakeholderUpdate {
  id: string;
  ref: string;
  number: number;
  status: 'draft' | 'sent' | 'discarded';
  since: string;
  until: string;
  assessment: string | null;
  lines: UpdateLine[];
  note: string;
  rephrased_by: string;
  use_rephrased: boolean;
  flags: LintFlag[];
  can_approve: boolean;
  why_not: string;
  drafted_by: PersonRef | null;
  drafted_at: string;
  approved_by: PersonRef | null;
  approved_at: string | null;
  recipients: { to: string; channel: string; sent: boolean }[];
  revisions: Revision[];
  version: number;
}

export interface Updates {
  plan: Plan;
  stakeholders: Stakeholder[];
  draft: StakeholderUpdate | null;
  sent: StakeholderUpdate[];
  can_run: boolean;
  can_approve: boolean;
  why_not: string;
  email_enabled: boolean;
  ai: string;
  ai_why_not: string;
  restricted: boolean;
}

export interface Briefing {
  ref: string;
  title: string;
  severity: string;
  tlp: string;
  phase: string;
  status: string;
  commander: string;
  declared_at: string;
  confirmed: number;
  leads: number;
  assets: number;
  contained: number;
  tasks_open: number;
  tasks_done: number;
  assessment: string[];
  rhythm: string;
  next_due_at: string | null;
  updates: { ref: string; number: number; approved_at: string | null; approved_by: string; lines: UpdateLine[]; ai_worded: boolean }[];
}

export const sectionNames: Record<UpdateLine['section'], string> = {
  assessment: 'Assessment',
  what: 'What has changed',
  position: 'Where things stand',
  note: 'From the communications lead',
  next: 'Next',
};

// Lines grouped under their headings, in order, for showing an update
export function bySection(lines: UpdateLine[]): [UpdateLine['section'], UpdateLine[]][] {
  const out: [UpdateLine['section'], UpdateLine[]][] = [];
  for (const line of lines) {
    const last = out[out.length - 1];
    if (last && last[0] === line.section) last[1].push(line);
    else out.push([line.section, [line]]);
  }
  return out;
}
