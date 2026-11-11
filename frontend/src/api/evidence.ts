// Exhibits, their chain of custody, and the incident graph (backend/ostraka/evidence_schemas.py)

import type { Tlp } from '../brand';
import type { PersonRef } from './incidents';

export type CustodyKind = 'received' | 'offered' | 'accepted' | 'declined' | 'withdrawn';

export interface CustodyEntry {
  position: number;
  kind: CustodyKind;
  at: string;
  recorded_at: string;
  actor: PersonRef | null;
  from_person: PersonRef | null;
  to_person: PersonRef | null;
  purpose: string;
  location: string;
  acknowledgement: string;
  hash_check: 'match' | 'mismatch' | null;
  offer_position: number | null;
  hash: string;
}

export interface Exhibit {
  id: string;
  ref: string;
  label: string;
  description: string;
  sha256: string | null;
  sha1: string | null;
  md5: string | null;
  masked: boolean;
  size_bytes: number | null;
  location: string;
  acquired_at: string | null;
  created_by: PersonRef | null;
  created_at: string;
  holder: PersonRef | null;
  pending: CustodyEntry | null;
  hash_mismatch: boolean;
  cited_by: { id: string; ref: string }[];
  entries: CustodyEntry[];
  chain: { ok: boolean; entries: number; broken_at: number | null; head: string };
  can_offer: boolean;
  can_answer: boolean;
  can_withdraw: boolean;
  version: number;
}

export type NodeType = 'observable' | 'asset' | 'technique' | 'finding' | 'evidence';
export type EdgeKind =
  | 'sighted_on'
  | 'cites_observable'
  | 'about_asset'
  | 'uses_technique'
  | 'cites_evidence'
  | 'connection'
  | 'connection_technique';

export interface GraphNode {
  id: string;
  type: NodeType;
  ref: string;
  label: string;
  entity_id: string | null;
  attrs: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  attrs: Record<string, unknown>;
}

export interface TechniqueUse {
  technique_id: string;
  name: string;
  tactics: string[];
  confirmed: number;
  leads: number;
  refuted: number;
  connections: number;
  findings: string[];
  first_seen: string | null;
}

export interface Graph {
  incident_id: string;
  ref: string;
  tlp: Tlp;
  restricted: boolean;
  masked: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  techniques: TechniqueUse[];
  attack_version: string | null;
}

export const nodeTypes: [NodeType, string][] = [
  ['asset', 'Assets'],
  ['observable', 'Observables'],
  ['evidence', 'Exhibits'],
  ['finding', 'Findings'],
  ['technique', 'Techniques'],
];

export const edgeWords: Record<EdgeKind, string> = {
  sighted_on: 'seen on',
  cites_observable: 'cites',
  about_asset: 'is about',
  uses_technique: 'uses',
  cites_evidence: 'cites exhibit',
  connection: 'connected to',
  connection_technique: 'connection used',
};
