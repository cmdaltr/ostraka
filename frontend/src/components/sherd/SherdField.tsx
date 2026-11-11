import { useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { FRAGMENTS, clipPath, random } from './geometry';
import './sherd.css';

const COUNT = 7;

/** Loose fragments lying about behind the app, placed afresh on each page: faint, still or drifting
 * slowly, and never in the way (they sit under the panels and ignore the pointer). */
export function SherdField() {
  const { pathname } = useLocation();
  const sherds = useMemo(() => {
    const next = random(Math.floor(Math.random() * 2 ** 31));
    // Spread them round the edges and corners, where there's space, rather than under the text
    return Array.from({ length: COUNT }, (_, i) => {
      const edge = i % 4;
      const along = 5 + next() * 90;
      const inset = 2 + next() * 10;
      const [left, top] = [
        [inset, along],
        [100 - inset - 8, along],
        [along, inset],
        [along, 100 - inset - 10],
      ][edge];
      return {
        fragment: Math.floor(next() * FRAGMENTS.length),
        left,
        top,
        size: 9 + next() * 9,
        turn: Math.round((next() - 0.5) * 120),
        drift: 14 + next() * 16,
        delay: -next() * 20,
      };
    });
  }, [pathname]);
  return (
    <div className="sherd-field" aria-hidden="true">
      {sherds.map((s, i) => (
        <span
          key={`${pathname}-${i}`}
          className="sherd"
          style={{
            left: `${s.left}%`,
            top: `${s.top}%`,
            width: `${s.size}rem`,
            rotate: `${s.turn}deg`,
            animationDuration: `${s.drift}s`,
            animationDelay: `${s.delay}s`,
          }}
        >
          <span className="ostrakon-piece" style={{ clipPath: clipPath(FRAGMENTS[s.fragment]) }} />
        </span>
      ))}
    </div>
  );
}
