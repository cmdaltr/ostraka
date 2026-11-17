import { describe, expect, it } from 'vitest';
import { exampleRun } from './Workflow';
import type { DrawnStep } from './Workflow';

const step = (title: string, after: number[] = [], outcomes: DrawnStep['outcomes'] = []): DrawnStep => ({
  title,
  team: null,
  after,
  phase: null,
  outcomes,
});

describe('an example run', () => {
  it('has the first half done, the next step open and the rest waiting', () => {
    const run = exampleRun([step('Triage'), step('Scope', [0]), step('Contain', [1]), step('Recover', [2])]);
    expect(run.map((s) => s.state)).toEqual(['done', 'done', 'open', 'waiting']);
  });

  it('takes a decision’s first outcome, and skips the step only the other outcome opens', () => {
    const run = exampleRun([
      step('Was it clicked?', [], [
        { label: 'Yes', action: 'none', phase: null, opens: [1] },
        { label: 'No', action: 'close', phase: null, opens: [2] },
      ]),
      step('Revoke sessions'),
      step('Close it'),
      step('Tell the user', [1]),
    ]);
    expect(run[0]).toMatchObject({ state: 'done', outcome: 'Yes' });
    expect(run[1].state).toBe('done');
    expect(run[2].state).toBe('skipped');
    expect(run[3].state).toBe('open');
  });

  it('runs a step a later decision can send the work back to, as any other', () => {
    const run = exampleRun([
      step('Hypothesis'),
      step('Collect', [0]),
      step('Did it hold?', [1], [{ label: 'Inconclusive', action: 'none', phase: null, opens: [1] }]),
      step('Write it up', [2]),
    ]);
    expect(run.map((s) => s.state)).toEqual(['done', 'done', 'open', 'waiting']);
  });
});
