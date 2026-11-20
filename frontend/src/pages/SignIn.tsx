import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api/client';
import type { Grant } from '../api/types';
import { brand } from '../brand';
import { startWith, useSession } from '../auth/session';
import { BrandMark } from '../components/BrandMark';
import { SiteLinks } from '../components/SiteLinks';
import { Progress } from '../components/sherd/Progress';
import { Notice, messageOf } from '../components/ui';
import { ForgotPassword } from './ForgotPassword';

export function SignIn() {
  const session = useSession();
  const [ticket, setTicket] = useState<string | null>(null);
  const [useBackup, setUseBackup] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
  const reason = session.status === 'signed-out' ? session.reason : undefined;

  async function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const step = await api<{ ticket: string }>('/api/auth/login', {
        method: 'POST',
        json: { email: form.get('email'), password: form.get('password') },
      });
      setTicket(step.ticket);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = String(new FormData(event.currentTarget).get('code') ?? '');
    setBusy(true);
    setError(null);
    try {
      await startWith(await api<Grant>('/api/auth/mfa', { method: 'POST', json: { ticket, code } }));
    } catch (e) {
      setError(messageOf(e));
      if (e instanceof Error && e.message.includes('took too long')) setTicket(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <BrandMark />
        {reason && !ticket && !forgot && <Notice>{reason}</Notice>}
        {error && !forgot && <Notice kind="error">{error}</Notice>}

        {forgot ? (
          <ForgotPassword onBack={() => setForgot(false)} />
        ) : !ticket ? (
          <form className="panel" onSubmit={submitPassword}>
            {/* the form speaks for itself; the heading is for screen readers */}
            <h1 className="visually-hidden">Sign in to {brand.name}</h1>
            <label className="field">
              <span>Email</span>
              <input id="email" name="email" type="email" autoComplete="username" required autoFocus />
            </label>
            <label className="field">
              <span>Password</span>
              <input id="password" name="password" type="password" autoComplete="current-password" required />
            </label>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              <Progress phase={busy ? 'busy' : 'idle'} label="Checking your password" busy="Checking…">
                Continue
              </Progress>
            </button>
            <button className="link-button" type="button" onClick={() => setForgot(true)}>
              Forgot password?
            </button>
            <p className="hint auth-note">
              Ostraka accounts are by invitation. Ask your team lead or an admin.
            </p>
          </form>
        ) : (
          <form className="panel" onSubmit={submitCode}>
            <h1>{useBackup ? 'Enter a backup code' : 'Enter your authenticator code'}</h1>
            <label className="field">
              <span>{useBackup ? 'Backup code' : '6-digit code'}</span>
              {useBackup ? (
                <input key="backup" id="code" name="code" autoComplete="off" placeholder="ABCDE-FGHJK" required autoFocus />
              ) : (
                <input
                  key="totp"
                  id="code"
                  name="code"
                  className="code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9 ]{6,7}"
                  maxLength={7}
                  required
                  autoFocus
                />
              )}
            </label>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              <Progress phase={busy ? 'busy' : 'idle'} label="Checking your code" busy="Signing in…">
                Sign in
              </Progress>
            </button>
            <button className="link-button" type="button" onClick={() => setUseBackup(!useBackup)}>
              {useBackup ? 'Use my authenticator instead' : 'I don’t have my authenticator'}
            </button>
          </form>
        )}
        <SiteLinks />
        <p className="hint" style={{ textAlign: 'center' }}>
          {brand.trademark}
        </p>
      </div>
    </main>
  );
}
