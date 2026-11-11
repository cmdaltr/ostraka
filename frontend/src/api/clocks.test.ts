import { describe, expect, it } from 'vitest';
import { countdown, countdownWords, gone, stateAt } from './clocks';

const start = '2026-11-10T09:00:00Z';
const due = '2026-11-13T09:00:00Z'; // 72 h on
const at = (hours: number) => Date.parse(start) + hours * 3_600_000;

describe('clock states', () => {
  it('move with the nudge thresholds, then breach', () => {
    const m = { due_at: due, starts_at: start, met_at: null };
    expect(stateAt(m, at(10))).toBe('running');
    expect(stateAt(m, at(54))).toBe('near');
    expect(stateAt(m, at(64.8))).toBe('closing');
    expect(stateAt(m, at(72))).toBe('breached');
  });
  it('say whether a met milestone was in time', () => {
    expect(stateAt({ due_at: due, starts_at: start, met_at: '2026-11-13T09:00:00Z' }, at(80))).toBe('met');
    expect(stateAt({ due_at: due, starts_at: start, met_at: '2026-11-13T09:00:01Z' }, at(80))).toBe('met_late');
    expect(stateAt({ due_at: null, starts_at: null, met_at: '2026-11-13T09:00:01Z' }, at(80))).toBe('met');
  });
  it('wait when there is no deadline yet', () => {
    expect(stateAt({ due_at: null, starts_at: null, met_at: null }, at(1))).toBe('waiting');
  });
});

describe('countdowns', () => {
  it('count down to the second, with days when there are any', () => {
    expect(countdown(due, at(71) + 1000)).toBe('00:59:59');
    expect(countdown(due, at(20))).toBe('2 d 04:00:00');
    expect(countdownWords(due, at(70))).toBe('02:00:00 to go');
    expect(countdownWords(due, at(75.5))).toBe('03:30:00 overdue');
  });
  it('fill a bar with the time gone, within bounds', () => {
    expect(gone({ due_at: due, starts_at: start }, at(36))).toBe(0.5);
    expect(gone({ due_at: due, starts_at: start }, at(-5))).toBe(0);
    expect(gone({ due_at: due, starts_at: start }, at(100))).toBe(1);
    expect(gone({ due_at: null, starts_at: null }, at(1))).toBe(0);
  });
});
