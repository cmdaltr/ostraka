import { describe, expect, it } from 'vitest';
import { TEAM_COLOURS, teamColour, teamSlots } from './teamColour';

describe('team colours', () => {
  it('keeps a team’s colour beside a team it doesn’t clash with', () => {
    const alone = teamSlots(['team-a']).get('team-a');
    expect(teamSlots(['team-z', 'team-a']).get('team-a')).toBe(alone);
  });

  it('never gives two teams in one drawing the same colour while colours are left', () => {
    const ids = Array.from({ length: TEAM_COLOURS }, (_, i) => `team-${i}`);
    const slots = teamSlots(ids);
    expect(new Set(slots.values()).size).toBe(TEAM_COLOURS);
    expect([...slots.values()].every((s) => s >= 1 && s <= TEAM_COLOURS)).toBe(true);
  });

  it('shares colours once there are more teams than colours', () => {
    const ids = Array.from({ length: TEAM_COLOURS + 2 }, (_, i) => `team-${i}`);
    expect(teamSlots(ids).size).toBe(TEAM_COLOURS + 2);
  });

  it('draws no team in red ochre', () => {
    expect(teamColour(undefined)).toBe('var(--ochre)');
    expect(teamColour(3)).toBe('var(--team-3)');
  });
});
