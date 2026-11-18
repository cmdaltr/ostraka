import { useEffect, useState } from 'react';
import { api } from '../api/client';

// Two arrows chasing each other: ask for another
function RerollIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16m0 4v-4h-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const suggest = () => api<{ codename: string }>('/api/incidents/codename').then((r) => r.codename);

// A record's code name: two words from Ostraka's lists, offered by the server and only re-rolled here,
// so it never says what the case is. With no current name it asks for one; if that fails the server picks.
export function CodenameField({ id, current }: { id: string; current?: string }) {
  const [name, setName] = useState(current ?? '');
  const [busy, setBusy] = useState(false);

  async function another() {
    setBusy(true);
    try {
      setName(await suggest());
    } catch {
      // keep the one shown; the server picks if the field is empty
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (current) return;
    let live = true;
    suggest()
      .then((offered) => live && setName(offered))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [current]);

  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        Code name
      </label>
      <div className="codename-field">
        <input id={id} name="codename" value={name} readOnly aria-describedby={`${id}-hint`} />
        <button
          className="icon-button"
          type="button"
          onClick={another}
          disabled={busy}
          aria-label="Another code name"
          title="Another code name"
        >
          <RerollIcon />
        </button>
      </div>
      <small id={`${id}-hint`} className="hint">
        What people call it outside Ostraka. It says nothing about the case, so it’s safe on a call or in a TLP:RED
        notice.
      </small>
    </div>
  );
}
