// Assessments, ACH and lessons learned (backend/ostraka/analysis_schemas.py)

import type { FindingStatus, PersonRef } from './incidents';

export type Probability =
  | 'remote_chance'
  | 'highly_unlikely'
  | 'unlikely'
  | 'realistic_possibility'
  | 'likely'
  | 'highly_likely'
  | 'almost_certain';

// The PHIA yardstick, in order: the only words an assessment uses for likelihood
export const yardstick: [Probability, string, string][] = [
  ['remote_chance', 'Remote chance', '≤5%'],
  ['highly_unlikely', 'Highly unlikely', '10–20%'],
  ['unlikely', 'Unlikely', '25–35%'],
  ['realistic_possibility', 'Realistic possibility', '40–<50%'],
  ['likely', 'Likely or probably', '55–75%'],
  ['highly_likely', 'Highly likely', '80–90%'],
  ['almost_certain', 'Almost certain', '≥95%'],
];

export interface NegativeEvidence {
  id?: string;
  absent: string;
  platform: string;
  query: string;
  scope: string;
  time_from: string | null;
  time_to: string | null;
}

export interface LintFlag {
  rule: string;
  severity: 'error' | 'warning';
  field: string;
  message: string;
  words: string[];
}

export interface Assessment {
  id: string;
  ref: string;
  judgement: string;
  probability: Probability;
  probability_words: string;
  probability_range: string;
  so_what: string;
  what_next: string;
  status: 'current' | 'superseded';
  supersedes: string | null;
  superseded_at: string | null;
  basis: { finding_id: string; ref: string; title: string; status: FindingStatus }[];
  absences: NegativeEvidence[];
  sentences: string[];
  flags: LintFlag[];
  created_by: PersonRef | null;
  created_at: string;
}

export type Rating = 'c' | 'i' | 'na';
export type Weight = 'h' | 'm' | 'l';
export type EvidenceKind = 'finding' | 'observable' | 'assumption' | 'absence';

export interface TaskRef {
  id: string;
  ref: string;
  status: string;
}

export interface Hypothesis {
  id: string;
  ref: string;
  statement: string;
  status: 'open' | 'rejected';
  rejection_reason: string;
  rejected_by: PersonRef | null;
  rejected_at: string | null;
  milestone: string;
  milestone_task: TaskRef | null;
  score: number;
  leading: boolean;
  version: number;
}

export interface AchEvidence {
  id: string;
  ref: string;
  kind: EvidenceKind;
  finding_id: string | null;
  finding_ref: string | null;
  observable_id: string | null;
  text: string;
  credibility: Weight;
  relevance: Weight;
  non_diagnostic: boolean;
  pivotal: boolean;
  lead_without: string | null;
  version: number;
}

export interface AchCell {
  hypothesis_id: string;
  evidence_id: string;
  consensus: Rating | null;
  disputed: boolean;
  mine: Rating | null;
  ratings: { rater: PersonRef; value: Rating }[];
}

export interface Ach {
  hypotheses: Hypothesis[];
  evidence: AchEvidence[];
  cells: AchCell[];
  raters: PersonRef[];
  max_raters: number;
  leading: string | null;
  algorithm: string;
}

export type LessonKind = 'went_well' | 'improve';
export type LessonArea = 'people' | 'process' | 'technology' | 'detection' | 'communication';

export interface Lesson {
  id: string;
  ref: string;
  kind: LessonKind;
  area: LessonArea;
  observation: string;
  recommendation: string;
  task: TaskRef | null;
  created_by: PersonRef | null;
  created_at: string;
  version: number;
}
