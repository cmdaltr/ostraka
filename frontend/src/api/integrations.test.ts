import { describe, expect, it } from 'vitest';
import { gapState, pairingLink, runSummary, statusWords } from './integrations';
import type { Gap, ShikariRun } from './integrations';

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

describe('the suite', () => {
  const run = (over: Partial<ShikariRun>): ShikariRun => ({
    id: 'r', run_id: 'x', kind: 'group', target: 'APT29', execute: true, window_days: 7, status: 'queued', error: '',
    detections: [], counts: [], totals: {}, started_by: null, started_at: '', checked_at: null, link: null, ...over,
  });

  it('says what a run found in one line', () => {
    expect(runSummary(run({ status: 'executed', totals: { queries: 4, run: 3, hits: 12, skipped: 1 } }))).toBe(
      '3 of 4 queries run · 12 results · 1 had no SIEM connection',
    );
    expect(runSummary(run({ status: 'failed', error: 'no such group' }))).toBe('Failed: no such group');
    expect(runSummary(run({ status: 'generated', detections: [{ file: 'a', name: 'a', techniques: [], platforms: [], queries: [] }] }))).toBe(
      'generated: 1 detection',
    );
  });

  it('knows a pairing link when it sees one', () => {
    expect(pairingLink.test('https://rowanholt.example.org/pair#pair_' + 'a'.repeat(43))).toBe(true);
    expect(pairingLink.test('https://rowanholt.example.org/pair')).toBe(false);
    expect(pairingLink.test('rvt_' + 'a'.repeat(43))).toBe(false);
  });
});
