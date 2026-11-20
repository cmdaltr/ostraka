import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { Progress } from '../components/sherd/Progress';
import { Notice, messageOf } from '../components/ui';

// Forgot password, from the sign-in page: the server emails a reset-access link, the same /invite#
// page an admin's reset opens. The answer is the same whether or not the address has an account.
// Where Ostraka can't send email, the page says to ask an admin instead.
export function ForgotPassword({ onBack }: { onBack: () => void }) {
  const available = useQuery({
    queryKey: ['forgot-available'],
    queryFn: () => api<{ available: boolean }>('/api/auth/forgot'),
  });
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const answer = await api<{ detail: string }>('/api/auth/forgot', {
        method: 'POST',
        json: { email: new FormData(event.currentTarget).get('email') },
      });
      setSent(answer.detail);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const back = (
    <button className="link-button" type="button" onClick={onBack}>
      Back to sign in
    </button>
  );
  if (available.data?.available === false)
    return (
      <div className="panel">
        <h1>Forgot your password?</h1>
        <p>Ostraka can’t send email here, so ask an administrator to reset your access. They’ll give you a link to choose a new password and set up a new authenticator.</p>
        {back}
      </div>
    );
  return (
    <form className="panel" onSubmit={ask}>
      <h1>Forgot your password?</h1>
      {error && <Notice kind="error">{error}</Notice>}
      {sent ? (
        <Notice kind="ok">{sent}</Notice>
      ) : (
        <>
          <p>
            Enter your email and Ostraka will send a link to choose a new password and set up a new authenticator. Your
            current password keeps working until the link is used.
          </p>
          <label className="field">
            <span>Email</span>
            <input id="forgot-email" name="email" type="email" autoComplete="username" required autoFocus />
          </label>
          <button className="btn btn-primary" type="submit" disabled={busy || !available.data}>
            <Progress phase={busy ? 'busy' : 'idle'} label="Asking for a link" busy="Sending…">
              Send me a link
            </Progress>
          </button>
        </>
      )}
      {back}
    </form>
  );
}
