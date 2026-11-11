import { useEffect, useState } from 'react';
import { brand } from '../brand';
import './SplitFlap.css';

// Malware, written the way it reads to anyone who hasn't reversed it: scarab (bug), cobra and
// viper (worm), the eye (spyware), fish (phish), lizard, owl and the rest
const HIEROGLYPHS = [...'𓆣𓆙𓆑𓂀𓁹𓆟𓆈𓅓𓃭𓄿𓋹𓆓'];
// The flaps on every tile, in the order they turn past. Spread into code points: the hieroglyphs
// sit outside the Basic Multilingual Plane
export const DIAL = [' ', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-', ...'ΑΓΔΘΚΛΞΟΠΡΣΤΦΨΩ', ...HIEROGLYPHS];
export const WIDTH = 7;
export const WORDS = [
  'ALERT', 'PHISH', 'TRACE', 'PIVOT', 'EXFIL', 'SCOPE', 'BEACON', 'PAYLOAD', 'IMPLANT', 'PERSIST',
  'LATERAL', 'CONTAIN', 'TOKEN', 'PROXY', 'SPOOF', 'AUDIT', 'PATCH', 'WIPER', 'LOGON', 'HUNTS',
  'SIGMA', '0DAYS', 'ΟΣΤΡΑΚΑ',
  '𓆣𓆙𓂀𓆑𓋹𓆟𓆈', '𓁹𓆟𓆓𓅓𓃭𓆣𓂀', '𓆙𓂀𓆣𓄿𓆈𓋹𓆑',
];
export const isMalware = (word: string) => [...word].some((ch) => HIEROGLYPHS.includes(ch));
export const HOLD_MS = 2800;
// The name stays up a little longer than the words, so it reads as the resting state
export const NAME_HOLD_MS = HOLD_MS + 500;
// The share of turns that land back on the name, so the board reads as the name more than anything else
const NAME_SHARE = 0.5;
const STEP_MS = 24;
// Each tile starts a couple of steps after the one to its left, as a real board ripples
const STAGGER = 2;

export function nextWord(current: string, name: string): string {
  if (Math.random() < NAME_SHARE) return name;
  const others = WORDS.filter((w) => w !== current);
  return others[Math.floor(Math.random() * others.length)];
}

// A word laid out across the board, centred, one character per tile
export function layout(word: string, width = WIDTH): string[] {
  const chars = [...word].slice(0, width);
  const left = Math.floor((width - chars.length) / 2);
  return Array.from({ length: width }, (_, i) => chars[i - left] ?? ' ');
}

// The next flap a tile shows on its way to `target`: always forward, wrapping round, as a board turns
export function step(current: string, target: string): string {
  if (current === target) return current;
  const at = DIAL.indexOf(current);
  return DIAL[(at + 1) % DIAL.length];
}

// The sign-in departures board: limestone tiles that flap through incident words and keep coming
// back to the name
export function SplitFlap() {
  const name = brand.name.toUpperCase();
  const [word, setWord] = useState(name);
  const [tiles, setTiles] = useState(() => layout(name));
  const target = layout(word);
  const settled = tiles.every((ch, i) => ch === target[i]);

  // Turn the tiles one flap at a time towards the word
  useEffect(() => {
    if (settled) return;
    let tick = 0;
    const timer = window.setInterval(() => {
      tick += 1;
      setTiles((now) => now.map((ch, i) => (tick > i * STAGGER ? step(ch, target[i]) : ch)));
    }, STEP_MS);
    return () => window.clearInterval(timer);
  }, [word, settled]);

  // Once it has settled, hold, then choose the next word
  useEffect(() => {
    if (!settled || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const timer = window.setTimeout(() => setWord((w) => nextWord(w, name)), word === name ? NAME_HOLD_MS : HOLD_MS);
    return () => window.clearTimeout(timer);
  }, [name, word, settled]);

  const named = settled && word === name;
  return (
    <span
      className={`splitflap${named ? ' splitflap-named' : ''}${settled && isMalware(word) ? ' splitflap-malware' : ''}`}
      role="img"
      aria-label={brand.name}
    >
      {tiles.map((ch, i) => (
        <span key={i} className="splitflap-tile" aria-hidden="true">
          <span key={ch} className={HIEROGLYPHS.includes(ch) ? 'splitflap-glyph' : undefined}>
            {ch === ' ' ? ' ' : ch}
          </span>
        </span>
      ))}
    </span>
  );
}
