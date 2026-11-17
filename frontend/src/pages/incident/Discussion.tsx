import { useEffect, useState } from 'react';
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
  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [open]);
  return (
    <div className={`discussion${open ? ' is-open' : ''}`}>
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
