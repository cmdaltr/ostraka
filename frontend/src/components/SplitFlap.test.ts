import { afterEach, describe, expect, it, vi } from 'vitest';
import { DIAL, HOLD_MS, NAME_HOLD_MS, WIDTH, WORDS, isMalware, layout, nextWord, step } from './SplitFlap';

describe('the departures board', () => {
  afterEach(() => vi.restoreAllMocks());

  it('lands on the name on half the turns, and holds it half a second longer', () => {
    const random = vi.spyOn(Math, 'random');
    random.mockReturnValueOnce(0.49);
    expect(nextWord('ALERT', 'OSTRAKA')).toBe('OSTRAKA');
    random.mockReturnValueOnce(0.5).mockReturnValueOnce(0);
    expect(nextWord('OSTRAKA', 'OSTRAKA')).not.toBe('OSTRAKA');
    expect(NAME_HOLD_MS - HOLD_MS).toBe(500);
  });

  it('never shows the same word twice running, unless it’s the name', () => {
    vi.spyOn(Math, 'random').mockReturnValueOnce(0.9).mockReturnValueOnce(0);
    expect(nextWord(WORDS[0], 'OSTRAKA')).toBe(WORDS[1]);
  });

  it('only uses words that fit the board and that every tile can show', () => {
    for (const word of WORDS) {
      expect([...word].length).toBeLessThanOrEqual(WIDTH);
      expect([...word].every((ch) => DIAL.includes(ch))).toBe(true);
    }
    expect([...'OSTRAKA'].every((ch) => DIAL.includes(ch))).toBe(true);
  });

  it('centres a short word, padding with blank tiles', () => {
    expect(layout('ALERT')).toEqual([' ', 'A', 'L', 'E', 'R', 'T', ' ']);
    expect(layout('OSTRAKA')).toEqual([...'OSTRAKA']);
  });

  it('turns each tile forward one flap at a time, wrapping round, and stops on the target', () => {
    expect(step('A', 'C')).toBe('B');
    expect(step('C', 'C')).toBe('C');
    expect(step(DIAL[DIAL.length - 1], 'A')).toBe(DIAL[0]);
    let ch = 'Z';
    for (let i = 0; i < DIAL.length && ch !== 'B'; i++) ch = step(ch, 'B');
    expect(ch).toBe('B');
  });

  it('marks the hieroglyph words as malware, and only those', () => {
    expect(isMalware('𓆣𓆙𓂀𓆑𓋹𓆟𓆈')).toBe(true);
    expect(isMalware('PAYLOAD')).toBe(false);
    expect(WORDS.filter(isMalware)).toHaveLength(3);
  });
});
