// Mirrors backend/ostraka/branding.py. Components take the product's name and wording from here.

export const brand = {
  name: 'Ostraka',
  tagline: 'Incident management and response',
  // What the letters stand for
  expansion: 'Operational Suite for Teams to Respond, Assess, Keep records & take Action',
  incidentPrefix: 'OST',
  huntPrefix: 'HNT',
} as const;

export const phases = ['detect', 'scope', 'contain', 'eradicate', 'recover', 'learn'] as const;
export const huntPhases = ['hypothesis', 'collect', 'analyse', 'conclude'] as const;
export type Phase = (typeof phases)[number] | (typeof huntPhases)[number];

export type RecordKind = 'incident' | 'hunt';
export const phasesOf = (kind: RecordKind): readonly Phase[] => (kind === 'hunt' ? huntPhases : phases);
export const kindName = (kind: RecordKind) => (kind === 'hunt' ? 'hunt' : 'incident');

export const severities = ['critical', 'high', 'medium', 'low'] as const;
export type Severity = (typeof severities)[number];

export const tlpLevels = ['red', 'amber+strict', 'amber', 'green', 'clear'] as const;
export type Tlp = (typeof tlpLevels)[number];
