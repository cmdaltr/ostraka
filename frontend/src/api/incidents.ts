// Incident shapes (backend/ostraka/incident_schemas.py) and the query keys live updates invalidate

import type { Phase, RecordKind, Severity, Tlp } from '../brand';

export type IncidentRole = 'commander' | 'comms' | 'scribe' | 'responder';
export type Verdict = 'unknown' | 'suspicious' | 'malicious' | 'benign';
export type FindingKind = 'evidence' | 'intelligence' | 'potential';
export type FindingStatus = 'lead' | 'confirmed' | 'refuted';
export type AssetStatus = 'identified' | 'investigating' | 'contained' | 'eradicated' | 'recovered' | 'clean';
export type LinkKind = 'lateral_movement' | 'c2' | 'exfiltration' | 'other';

export interface PersonRef {
  id: string;
  name: string;
}

export interface IncidentSummary {
  id: string;
  kind: RecordKind;
  ref: string;
  title: string;
  severity: Severity;
  tlp: Tlp;
  phase: Phase;
  status: 'open' | 'closed';
  commander: PersonRef | null;
  teams: PersonRef[];
  declared_at: string;
  updated_at: string;
  tags: string[];
}

export interface Incident extends IncidentSummary {
  summary: string;
  aware_at: string;
  closed_at: string | null;
  members: { user: PersonRef; role: IncidentRole }[];
  version: number;
  can_edit: boolean;
  can_manage: boolean;
  // Sensitive: only the commander exports reports
  sensitive: boolean;
  can_export: boolean;
  brand_id: string | null;
  counts: { observables: number; assets: number; findings: number; comments: number; tasks: number };
}

export interface Sighting {
  id: string;
  seen_at: string | null;
  asset_id: string | null;
  host: string;
  user_account: string;
  source: string;
  recorded_at: string;
}

export interface IncidentObservable {
  id: string;
  ref: string;
  kind: string;
  value: string | null;
  masked: boolean;
  verdict: Verdict;
  note: string;
  tags: string[];
  sightings: Sighting[];
  added_at: string;
  version: number;
  elsewhere: { visible: { id: string; ref: string; title: string }[]; hidden: number; ask: string[] };
}

export interface Extracted {
  kind: string;
  value: string;
  confidence: number;
  already_in_incident: boolean;
}

export interface Asset {
  id: string;
  ref: string;
  hostname: string;
  ip: string;
  public_ip: string;
  os: string;
  primary_user: string;
  purpose: string;
  data_types: string[];
  criticality: Severity;
  status: AssetStatus;
  is_patient_zero: boolean;
  tags: string[];
  notes: string;
  version: number;
}

export interface AssetLink {
  id: string;
  source_id: string;
  target_id: string;
  kind: LinkKind;
  seen_at: string | null;
  technique: string;
  // Where the connection was seen
  source: string;
  note: string;
}

export interface Verification {
  id: string;
  method: 'search' | 'rowanholt' | 'other';
  platform: string;
  query: string;
  scope: string;
  time_from: string | null;
  time_to: string | null;
  result_count: number | null;
  reference: string;
  note: string;
  ran_by: PersonRef | null;
  ran_at: string;
}

export interface Finding {
  id: string;
  ref: string;
  kind: FindingKind;
  title: string;
  statement: string;
  status: FindingStatus;
  occurred_at: string | null;
  asset_id: string | null;
  techniques: string[];
  tags: string[];
  observable_ids: string[];
  source_reliability: string | null;
  information_credibility: string | null;
  handling_code: string | null;
  handling_conditions: string;
  created_by: PersonRef | null;
  created_at: string;
  decided_by: PersonRef | null;
  decided_at: string | null;
  decision_note: string;
  verifications: Verification[];
  version: number;
}

export interface Comment {
  id: string;
  target_type: 'incident' | 'finding' | 'asset' | 'observable' | 'task';
  target_id: string | null;
  parent_id: string | null;
  author: PersonRef | null;
  body: string;
  created_at: string;
  edited_at: string | null;
  deleted: boolean;
  resolved_at: string | null;
  version: number;
}

export interface ActivityEvent {
  position: number;
  at: string;
  actor: PersonRef | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  summary: string;
}

export interface TimelineItem {
  at: string;
  recorded_at: string;
  kind: 'sighting' | 'finding' | 'link';
  ref: string;
  title: string;
  detail: string;
  entity_id: string;
  asset: string | null;
  techniques: string[];
}

export const roleLabels: Record<IncidentRole, string> = {
  commander: 'Incident commander',
  comms: 'Comms',
  scribe: 'Scribe',
  responder: 'Responder',
};

// Another record linked to this one; records the viewer can't open arrive only as a count
export interface LinkedRecord {
  id: string;
  link_id: string;
  kind: RecordKind;
  ref: string;
  title: string;
  status: 'open' | 'closed';
  phase: Phase;
  severity: Severity;
}

export interface Links {
  parent: LinkedRecord | null;
  children: LinkedRecord[];
  related: LinkedRecord[];
  duplicate_of: LinkedRecord | null;
  duplicates: LinkedRecord[];
  promoted_from: LinkedRecord | null;
  promoted_to: LinkedRecord[];
  hidden: number;
}

export type LinkRelation = 'child_of' | 'parent_of' | 'related_to' | 'duplicate_of';

export const keys = {
  list: (status: string, kind: string = 'all') => ['incidents', status, kind] as const,
  incident: (id: string) => ['incident', id] as const,
  section: (id: string, section: string) => ['incident', id, section] as const,
};

// Which cached sections an activity event makes stale
export function sectionsFor(entityType: string): string[] {
  switch (entityType) {
    case 'observable':
      return ['observables', 'timeline', 'observables-working', 'graph'];
    case 'asset':
    case 'asset_link':
      return ['assets', 'links', 'timeline', 'graph'];
    case 'finding':
      return ['findings', 'timeline', 'rowanholt', 'detection-gaps', 'graph', 'exhibits'];
    case 'exhibit':
      return ['exhibits', 'graph'];
    case 'comment':
      return ['comments'];
    case 'task':
      return ['tasks', 'playbooks', 'ach', 'lessons'];
    case 'assessment':
      return ['assessments'];
    case 'hypothesis':
    case 'evidence':
      return ['ach'];
    case 'lesson':
      return ['lessons'];
    case 'clock':
      return ['clocks'];
    case 'link':
      return ['record-links'];
    case 'handover':
      return ['handover'];
    case 'rowanholt':
      return ['rowanholt'];
    case 'detection_gap':
      return ['detection-gaps', 'tasks'];
    default:
      return [];
  }
}

export const label = (value: string) => value.replace(/_/g, ' ');
