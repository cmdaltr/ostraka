import { describe, expect, it } from 'vitest';
import { describeChanges } from './handover';
import { sectionsFor } from './incidents';
import { othersOn } from '../live/presence';

describe('since you left', () => {
  it('says what changed in words', () => {
    expect(
      describeChanges([
        { entity_type: 'finding', count: 3 },
        { entity_type: 'task', count: 1 },
        { entity_type: 'hypothesis', count: 2 },
        { entity_type: 'something_new', count: 2 },
      ]),
    ).toBe('3 findings, 1 task, 2 hypotheses, 2 something news');
  });
});

describe('presence on a record', () => {
  const people = [
    { user_id: 'me', name: 'Sam', focus: 'finding:f-1', editing: true },
    { user_id: 'u-2', name: 'Dana', focus: 'finding:f-1', editing: false },
    { user_id: 'u-3', name: 'Lee', focus: 'findings', editing: false },
  ];
  it('lists the others on it, never yourself', () => {
    expect(othersOn(people, 'me', 'finding:f-1').map((p) => p.name)).toEqual(['Dana']);
    expect(othersOn(people, 'me', 'task:t-1')).toEqual([]);
  });
});

describe('live invalidation for handover', () => {
  it('refreshes the handover list and work elsewhere', () => {
    expect(sectionsFor('handover')).toEqual(['handover']);
    expect(sectionsFor('observable')).toContain('observables-working');
  });
});
