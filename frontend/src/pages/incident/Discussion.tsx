import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

const KEY = 'ostraka.discussion';

function remembered(): boolean {
  try {
    return localStorage.getItem(KEY) === 'open';
  } catch {
    return false;
  }
}

// The discussion as a drawer behind a tab on the page's right edge, so the section has the width to itself
// until you want to talk. Open or shut is remembered per browser; the thread stays mounted while shut, so
// a half-written comment survives.
export function Discussion({ count, children }: { count: number; children: ReactNode }) {
  const [open, setOpen] = useState(remembered);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, open ? 'open' : 'shut');
    } catch {
      // a private window: it just won't be remembered
    }
  }, [open]);
  // Scrolling over the tab or the drawer moves the thread or nothing, never the page behind it. A wheel the
  // drawer can still use passes to it; one it can't (at an end, or over the tab) stops here.
  const rail = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = rail.current;
    if (!el) return;
    const hold = (e: WheelEvent) => {
      const drawer = (e.target as Element).closest('.discussion-drawer');
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
    const close = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [open]);
  return (
    <div ref={rail} className={`discussion${open ? ' is-open' : ''}`}>
      <button
        className="discussion-tab"
        type="button"
        aria-expanded={open}
        aria-controls="discussion-drawer"
        onClick={() => setOpen(!open)}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path
            d="M4 5h16v11H9l-5 4z"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinejoin="round"
          />
        </svg>
        <span>Discussion</span>
        {count > 0 && <span className="discussion-count num">{count}</span>}
      </button>
      <aside id="discussion-drawer" className="discussion-drawer" aria-label="Discussion" hidden={!open}>
        {children}
      </aside>
    </div>
  );
}
