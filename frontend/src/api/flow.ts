// The attack flow (backend ostraka/flow.py): its stages in ATT&CK order, and what GET /flow returns
export const flowStages = [
  ['prepares', 'Prepares'],
  ['gets_in', 'Gets in'],
  ['runs', 'Runs'],
  ['stays', 'Stays and rises'],
  ['hides', 'Hides'],
  ['takes_credentials', 'Takes credentials'],
  ['moves', 'Looks around and moves'],
  ['talks_home', 'Talks home'],
  ['takes', 'Collects and takes'],
  ['impact', 'Does damage'],
] as const;

export type StageCall = 'expected' | 'dismissed' | 'cleared';
type Technique = { id: string; name: string };

export type FlowStage = {
  key: string;
  name: string;
  says: string;
  layer: 'seen' | 'expected' | 'added';
  coverage: 'covered' | 'focused' | 'open';
  why: string;
  findings: { ref: string; title: string; status: string }[];
  links: { source: string; target: string; technique: string }[];
  techniques: Technique[];
  suggestions: (Technique & { groups: number })[];
  tasks_open: { ref: string; title: string }[];
  tasks_done: number;
  call: { call: StageCall; note: string; by: string; at: string } | null;
};

export type Flow = {
  stages: FlowStage[];
  dismissed: { key: string; name: string; note: string; by: string; at: string }[];
  covered: number;
  peers: number;
  group: { id: string; name: string } | null;
  all_stages: { id: string; name: string }[];
  can_edit: boolean;
};
