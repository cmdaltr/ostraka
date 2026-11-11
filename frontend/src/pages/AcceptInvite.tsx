import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { roleNames } from '../api/types';
import type { Enrolment, Grant, InvitationPreview } from '../api/types';
import { startWith } from '../auth/session';
import { BrandMark } from '../components/BrandMark';
import { Progress } from '../components/sherd/Progress';
import { BackupCodes, CopyBox, Notice, messageOf } from '../components/ui';

type Step = 'password' | 'authenticator' | 'codes';

// Joining ostraka: choose a password, enrol an authenticator, save backup codes. The account exists
// only once the authenticator has produced a working code.
export function AcceptInvite() {
  const { token = '' } = useParams();
  const navigate = useNavigate();
  const [invite, setInvite] = useState<InvitationPreview | null>(null);
  const [gone, setGone] = useState<string | null>(null);
  const [step, setStep] = useState<Step>('password');
  const [enrol, setEnrol] = useState<Enrolment | null>(null);
  const [done, setDone] = useState<(Grant & { backup_codes: string[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<InvitationPreview>(`/api/invitations/accept/${token}`)
      .then(setInvite)
      .catch((e) => setGone(messageOf(e)));
  }, [token]);

  async function choosePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get('password') !== form.get('confirm')) {
      setError('The two passwords don’t match.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setEnrol(
        await api<Enrolment>(`/api/invitations/accept/${token}/start`, {
          method: 'POST',
          json: { name: form.get('name'), password: form.get('password') },
        }),
      );
      setStep('authenticator');
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<Grant & { backup_codes: string[] }>(`/api/invitations/accept/${token}/complete`, {
        method: 'POST',
        json: { code: new FormData(event.currentTarget).get('code') },
      });
      setDone(result);
      setStep('codes');
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    if (done) await startWith(done);
    navigate('/', { replace: true });
  }

  const reset = invite?.is_reset;
  return (
    <main className="auth-page">
      <div className="auth-card" style={{ maxWidth: 480 }}>
        <BrandMark />
        {gone && <Notice kind="error">{gone}</Notice>}
        {error && <Notice kind="error">{error}</Notice>}

        {invite && step === 'password' && (
          <form className="panel" onSubmit={choosePassword}>
            <header>
              <h1 style={{ textAlign: 'left' }}>{reset ? 'Restore your access' : 'Join Ostraka'}</h1>
              <p>
                {reset
                  ? `Choose a new password and set up a new authenticator for ${invite.email}.`
                  : `You’ve been invited as ${roleNames[invite.role].toLowerCase()}${invite.team ? ` in ${invite.team}` : ''}. Signing in as ${invite.email}.`}
              </p>
            </header>
            <label className="field">
              <span>Your name</span>
              <input id="name" name="name" autoComplete="name" required maxLength={120} autoFocus />
            </label>
            <label className="field">
              <span>Password</span>
              <input id="password" name="password" type="password" autoComplete="new-password" minLength={12} required />
            </label>
            <label className="field">
              <span>Password again</span>
              <input id="confirm" name="confirm" type="password" autoComplete="new-password" minLength={12} required />
            </label>
            <p className="hint">At least 12 characters. A few unrelated words make a strong, memorable password.</p>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              <Progress phase={busy ? 'busy' : 'idle'} label="Setting your password" busy="Setting your password…">
                Continue
              </Progress>
            </button>
          </form>
        )}

        {step === 'authenticator' && enrol && (
          <form className="panel" onSubmit={confirmCode}>
            <header>
              <h1 style={{ textAlign: 'left' }}>Set up your authenticator</h1>
              <p>
                Scan this with an authenticator app (Microsoft Authenticator, 1Password, Google Authenticator), then
                enter the 6-digit code it shows. Every Ostraka sign-in needs one.
              </p>
            </header>
            <img className="qr" src={enrol.qr_svg} alt="QR code for your authenticator app" />
            <details>
              <summary className="hint">Can’t scan it? Enter this key instead</summary>
              <CopyBox value={enrol.secret} label="Authenticator key" />
            </details>
            <label className="field">
              <span>6-digit code</span>
              <input
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
            </label>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              <Progress phase={busy ? 'busy' : 'idle'} label="Checking your code" busy="Checking…">
                Confirm
              </Progress>
            </button>
          </form>
        )}

        {step === 'codes' && done && <BackupCodes codes={done.backup_codes} onDone={finish} />}
      </div>
    </main>
  );
}
