// The sign-in inscription's lettering: Latin capitals drawn as strokes in the manner of a Greek
// inscription (even monoline strokes, every letter in a cell of its own as in stoichedon, the lunate E
// and an A with a sloping bar). The name is written, scratched out in black chalk, another word is
// written in its place, and that is scratched out for the name again. Pure, so the timings can be
// tested.

/** Each letter's cell, in the units its strokes are drawn in (cap height 100). */
export const CELL = 84;
const STROKE_MS = 70;
const NAME_HOLD_MS = 8000;
const WORD_HOLD_MS = 2400;
export const SCRATCH_MS = 650;
export const FADE_MS = 350;
export const NAME = 'OSTRAKA';

// Strokes centred on x = 0, from the cap line (y = 0) to the baseline (y = 100)
const ROUND_C = 'M26 12 A36 48 0 1 0 26 88';
const GLYPHS: Record<string, string[]> = {
  A: ['M-32 100 L0 0 L32 100', 'M-19 64 L20 54'],
  B: ['M-24 0 V100', 'M-24 0 H4 A22 24 0 0 1 4 48 H-24', 'M-24 48 H8 A24 26 0 0 1 8 100 H-24'],
  C: [ROUND_C],
  D: ['M-24 0 V100', 'M-24 0 H-4 A30 50 0 0 1 -4 100 H-24'],
  E: [ROUND_C, 'M-26 50 H14'],
  F: ['M-22 0 V100', 'M-22 0 H26', 'M-22 48 H16'],
  G: [ROUND_C, 'M6 58 H28 V86'],
  H: ['M-26 0 V100', 'M26 0 V100', 'M-26 50 H26'],
  I: ['M0 0 V100'],
  J: ['M18 0 V72 Q18 100 -4 100 Q-22 100 -24 82'],
  K: ['M-24 0 V100', 'M26 0 L-22 50 L26 100'],
  L: ['M-22 0 V100 H24'],
  M: ['M-32 100 L-24 0 L0 64 L24 0 L32 100'],
  N: ['M-26 100 V0 L26 100 V0'],
  O: ['M-36 50 A36 48 0 1 0 36 50 A36 48 0 1 0 -36 50'],
  P: ['M-24 100 V0 H4 A24 26 0 0 1 4 52 H-24'],
  Q: ['M-36 50 A36 48 0 1 0 36 50 A36 48 0 1 0 -36 50', 'M10 76 L32 104'],
  R: ['M-24 100 V0 H4 A24 26 0 0 1 4 52 H-24', 'M-2 52 L26 100'],
  S: ['M26 8 L0 0 L-24 16 L-22 34 L22 64 L24 84 L0 100 L-28 92'],
  T: ['M-30 0 H30', 'M0 0 V100'],
  U: ['M-26 0 V64 Q-26 100 0 100 Q26 100 26 64 V0'],
  V: ['M-30 0 L0 100 L30 0'],
  W: ['M-34 0 L-18 100 L0 30 L18 100 L34 0'],
  X: ['M-28 0 L28 100', 'M28 0 L-28 100'],
  Y: ['M-28 0 L0 50 L28 0', 'M0 50 V100'],
  Z: ['M-26 0 H26 L-26 100 H26'],
  '0': ['M-28 50 A28 48 0 1 0 28 50 A28 48 0 1 0 -28 50'],
  '1': ['M-10 14 L4 0 V100'],
  '2': ['M-24 22 Q-22 0 0 0 Q24 0 24 24 Q24 44 -24 100 H26'],
  '3': ['M-22 8 Q0 -6 18 10 Q30 30 -2 48 Q30 52 26 78 Q20 104 -24 92'],
  '4': ['M14 100 V0 L-28 70 H30'],
  '5': ['M24 0 H-20 L-24 46 Q0 34 18 48 Q32 64 20 88 Q4 106 -24 92'],
  '6': ['M20 4 Q-26 10 -26 64 Q-26 100 0 100 Q26 100 26 72 Q26 46 0 46 Q-20 46 -26 62'],
  '7': ['M-26 0 H26 L-6 100'],
  '8': ['M0 0 A21 24 0 1 0 0 48 A21 24 0 1 0 0 0', 'M0 48 A25 26 0 1 0 0 100 A25 26 0 1 0 0 48'],
  '9': ['M-20 96 Q26 90 26 36 Q26 0 0 0 Q-26 0 -26 28 Q-26 54 0 54 Q20 54 26 38'],
  '-': ['M-18 54 H18'],
  ' ': [],
};

/** The words written in the name's place, one at a time between showings of the name. */
export const WORDS = [
  'PHISHING',
  'RANSOMWARE',
  'BEACON',
  'PAYLOAD',
  'LATERAL MOVEMENT',
  'EXFILTRATION',
  'PERSISTENCE',
  'ZERO-DAY',
  'BACKDOOR',
  'ROOTKIT',
  'BOTNET',
  'WEBSHELL',
  'KERBEROASTING',
  'SUPPLY CHAIN',
  'INITIAL ACCESS',
  'C2',
  'TRIAGE',
  'CONTAINMENT',
  'IOC',
  'MALWARE',
];

export interface Stroke {
  d: string;
  /** Where the letter's cell is centred */
  x: number;
  /** When this stroke starts being written, in ms from the start of the word */
  delay: number;
}

/** The word's strokes, letter by letter, and the width of its cells. */
export function layout(word: string): { width: number; strokes: Stroke[] } {
  const strokes: Stroke[] = [];
  [...word].forEach((ch, i) => {
    for (const d of GLYPHS[ch] ?? []) {
      strokes.push({ d, x: CELL * i + CELL / 2, delay: strokes.length * STROKE_MS });
    }
  });
  return { width: CELL * [...word].length, strokes };
}

/** Whether every letter of the word has strokes to draw it with. */
export const writable = (word: string) => [...word].every((ch) => ch in GLYPHS);

export const writeMs = (word: string) => layout(word).strokes.length * STROKE_MS;
export const holdMs = (word: string) => (word === NAME ? NAME_HOLD_MS : WORD_HOLD_MS);

/** After a word comes the name; after the name, any word but the last one. */
export function nextWord(current: string, last: string | null, random: () => number = Math.random): string {
  if (current !== NAME) return NAME;
  const choices = WORDS.filter((w) => w !== last);
  return choices[Math.floor(random() * choices.length)];
}

/** One showing of a word, from the first stroke to the end of its fade. */
export const showingMs = (word: string) => writeMs(word) + holdMs(word) + SCRATCH_MS + FADE_MS;

/** A chalk scribble back and forth across a word `width` wide, roughly the same each time but never
 * quite: a few passes from one side to the other and a last stroke through the middle. */
export function scribble(width: number, random: () => number = Math.random): string {
  const passes = 3 + Math.floor(random() * 2);
  const jitter = (n: number) => (random() - 0.5) * n;
  const points: string[] = [`M${8 + jitter(10)} ${46 + jitter(20)}`];
  for (let i = 1; i <= passes; i++) {
    const x = i % 2 ? width - 8 + jitter(16) : 8 + jitter(16);
    const y = 22 + (56 * i) / passes + jitter(12);
    points.push(`L${x.toFixed(1)} ${y.toFixed(1)}`);
  }
  const end = (width - 4 + jitter(8)).toFixed(1);
  points.push(`M${4 + jitter(8)} ${52 + jitter(10)} L${end} ${48 + jitter(10)}`);
  return points.join(' ');
}
