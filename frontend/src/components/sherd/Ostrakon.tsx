import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { FRAGMENTS, HOME, centre, clipPath, creep, scatter, towards, transform } from './geometry';
import type { Pose } from './geometry';
import './sherd.css';

const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** The mark as its fragments, each at the pose given (all home: the whole sherd). */
export function Ostrakon({
  poses,
  glow = false,
  width,
  label,
  state,
}: {
  poses: readonly Pose[] | null;
  glow?: boolean;
  width?: string;
  label?: string;
  state: string;
}) {
  return (
    <span
      className={glow ? 'ostrakon ostrakon-glow' : 'ostrakon'}
      style={{ width } as CSSProperties}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      data-state={state}
    >
      {FRAGMENTS.map((fragment, i) => {
        const [cx, cy] = centre(fragment);
        return (
          <span
            key={i}
            className="ostrakon-piece"
            style={{
              clipPath: clipPath(fragment),
              transformOrigin: `${cx}% ${cy}%`,
              transform: transform(poses?.[i] ?? HOME),
            }}
          />
        );
      })}
    </span>
  );
}

/** A whole sherd that breaks apart when clicked and mends itself a moment later. */
export function DispersingOstrakon({ width, label = 'Ostraka' }: { width?: string; label?: string }) {
  const [poses, setPoses] = useState<Pose[] | null>(null);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const disperse = () => {
    if (reducedMotion()) return;
    window.clearTimeout(timer.current);
    setPoses(scatter(Math.floor(Math.random() * 2 ** 31)));
    timer.current = window.setTimeout(() => setPoses(null), 1100);
  };
  return (
    <button type="button" className="ostrakon-button" onClick={disperse} aria-label={label}>
      <Ostrakon poses={poses} width={width} state={poses ? 'scattered' : 'whole'} />
    </button>
  );
}

/** Work under way: the fragments drift together while it runs, close up when it's done, and the mended
 * sherd glows once. With `progress` (0 to 1) they follow it; without, they creep in and wait. */
export function Assembling({
  done = false,
  progress = null,
  width = '7rem',
  label,
  inline = false,
}: {
  done?: boolean;
  progress?: number | null;
  width?: string;
  label: string;
  inline?: boolean;
}) {
  const [start] = useState(() => scatter(Math.floor(Math.random() * 2 ** 31), inline ? 0.6 : 1));
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (done || progress !== null) return;
    const began = performance.now();
    const tick = window.setInterval(() => setElapsed(performance.now() - began), 150);
    return () => window.clearInterval(tick);
  }, [done, progress]);
  const t = done ? 1 : progress !== null ? Math.max(0, Math.min(1, progress)) * 0.95 : creep(elapsed);
  return (
    <span
      className={inline ? 'assembling assembling-inline' : 'assembling'}
      role="status"
      aria-live="polite"
      style={{ '--sherd-width': width } as CSSProperties}
    >
      <Ostrakon
        poses={t >= 1 ? null : start.map((pose) => towards(pose, t))}
        glow={done}
        width={width}
        state={done ? 'whole' : 'assembling'}
      />
      <span className={inline ? 'visually-hidden' : 'assembling-label'}>{done ? `${label}: done` : label}</span>
    </span>
  );
}

/** Long enough to see it mended and the glow at its brightest (sherd.css). */
const HOLD_MS = 750;

/** Content that has to load: the sherd assembles until `ready`, mends and glows, then the content
 * takes its place. Content that's ready at once (from the cache) shows straight away. */
export function Loading({ ready, label, children }: { ready: boolean; label: string; children: ReactNode }) {
  const [waited] = useState(!ready);
  const [shown, setShown] = useState(ready);
  useEffect(() => {
    if (!ready || shown) return;
    if (!waited || reducedMotion()) {
      setShown(true);
      return;
    }
    const hold = window.setTimeout(() => setShown(true), HOLD_MS);
    return () => window.clearTimeout(hold);
  }, [ready, shown, waited]);
  if (shown) return <>{children}</>;
  return (
    <div className="loading-page">
      <Assembling done={ready} label={label} />
    </div>
  );
}

/** For a button's own work: assembles while `busy`, then glows for a moment once it's finished. */
export function useFinishing(busy: boolean, ms = 700): 'idle' | 'busy' | 'finished' {
  const [phase, setPhase] = useState<'idle' | 'busy' | 'finished'>('idle');
  useEffect(() => {
    if (busy) {
      setPhase('busy');
      return;
    }
    setPhase((p) => (p === 'busy' ? 'finished' : p));
  }, [busy]);
  useEffect(() => {
    if (phase !== 'finished') return;
    const t = window.setTimeout(() => setPhase('idle'), ms);
    return () => window.clearTimeout(t);
  }, [phase, ms]);
  return phase;
}
