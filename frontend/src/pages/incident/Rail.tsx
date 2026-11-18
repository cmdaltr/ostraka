import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

const KEY = 'ostraka.rail';

export type Flap = {
  key: string;
  label: string;
  icon: ReactNode;
  count?: number;
  // told whether it's open, so a flap can wait to load until someone looks
  content: (open: boolean) => ReactNode;
};

function remembered(keys: string[]): string | null {
  try {
    const kept = localStorage.getItem(KEY);
    return kept && keys.includes(kept) ? kept : null;
  } catch {
    return null;
  }
}

// Flaps on the page's right edge, each opening a drawer, so the section has the width to itself until you
// want to talk or ask. One drawer at a time; which one is open is remembered per browser. Every drawer stays
// mounted while shut, so a half-written comment or question survives.
export function Rail({ flaps }: { flaps: Flap[] }) {
  const [open, setOpen] = useState<string | null>(() => remembered(flaps.map((f) => f.key)));
  useEffect(() => {
    try {
      localStorage.setItem(KEY, open ?? '');
    } catch {
      // a private window: it just won't be remembered
    }
  }, [open]);
  // Scrolling over the flaps or a drawer moves the drawer or nothing, never the page behind it. A wheel the
  // drawer can still use passes to it; one it can't (at an end, or over a flap) stops here.
  const rail = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = rail.current;
    if (!el) return;
    const hold = (e: WheelEvent) => {
      const drawer = (e.target as Element).closest('.rail-drawer');
      const room =
        drawer &&
        (e.deltaY < 0 ? drawer.scrollTop > 0 : drawer.scrollTop + drawer.clientHeight < drawer.scrollHeight - 1);
      if (!room) e.preventDefault();
    };
    el.addEventListener('wheel', hold, { passive: false });
    return () => el.removeEventListener('wheel', hold);
  }, []);
  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(null);
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [open]);
  return (
    <div ref={rail} className={`rail${open ? ' is-open' : ''}`}>
      <div className="rail-flaps">
        {flaps.map((f) => (
          <button
            key={f.key}
            className={`rail-flap rail-flap-${f.key}`}
            type="button"
            aria-expanded={open === f.key}
            aria-controls={`${f.key}-drawer`}
            onClick={() => setOpen(open === f.key ? null : f.key)}
          >
            {f.icon}
            <span>{f.label}</span>
            {!!f.count && <span className="rail-count num">{f.count}</span>}
          </button>
        ))}
      </div>
      {flaps.map((f) => (
        <aside key={f.key} id={`${f.key}-drawer`} className="rail-drawer" aria-label={f.label} hidden={open !== f.key}>
          {f.content(open === f.key)}
        </aside>
      ))}
    </div>
  );
}
