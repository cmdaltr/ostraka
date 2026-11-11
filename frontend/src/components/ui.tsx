import { useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError } from '../api/client';

export function Notice({ kind = 'info', children }: { kind?: 'info' | 'error' | 'ok'; children: ReactNode }) {
  return (
    <div className={`notice notice-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  );
}

export function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Ostraka isn’t answering. Check your connection and try again.';
}

// Shows a value once, with a copy button that falls back to selecting the text
export function CopyBox({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async (event: React.MouseEvent<HTMLButtonElement>) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      const code = event.currentTarget.parentElement?.querySelector('code');
      if (code) window.getSelection()?.selectAllChildren(code);
    }
  };
  return (
    <div className="copy-box">
      <code aria-label={label}>{value}</code>
      <button className="btn btn-quiet" type="button" onClick={copy}>
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}

export function BackupCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  return (
    <div className="panel">
      <header>
        <h2>Save your backup codes</h2>
        <p>
          Each code signs you in once if you lose your authenticator. Store them somewhere safe, such as a password
          manager. Ostraka won’t show them again.
        </p>
      </header>
      <ol className="codes" aria-label="Backup codes">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ol>
      <CopyBox value={codes.join('\n')} label="All backup codes" />
      <label className="check">
        <input id="codes-saved" type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        <span>I’ve saved these codes</span>
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="button" disabled={!saved} onClick={onDone}>
          Continue
        </button>
      </div>
    </div>
  );
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
