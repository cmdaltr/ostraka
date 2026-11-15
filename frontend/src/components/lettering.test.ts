import { describe, expect, it } from 'vitest';
import { CELL, NAME, WORDS, holdMs, layout, nextWord, scribble, showingMs, writable, writeMs } from './lettering';

describe('the sign-in inscription', () => {
  it('can write the name and every word', () => {
    for (const word of [NAME, ...WORDS]) expect(writable(word), word).toBe(true);
  });

  it('gives every letter a cell of its own, and writes the strokes one after another', () => {
    const { width, strokes } = layout('IOC');
    expect(width).toBe(3 * CELL);
    expect(strokes.map((s) => s.x)).toEqual([CELL / 2, CELL * 1.5, CELL * 2.5]);
    expect(strokes.map((s) => s.delay)).toEqual([0, 70, 140]);
    expect(layout('C2 X').strokes.map((s) => s.x)).toContain(CELL * 3.5); // a space keeps its cell
  });

  it('alternates the name with words, never the same word twice running', () => {
    expect(nextWord('PHISHING', null)).toBe(NAME);
    for (let i = 0; i < 50; i++) {
      const word = nextWord(NAME, 'BEACON');
      expect(WORDS).toContain(word);
      expect(word).not.toBe('BEACON');
    }
  });

  it('shows the name for most of the time, even against the longest word', () => {
    const longest = [...WORDS].sort((a, b) => writeMs(b) - writeMs(a))[0];
    expect(showingMs(NAME)).toBeGreaterThan(showingMs(longest));
    expect(holdMs(NAME)).toBeGreaterThan(holdMs(longest));
  });

  it('scribbles across the whole word', () => {
    const d = scribble(600, () => 0.5);
    const xs = [...d.matchAll(/[ML](-?[\d.]+) /g)].map((m) => Number(m[1]));
    expect(Math.min(...xs)).toBeLessThan(20);
    expect(Math.max(...xs)).toBeGreaterThan(580);
  });
});
