// The attack flow (backend ostraka/flow.py): the attack's anatomy, in Shikari's words (ostraka/anatomy.py),
// and what GET /flow returns
export type FlowColumn = 'way' | 'toolkit' | 'action' | 'service';

export const columnNames: Record<FlowColumn, string> = {
  way: 'Ways in',
  toolkit: 'Toolkit',
  action: 'Actions',
  service: 'Where it goes',
};

export interface AnatomyPart {
  key: string;
  name: string;
  column: FlowColumn;
  verb: string; // the step into it, on the line that reaches it
  says: string; // what the attacker does there, in a few words
  brief?: string; // shorter, for under its node in a drawing
}

const ACTION = 'Something it does once in';
// Every part of the anatomy, in its order: backend ostraka/flow.py STAGES
export const anatomyParts: readonly AnatomyPart[] = [
  { key: 'exploit', name: 'Exploit', column: 'way', verb: 'Exploits', says: 'Public-facing apps' },
  { key: 'phishing', name: 'Phishing', column: 'way', verb: 'Phishes', says: 'Lures' },
  { key: 'credentials', name: 'Credentials', column: 'way', verb: 'Signs in', says: 'Valid accounts' },
  { key: 'toolkit', name: 'Toolkit', column: 'toolkit', verb: 'Runs', says: 'What it runs: frameworks, stealers, web shells, RATs', brief: 'What it runs' },
  { key: 'steals_logins', name: 'Steals logins', column: 'action', verb: 'To', says: ACTION },
  { key: 'moves', name: 'Moves across the network', column: 'action', verb: 'To', says: ACTION },
  { key: 'admin', name: 'Gains admin rights', column: 'action', verb: 'To', says: ACTION },
  { key: 'keeps_access', name: 'Keeps access', column: 'action', verb: 'To', says: ACTION },
  { key: 'hides', name: 'Hides its tracks', column: 'action', verb: 'To', says: ACTION },
  { key: 'looks_around', name: 'Looks around', column: 'action', verb: 'To', says: ACTION },
  { key: 'gathers', name: 'Gathers data', column: 'action', verb: 'To', says: ACTION },
  { key: 'takes_out', name: 'Takes data out', column: 'action', verb: 'To', says: ACTION },
  { key: 'destroys', name: 'Encrypts or destroys', column: 'action', verb: 'To', says: ACTION },
  { key: 'extorts', name: 'Extorts', column: 'action', verb: 'To', says: ACTION },
  { key: 'cloud', name: 'Cloud', column: 'service', verb: 'Uses', says: 'Cloud tenants, Hosting, Tunnels and anonymisers', brief: 'Tenants, hosting, tunnels' },
  { key: 'messaging', name: 'Messaging', column: 'service', verb: 'Beacons to', says: 'Messaging' },
  { key: 'storage', name: 'Code and storage', column: 'service', verb: 'Collects from', says: 'Code and paste sites, Cloud storage', brief: 'Code sites, cloud storage' },
  { key: 'infrastructure', name: 'Infrastructure', column: 'service', verb: 'Connects to', says: 'Its own addresses: C2 and staging', brief: 'C2 and staging' },
];

export const flowSteps: readonly (readonly [string, string, FlowColumn])[] = anatomyParts.map(
  (p) => [p.key, p.name, p.column] as const,
);

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
