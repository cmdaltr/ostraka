// Handover and presence shapes (backend/ostraka/handover_schemas.py)

import type { ActivityEvent, PersonRef } from './incidents';

export interface SinceYouLeft {
  first_visit: boolean;
  last_viewed_at: string | null;
  from_position: number;
  through_position: number;
  total: number;
  changes: { entity_type: string; count: number }[];
  people: string[];
  events: ActivityEvent[];
}

export interface HandoverDraft {
  from_position: number;
  through_position: number;
  since: string;
  sections: { title: string; lines: string[] }[];
  text: string;
}

export interface HandoverNote {
  id: string;
  ref: string;
  author: PersonRef | null;
  from_position: number;
  through_position: number;
  body: string;
  edited: boolean;
  created_at: string;
}

// Someone else is working this observable in another open incident
export interface Working {
  item_id: string;
  visible: { incident_id: string; ref: string; title: string; people: string[]; editing: boolean; last_at: string | null }[];
  hidden: number;
  ask: string[];
}

const kindNames: Record<string, [string, string]> = {
  incident: ['change to the incident', 'changes to the incident'],
  observable: ['observable', 'observables'],
  task: ['task', 'tasks'],
  finding: ['finding', 'findings'],
  asset: ['asset', 'assets'],
  asset_link: ['asset link', 'asset links'],
  comment: ['comment', 'comments'],
  assessment: ['assessment', 'assessments'],
  hypothesis: ['hypothesis', 'hypotheses'],
  evidence: ['piece of evidence', 'pieces of evidence'],
  lesson: ['lesson', 'lessons'],
  handover: ['handover', 'handovers'],
  user: ['change of people', 'changes of people'],
  team: ['change of teams', 'changes of teams'],
  link: ['linked record', 'linked records'],
};

// "3 findings, 1 task": what changed, in words
export function describeChanges(changes: SinceYouLeft['changes']): string {
  return changes
    .map(({ entity_type, count }) => {
      const [one, many] = kindNames[entity_type] ?? [entity_type.replace(/_/g, ' '), `${entity_type.replace(/_/g, ' ')}s`];
      return `${count} ${count === 1 ? one : many}`;
    })
    .join(', ');
}
