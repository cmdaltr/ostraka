import { describe, expect, it } from 'vitest';
import { gapState, statusWords } from './integrations';
import type { Gap } from './integrations';

const gap = (overrides: Partial<Gap> = {}): Gap => ({
  id: 'g-1',
  technique: 'T1003.001',
  technique_name: 'LSASS Memory',
  attack_url: 'https://attack.mitre.org/techniques/T1003/001/',
  status: 'open',
  finding_id: 'f-1',
  finding_ref: 'FND-3',
  task: { id: 't-1', ref: 'TSK-9', title: 'Detection for T1003.001', status: 'open' },
  opened_by: null,
  opened_at: '2026-11-11T10:00:00Z',
  checked_at: '2026-11-11T10:00:00Z',
  check_error: '',
  closed_at: null,
  closed_by: '',
  incident: null,
  ...overrides,
});

describe('Rowanholt status words', () => {
  it('reads a job status, with progress while it runs', () => {
    expect(statusWords('running', 40)).toBe('Running, 40%');
    expect(statusWords('awaiting_confirmation')).toBe('Awaiting confirmation');
    expect(statusWords('completed', 100)).toBe('Completed');
    expect(statusWords('')).toBe('Not seen yet');
  });
});

describe('what a detection gap waits for', () => {
  it('says closed only when Shikari has a detection, never because the task is done', () => {
    expect(gapState(gap({ status: 'closed' }))).toBe('Detected in Shikari');
    expect(gapState(gap({ task: { id: 't-1', ref: 'TSK-9', title: '', status: 'done' } }))).toBe(
      'Task done, Shikari has no detection yet',
    );
    expect(gapState(gap())).toBe('Waiting for a detection');
    expect(gapState(gap({ task: null }))).toBe('No task yet');
    expect(gapState(gap({ check_error: "Shikari can't be reached" }))).toBe('Not checked: Shikari didn’t answer');
  });
});
