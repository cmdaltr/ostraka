import { useEffect, useId, useMemo, useState } from 'react';
import { brand } from '../brand';
import {
  FADE_MS,
  NAME,
  SCRATCH_MS,
  holdMs,
  layout,
  nextWord,
  scribble,
  writeMs,
} from './lettering';
import './Inscription.css';

type Phase = 'held' | 'writing' | 'scratching' | 'fading';

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** The name as an inscription that is now and then scratched out in black chalk and something else from
 * the work written in its place (lettering.ts). It opens on the name, already written; with reduced
 * motion the name stays. The words are decoration: screen readers hear the name only. */
export function Inscription() {
  const [shown, setShown] = useState({ word: NAME, last: null as string | null, phase: 'held' as Phase });
  const filter = useId();

  useEffect(() => {
    if (reducedMotion()) return;
    const { word, last, phase } = shown;
    const wait = { held: holdMs(word), writing: writeMs(word), scratching: SCRATCH_MS, fading: FADE_MS };
    const after = wait[phase];
    const timer = window.setTimeout(() => {
      if (phase === 'held') setShown({ word, last, phase: 'scratching' });
      else if (phase === 'scratching') setShown({ word, last, phase: 'fading' });
      else if (phase === 'writing') setShown({ word, last, phase: 'held' });
      else {
        const next = nextWord(word, last);
        setShown({ word: next, last: word === NAME ? last : word, phase: 'writing' });
      }
    }, after);
    return () => window.clearTimeout(timer);
  }, [shown]);

  const { word, phase } = shown;
  const { width, strokes } = useMemo(() => layout(word), [word]);
  // A fresh scribble for each showing
  const scratch = useMemo(() => scribble(width), [width, word]);

  return (
    <span className="inscription" role="img" aria-label={brand.name} data-word={word} data-phase={phase}>
      <svg
        viewBox={`-6 -12 ${width + 12} 128`}
        aria-hidden="true"
        className={`inscription-word${word === NAME ? ' inscription-name' : ''}`}
      >
        <defs>
          {/* rough edges and a broken line, as chalk drags over stone */}
          <filter id={filter} x="-5%" y="-20%" width="110%" height="140%">
            <feTurbulence
              type="fractalNoise"
              baseFrequency="0.35 0.9"
              numOctaves="2"
              seed="7"
              result="grain"
            />
            <feDisplacementMap in="SourceGraphic" in2="grain" scale="6" result="rough" />
            <feComponentTransfer in="grain" result="holes">
              <feFuncA type="discrete" tableValues="1 1 1 1 0.55 1 1 1" />
            </feComponentTransfer>
            <feComposite in="rough" in2="holes" operator="in" />
          </filter>
        </defs>
        <g className="inscription-letters" key={`${word}-${shown.last}`}>
          {strokes.map((s, i) => (
            <path
              key={i}
              d={s.d}
              pathLength={1}
              transform={`translate(${s.x} 0)`}
              style={phase === 'writing' ? { animationDelay: `${s.delay}ms` } : undefined}
            />
          ))}
        </g>
        {(phase === 'scratching' || phase === 'fading') && (
          <path className="inscription-scratch" d={scratch} pathLength={1} filter={`url(#${filter})`} />
        )}
      </svg>
      {/* the name's ™, top right */}
      <sup className="inscription-tm" aria-hidden="true" data-shown={word === NAME}>
        ™
      </sup>
    </span>
  );
}
