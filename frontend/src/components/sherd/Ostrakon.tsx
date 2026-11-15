import { useEffect, useState, useSyncExternalStore } from 'react';
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

/** Work under way: the fragments drift together while it runs, close up when it's done, and the mended
 * sherd glows once. With `progress` (0 to 1) they follow it; without, they creep in and wait. With
 * `glide` (ms) they come home in one smooth movement that long; done sooner, they finish on the usual
 * curve from wherever they've got to. */
export function Assembling({
  done = false,
  progress = null,
  glide = 0,
  width = '7rem',
  label,
  inline = false,
}: {
  done?: boolean;
  progress?: number | null;
  glide?: number;
  width?: string;
  label: string;
  inline?: boolean;
}) {
  const [start] = useState(() => scatter(Math.floor(Math.random() * 2 ** 31), inline ? 0.6 : 1));
  const [elapsed, setElapsed] = useState(0);
  const [gliding, setGliding] = useState(false);
  useEffect(() => {
    if (done || progress !== null || glide) return;
    const began = performance.now();
    const tick = window.setInterval(() => setElapsed(performance.now() - began), 150);
    return () => window.clearInterval(tick);
  }, [done, progress, glide]);
  // Scattered for the first frame, then sent home: the browser eases the whole way in one transition
  useEffect(() => {
    if (!glide) return;
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => setGliding(true));
    });
    return () => cancelAnimationFrame(frame);
  }, [glide]);
  const t = done ? 1 : progress !== null ? Math.max(0, Math.min(1, progress)) * 0.95 : creep(elapsed);
  const home = glide ? done || gliding : t >= 1;
  const poses = home ? null : glide ? start : start.map((pose) => towards(pose, t));
  return (
    <span
      className={`assembling${inline ? ' assembling-inline' : ''}${glide && !done ? ' assembling-glide' : ''}`}
      role="status"
      aria-live="polite"
      style={{ '--sherd-width': width, '--sherd-glide': `${glide}ms` } as CSSProperties}
    >
      <Ostrakon poses={poses} glow={done} width={width} state={done ? 'whole' : 'assembling'} />
      <span className={inline ? 'visually-hidden' : 'assembling-label'}>{done ? `${label}: done` : label}</span>
    </span>
  );
}

/** Long enough to see it mended and the glow at its brightest (sherd.css). */
const HOLD_MS = 750;

// While the opening sherd covers the app, the first page's own loading waits beneath it without a
// sherd of its own, and the opening mends once nothing beneath is still loading: one sherd, not two
let covering = false;
let pending = 0;
const watchers = new Set<() => void>();
const watch = (watcher: () => void) => {
  watchers.add(watcher);
  return () => {
    watchers.delete(watcher);
  };
};
const pendingNow = () => pending;
function setPending(by: number) {
  pending += by;
  watchers.forEach((watcher) => watcher());
}

/** Content that has to load: the sherd assembles until `ready`, mends and glows, then the content
 * takes its place. Content that's ready at once (from the cache) shows straight away, and so does
 * content loading under the opening sherd, which stands in for this one. */
export function Loading({
  ready,
  label,
  children,
  width = '16rem',
}: {
  ready: boolean;
  label: string;
  children: ReactNode;
  width?: string;
}) {
  const [covered] = useState(() => covering);
  const [waited] = useState(!ready);
  const [shown, setShown] = useState(ready);
  useEffect(() => {
    if (!covered || ready) return;
    setPending(1);
    return () => setPending(-1);
  }, [covered, ready]);
  useEffect(() => {
    if (!ready || shown) return;
    if (!waited || covered || reducedMotion()) {
      setShown(true);
      return;
    }
    const hold = window.setTimeout(() => setShown(true), HOLD_MS);
    return () => window.clearTimeout(hold);
  }, [ready, shown, waited, covered]);
  if (shown) return <>{children}</>;
  if (covered) return null;
  return (
    <div className="loading-page">
      <Assembling done={ready} label={label} width={width} />
    </div>
  );
}

/** Opening the app: the sherd covers the window while `ready` is awaited, then while the first page
 * loads beneath it, then mends, stays for `hold` and, over the last `fade` of it, fades into the page.
 * With `duration` the pieces take that long to come home however soon everything's ready. With
 * reduced motion the page shows as soon as it's ready. */
export function Opening({
  ready,
  label,
  children,
  width,
  duration = 0,
  hold = HOLD_MS,
  fade = 0,
}: {
  ready: boolean;
  label: string;
  children: ReactNode;
  width?: string;
  duration?: number;
  hold?: number;
  fade?: number;
}) {
  const still = reducedMotion();
  // Fixed for the sherd's life, so learning who's signed in never changes how the pieces move
  const [glide] = useState(duration);
  const [arrived, setArrived] = useState(false);
  const [beneath, setBeneath] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [shown, setShown] = useState(false);
  const loadingBeneath = useSyncExternalStore(watch, pendingNow);
  // Read by the page's own Loading as it first renders, beneath this
  if (ready && !shown && !still) covering = true;
  useEffect(() => () => void (covering = false), []);
  useEffect(() => {
    if (shown) covering = false;
  }, [shown]);
  useEffect(() => {
    if (!duration || still) return;
    const timer = window.setTimeout(() => setArrived(true), duration);
    return () => window.clearTimeout(timer);
  }, [duration, still]);
  // The page has rendered beneath once, so whatever it's loading has said so
  useEffect(() => {
    if (ready) setBeneath(true);
  }, [ready]);
  const assembled = ready && (still || (beneath && loadingBeneath === 0 && (!duration || arrived)));
  useEffect(() => {
    if (!assembled || shown) return;
    if (still) {
      setShown(true);
      return;
    }
    const leave = window.setTimeout(() => setLeaving(true), Math.max(0, hold - fade));
    const timer = window.setTimeout(() => setShown(true), hold);
    return () => {
      window.clearTimeout(leave);
      window.clearTimeout(timer);
    };
  }, [assembled, shown, still, hold, fade]);
  const style = { '--loading-fade': `${fade}ms` } as CSSProperties;
  const revealed = fade && !still ? 'loading-content loading-revealed' : 'loading-content';
  // Keyed, so the page isn't mounted afresh when it's revealed, nor the sherd when the page arrives.
  // Once shown, the page stays, whatever `ready` does later
  return (
    <>
      {(ready || shown) && (
        <div key="content" className={shown ? revealed : 'loading-content loading-covered'} style={style}>
          {children}
        </div>
      )}
      {!shown && (
        <div
          key="sherd"
          className={`loading-page loading-opening${leaving && fade ? ' loading-leaving' : ''}`}
          style={style}
        >
          <Assembling done={assembled} glide={glide} label={label} width={width} />
        </div>
      )}
    </>
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
