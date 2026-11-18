// The attack flow (backend ostraka/flow.py): its stages in ATT&CK order, and what GET /flow returns
// The attack flow (backend ostraka/flow.py): the attack's anatomy, in Shikari's words (ostraka/anatomy.py),
// and what GET /flow returns
export type FlowColumn = 'way' | 'toolkit' | 'action' | 'service';

export const columnNames: Record<FlowColumn, string> = {
  way: 'Ways in',
  toolkit: 'Toolkit',
  action: 'Actions',
  service: 'Where it goes',
};

export const flowSteps: readonly (readonly [string, string, FlowColumn])[] = [
  ['exploit', 'Exploit', 'way'],
  ['phishing', 'Phishing', 'way'],
  ['credentials', 'Credentials', 'way'],
  ['toolkit', 'Toolkit', 'toolkit'],
  ['steals_logins', 'Steals logins', 'action'],
  ['moves', 'Moves across the network', 'action'],
  ['admin', 'Gains admin rights', 'action'],
  ['keeps_access', 'Keeps access', 'action'],
  ['hides', 'Hides its tracks', 'action'],
  ['looks_around', 'Looks around', 'action'],
  ['gathers', 'Gathers data', 'action'],
  ['takes_out', 'Takes data out', 'action'],
  ['destroys', 'Encrypts or destroys', 'action'],
  ['extorts', 'Extorts', 'action'],
  ['cloud', 'Cloud', 'service'],
  ['messaging', 'Messaging', 'service'],
  ['storage', 'Code and storage', 'service'],
  ['infrastructure', 'Infrastructure', 'service'],
];

export type StageCall = 'expected' | 'dismissed' | 'cleared';
type Technique = { id: string; name: string };

export type FlowStage = {
  key: string;
  name: string;
  column: FlowColumn;
  verb: string; // the step into it: Phishes, Runs, Beacons to
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
  items: string[]; // what the record names there: CVEs, tools, services
};

export type Flow = {
  stages: FlowStage[];
  dismissed: { key: string; name: string; note: string; by: string; at: string }[];
  covered: number;
  peers: number;
  group: { id: string; name: string } | null;
  all_stages: { id: string; name: string; column: FlowColumn }[];
  can_edit: boolean;
};
