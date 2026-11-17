// Each team's colour in a playbook's swimlanes: one of the --team-N tokens, picked by a hash of the team's
// id so a team keeps its colour from drawing to drawing. Two teams in one drawing never share a colour
// while there are colours left: the later id moves on to the next free one.

export const TEAM_COLOURS = 8;

function hash(text: string): number {
  // FNV-1a: small, and stable across browsers
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return h >>> 0;
}

/** Colour slots (1 to TEAM_COLOURS) for the teams in one drawing, by id */
export function teamSlots(ids: string[]): Map<string, number> {
  const slots = new Map<string, number>();
  const taken = new Set<number>();
  for (const id of [...new Set(ids)].sort()) {
    let slot = hash(id) % TEAM_COLOURS;
    if (taken.size < TEAM_COLOURS) while (taken.has(slot)) slot = (slot + 1) % TEAM_COLOURS;
    taken.add(slot);
    slots.set(id, slot + 1);
  }
  return slots;
}

/** The CSS colour for a slot; no team (whoever runs the playbook) is muted */
export const teamColour = (slot: number | undefined) => (slot ? `var(--team-${slot})` : 'var(--muted)');
