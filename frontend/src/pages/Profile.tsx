import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { availabilityNames } from '../api/types';
import type { ApiToken, Availability, Enrolment, Me, SecurityEvent, SignedInBrowser } from '../api/types';
import { loadMe, updateMe } from '../auth/session';
import type { MePatch } from '../auth/session';
import { brand } from '../brand';
import { BackupCodes, CopyBox, Notice, formatDate, messageOf } from '../components/ui';
import { disablePush, enablePush, pushState } from '../live/push';
import type { PushState } from '../live/push';
import { modes, palettes } from '../theme/theme';
import type { Mode, Palette } from '../theme/theme';

const paletteNames: Record<Palette, string> = {
  cobalt: 'Cobalt (default)',
  ostrakon: 'Ostrakon',
  ledger: 'Ledger',
};
const modeNames: Record<Mode, string> = { system: 'Match my device', light: 'Light', dark: 'Dark' };

function Result({ ok, error }: { ok?: string | null; error?: string | null }) {
  if (error) return <Notice kind="error">{error}</Notice>;
  if (ok) return <Notice kind="ok">{ok}</Notice>;
  return null;
}

// Every IANA zone the browser knows, with the person's own first in case the browser lacks it
function timeZones(current: string): string[] {
  const known = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  return known.includes(current) ? known : [current, ...known];
}

function AboutYou({ me }: { me: Me }) {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await updateMe({
        name: String(form.get('name')),
        job_title: String(form.get('job_title')),
        phone: String(form.get('phone')),
        timezone: String(form.get('timezone')),
      });
      setStatus({ ok: 'Saved.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }
  const setAvailability = (availability: Availability) =>
    updateMe({ availability }).catch((e) => setStatus({ error: messageOf(e) }));
  return (
    <>
      <form className="panel" onSubmit={save}>
        <header>
          <h2>About you</h2>
          <p>
            {me.email}. Your role and team are set by an admin. Everyone in Ostraka sees your job title; observers
            don’t see your phone number.
          </p>
        </header>
        <div className="form-row">
          <label className="field">
            <span>Name</span>
            <input id="profile-name" name="name" defaultValue={me.name} required maxLength={120} />
          </label>
          <label className="field">
            <span>Job title</span>
            <input id="profile-title" name="job_title" defaultValue={me.job_title} maxLength={120} placeholder="e.g. Senior analyst" />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span>Out-of-hours phone</span>
            <input
              id="profile-phone"
              name="phone"
              type="tel"
              defaultValue={me.phone ?? ''}
              maxLength={40}
              pattern="\+?[0-9 ()\-.]{6,40}"
              placeholder="+44 7700 900123"
            />
          </label>
          <label className="field">
            <span>Time zone</span>
            <select id="profile-timezone" name="timezone" defaultValue={me.timezone}>
              {timeZones(me.timezone).map((zone) => (
                <option key={zone} value={zone}>
                  {zone.replaceAll('_', ' ')}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Save
          </button>
        </div>
        <Result {...status} />
      </form>
      <section className="panel">
        <header>
          <h2>Availability</h2>
          <p>Shown beside your name in the directory, so a lead can see who to give work to.</p>
        </header>
        <div className="actions" role="radiogroup" aria-label="Availability">
          {(Object.keys(availabilityNames) as Availability[]).map((a) => (
            <label className="check" key={a}>
              <input
                type="radio"
                name="availability"
                value={a}
                checked={me.availability === a}
                onChange={() => setAvailability(a)}
              />
              <span>{availabilityNames[a]}</span>
            </label>
          ))}
        </div>
      </section>
    </>
  );
}

function Appearance({ me }: { me: Me }) {
  const [error, setError] = useState<string | null>(null);
  const change = (changes: Partial<Pick<Me, 'theme_palette' | 'theme_mode' | 'show_raw_iocs'>>) =>
    updateMe(changes).catch((e) => setError(messageOf(e)));
  return (
    <section className="panel">
      <header>
        <h2>Appearance</h2>
        <p>Applies wherever you sign in. Severity and TLP colours stay the same in every theme.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Theme</span>
          <select id="theme-palette" value={me.theme_palette} onChange={(e) => change({ theme_palette: e.target.value as Palette })}>
            {palettes.map((p) => (
              <option key={p} value={p}>
                {paletteNames[p]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Light or dark</span>
          <select id="theme-mode" value={me.theme_mode} onChange={(e) => change({ theme_mode: e.target.value as Mode })}>
            {modes.map((m) => (
              <option key={m} value={m}>
                {modeNames[m]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {me.role === 'observer' && (
        <label className="check">
          <input
            id="show-raw-iocs"
            type="checkbox"
            checked={me.show_raw_iocs}
            onChange={(e) => change({ show_raw_iocs: e.target.checked })}
          />
          <span>
            Show raw IOCs
            <br />
            <span className="hint">
              Off by default: briefings describe indicators without listing them. Turn this on to see the IP
              addresses, domains and hashes themselves.
            </span>
          </span>
        </label>
      )}
      {error && <Notice kind="error">{error}</Notice>}
    </section>
  );
}

const pushWords: Record<PushState, string> = {
  unsupported: 'This browser can’t receive push notifications.',
  denied: 'This browser has blocked notifications from Ostraka. Allow them in the site settings, then come back.',
  off: 'This browser isn’t receiving push notifications.',
  on: 'This browser receives push notifications.',
};

function Notifications({ me }: { me: Me }) {
  const [error, setError] = useState<string | null>(null);
  const [push, setPush] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    pushState().then(setPush, () => setPush('unsupported'));
  }, []);
  const change = (changes: MePatch) => updateMe(changes).catch((e) => setError(messageOf(e)));
  const toggle = async () => {
    setBusy(true);
    try {
      setPush(push === 'on' ? await disablePush() : await enablePush());
      setError(null);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel">
      <header>
        <h2>Notifications</h2>
        <p>
          Everything appears under the bell. Email and push carry a short version: indicators are masked, and for
          TLP:RED and TLP:AMBER+STRICT incidents only the reference is sent.
        </p>
      </header>
      <label className="check">
        <input id="notify-email" type="checkbox" checked={me.notify_email} onChange={(e) => change({ notify_email: e.target.checked })} />
        <span>
          Email me about tasks given to me, overdue work, nudges, mentions and handovers
          <br />
          <span className="hint">Sent to {me.email}.</span>
        </span>
      </label>
      <label className="check">
        <input id="notify-push" type="checkbox" checked={me.notify_push} onChange={(e) => change({ notify_push: e.target.checked })} />
        <span>Send push notifications to browsers I’ve turned them on in</span>
      </label>
      {push && (
        <div className="actions">
          <span className="hint">{pushWords[push]}</span>
          {(push === 'on' || push === 'off') && (
            <button className="btn btn-quiet" type="button" disabled={busy} onClick={toggle}>
              {push === 'on' ? 'Turn off in this browser' : 'Turn on in this browser'}
            </button>
          )}
        </div>
      )}
      {error && <Notice kind="error">{error}</Notice>}
    </section>
  );
}

// Every kind a person may keep to the bell; the new-device alert has its own switch under Security
const kindNames: Record<string, string> = {
  assigned: 'A task is given to me',
  team_task: 'A task waits in my team’s queue',
  due_soon: 'A task of mine is due soon',
  overdue: 'A task of mine is overdue',
  escalated: 'Someone else’s overdue task is escalated to me',
  nudge: 'Someone nudges me',
  mention: 'Someone mentions me',
  reply: 'Someone replies to my comment',
  commander: 'I’m made incident commander',
  unblocked: 'A task I hold stops waiting',
};

function Kinds({ me }: { me: Me }) {
  const [error, setError] = useState<string | null>(null);
  const toggle = (kind: string, send: boolean) =>
    updateMe({
      muted_kinds: send ? me.muted_kinds.filter((k) => k !== kind) : [...me.muted_kinds, kind],
    }).catch((e) => setError(messageOf(e)));
  return (
    <section className="panel">
      <header>
        <h2>What reaches you outside Ostraka</h2>
        <p>Untick something to keep it under the bell only.</p>
      </header>
      {Object.entries(kindNames).map(([kind, words]) => (
        <label className="check" key={kind}>
          <input
            id={`send-${kind}`}
            type="checkbox"
            checked={!me.muted_kinds.includes(kind)}
            onChange={(e) => toggle(kind, e.target.checked)}
          />
          <span>{words}</span>
        </label>
      ))}
      {error && <Notice kind="error">{error}</Notice>}
    </section>
  );
}

function QuietHours({ me }: { me: Me }) {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await updateMe({ quiet_from: String(form.get('from')), quiet_until: String(form.get('until')) });
      setStatus({ ok: 'Saved.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }
  async function clear() {
    try {
      await updateMe({ quiet_from: '', quiet_until: '' });
      setStatus({ ok: 'Quiet hours are off.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }
  return (
    <form className="panel" onSubmit={save} key={`${me.quiet_from}-${me.quiet_until}`}>
      <header>
        <h2>Quiet hours</h2>
        <p>
          No email or push between these times, read in your time zone ({me.timezone.replaceAll('_', ' ')}). Work on a
          critical incident still comes through, and so does a sign-in from a new browser.
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>From</span>
          <input id="quiet-from" name="from" type="time" defaultValue={me.quiet_from ?? ''} required />
        </label>
        <label className="field">
          <span>Until</span>
          <input id="quiet-until" name="until" type="time" defaultValue={me.quiet_until ?? ''} required />
        </label>
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Save
          </button>
          {me.quiet_from && (
            <button className="btn btn-quiet" type="button" onClick={clear}>
              Turn off
            </button>
          )}
        </div>
      </div>
      <Result {...status} />
    </form>
  );
}

function Password() {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    if (form.get('new_password') !== form.get('confirm')) {
      setStatus({ error: 'The two new passwords don’t match.' });
      return;
    }
    try {
      await api('/api/users/me/password', {
        method: 'POST',
        json: { current_password: form.get('current_password'), new_password: form.get('new_password') },
      });
      formEl.reset();
      setStatus({ ok: 'Password changed. Every other browser you were signed in on has been signed out.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }
  return (
    <form className="panel" onSubmit={save}>
      <header>
        <h2>Password</h2>
        <p>Changing it signs you out everywhere else.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Current password</span>
          <input id="current-password" name="current_password" type="password" autoComplete="current-password" required />
        </label>
        <label className="field">
          <span>New password</span>
          <input id="new-password" name="new_password" type="password" autoComplete="new-password" minLength={12} required />
        </label>
        <label className="field">
          <span>New password again</span>
          <input id="confirm-password" name="confirm" type="password" autoComplete="new-password" minLength={12} required />
        </label>
      </div>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Change password
        </button>
      </div>
      <Result {...status} />
    </form>
  );
}

function Backup({ me }: { me: Me }) {
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function regenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const result = await api<{ backup_codes: string[] }>('/api/users/me/backup-codes', {
        method: 'POST',
        json: { code: new FormData(event.currentTarget).get('code') },
      });
      setError(null);
      setCodes(result.backup_codes);
    } catch (e) {
      setError(messageOf(e));
    }
  }
  if (codes) return <BackupCodes codes={codes} onDone={() => { setCodes(null); loadMe(); }} />;
  return (
    <form className="panel" onSubmit={regenerate}>
      <header>
        <h2>Backup codes</h2>
        <p>
          {me.backup_codes_left} of 10 left. A new set replaces every old code; confirm with your authenticator.
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Authenticator code</span>
          <input id="backup-totp" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required />
        </label>
        <div className="actions">
          <button className="btn" type="submit">
            Make new codes
          </button>
        </div>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
    </form>
  );
}

function Authenticator() {
  const [enrol, setEnrol] = useState<Enrolment | null>(null);
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      setEnrol(
        await api<Enrolment>('/api/users/me/authenticator', {
          method: 'POST',
          json: { password: form.get('password'), code: form.get('code') },
        }),
      );
      setStatus({});
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api('/api/users/me/authenticator/confirm', {
        method: 'POST',
        json: { code: new FormData(event.currentTarget).get('code') },
      });
      setEnrol(null);
      setStatus({ ok: 'Your new authenticator is in use. Every other browser has been signed out.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }
  if (enrol)
    return (
      <form className="panel" onSubmit={confirm}>
        <header>
          <h2>New authenticator</h2>
          <p>Scan this with the new app, then enter the code it shows. Until you do, the old one keeps working.</p>
        </header>
        <img className="qr" src={enrol.qr_svg} alt="QR code for your new authenticator" />
        <details>
          <summary className="hint">Can’t scan it?</summary>
          <CopyBox value={enrol.secret} label="Authenticator key" />
        </details>
        <div className="form-row">
          <label className="field">
            <span>Code from the new authenticator</span>
            <input id="new-totp" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required />
          </label>
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Use this authenticator
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setEnrol(null)}>
              Cancel
            </button>
          </div>
        </div>
        <Result {...status} />
      </form>
    );
  return (
    <form className="panel" onSubmit={start}>
      <header>
        <h2>Authenticator</h2>
        <p>Moving to a new phone? Confirm it’s you with your password and a code from the current authenticator.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Password</span>
          <input id="mfa-password" name="password" type="password" autoComplete="current-password" required />
        </label>
        <label className="field">
          <span>Current code</span>
          <input id="mfa-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required />
        </label>
        <div className="actions">
          <button className="btn" type="submit">
            Change authenticator
          </button>
        </div>
      </div>
      <Result {...status} />
    </form>
  );
}

function Browsers() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const browsers = useQuery({
    queryKey: ['my-sessions'],
    queryFn: () => api<SignedInBrowser[]>('/api/users/me/sessions'),
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['my-sessions'] });
    queryClient.invalidateQueries({ queryKey: ['security-log'] });
  };
  const end = (path: string, method: 'DELETE' | 'POST') =>
    api(path, { method })
      .then(() => setError(null))
      .catch((e) => setError(messageOf(e)))
      .finally(refresh);
  const others = (browsers.data ?? []).filter((b) => !b.current);
  return (
    <section className="panel">
      <header>
        <h2>Signed in</h2>
        <p>Every browser signed in to your account. Signing one out ends it at its next request.</p>
      </header>
      {browsers.data && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Browser</th>
                <th>IP address</th>
                <th>Signed in</th>
                <th>Last active</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {browsers.data.map((b) => (
                <tr key={b.family}>
                  <td>
                    {b.device} {b.current && <span className="chip chip-accent">This browser</span>}
                  </td>
                  <td className="mono">{b.ip || '—'}</td>
                  <td className="num">{formatDate(b.signed_in_at)}</td>
                  <td className="num">{formatDate(b.last_active_at)}</td>
                  <td>
                    {!b.current && (
                      <button
                        className="link-button"
                        type="button"
                        aria-label={`Sign out ${b.device}`}
                        onClick={() => end(`/api/users/me/sessions/${b.family}`, 'DELETE')}
                      >
                        Sign out
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {others.length > 1 && (
        <div className="actions">
          <button className="btn" type="button" onClick={() => end('/api/users/me/sessions/end-others', 'POST')}>
            Sign out everywhere else
          </button>
        </div>
      )}
      {(error || browsers.error) && <Notice kind="error">{error ?? messageOf(browsers.error)}</Notice>}
    </section>
  );
}

const eventNames: Record<string, string> = {
  login: 'Signed in',
  login_failed: 'Sign-in failed',
  logout: 'Signed out',
  account_locked: 'Account locked after failed sign-ins',
  password_changed: 'Password changed',
  mfa_changed: 'Authenticator changed',
  backup_code_used: 'Signed in with a backup code',
  backup_codes_replaced: 'New backup codes made',
  session_ended: 'A browser was signed out',
  sessions_ended: 'Every other browser was signed out',
  api_token_created: 'API token created',
  api_token_revoked: 'API token revoked',
  account_changed: 'Account changed',
  access_reset_issued: 'Access reset sent',
  invited: 'Invited',
  push_subscribed: 'Push turned on in a browser',
  admin_bootstrapped: 'Made the first admin',
};

function describeEvent(event: SecurityEvent): string {
  const name = eventNames[event.action] ?? event.action.replaceAll('_', ' ');
  return event.detail.new_device ? `${name} from a new browser` : name;
}

function SecurityLog() {
  const log = useQuery({
    queryKey: ['security-log'],
    queryFn: () => api<SecurityEvent[]>('/api/users/me/security-log'),
  });
  return (
    <section className="panel">
      <header>
        <h2>Security log</h2>
        <p>The last hundred security events on your account, including changes an admin made to it.</p>
      </header>
      {log.data && log.data.length > 0 && (
        <div className="table-wrap">
          <table className="table" aria-label="Security log">
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>By</th>
                <th>IP address</th>
              </tr>
            </thead>
            <tbody>
              {log.data.map((e) => (
                <tr key={e.id}>
                  <td className="num">{formatDate(e.at)}</td>
                  <td>{describeEvent(e)}</td>
                  <td>{e.by ?? 'You'}</td>
                  <td className="mono">{e.ip || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {log.error && <Notice kind="error">{messageOf(log.error)}</Notice>}
    </section>
  );
}

function NewDeviceAlerts({ me }: { me: Me }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="panel">
      <header>
        <h2>New-browser alerts</h2>
      </header>
      <label className="check">
        <input
          id="new-device-alerts"
          type="checkbox"
          checked={me.new_device_alerts}
          onChange={(e) => updateMe({ new_device_alerts: e.target.checked }).catch((err) => setError(messageOf(err)))}
        />
        <span>
          Email me when my account signs in from a browser it hasn’t used before
          <br />
          <span className="hint">Sent even in quiet hours: it may be the only sign someone else has your password.</span>
        </span>
      </label>
      {error && <Notice kind="error">{error}</Notice>}
    </section>
  );
}

function Tokens() {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => api<ApiToken[]>('/api/tokens').then(setTokens).catch((e) => setError(messageOf(e)));
  useEffect(() => {
    load();
  }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    try {
      const result = await api<ApiToken & { token: string }>('/api/tokens', {
        method: 'POST',
        json: { name: form.get('name'), scope: form.get('scope'), expires_in_days: Number(form.get('days')) },
      });
      setCreated(result.token);
      formEl.reset();
      load();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  async function revoke(id: string) {
    await api(`/api/tokens/${id}`, { method: 'DELETE' }).catch((e) => setError(messageOf(e)));
    load();
  }

  return (
    <section className="panel">
      <header>
        <h2>API tokens</h2>
        <p>For scripts and the MCP server. A read-only token can look but never change anything.</p>
      </header>
      {created && (
        <Notice kind="ok">
          <p style={{ margin: '0 0 8px' }}>Copy this token now; Ostraka only keeps a hash of it.</p>
          <CopyBox value={created} label="New API token" />
          <p style={{ margin: '12px 0 8px' }}>To use it from Claude Code, or any MCP client that speaks HTTP:</p>
          <CopyBox
            value={`claude mcp add --transport http ${brand.name.toLowerCase()} ${window.location.origin}/mcp --header "Authorization: Bearer ${created}"`}
            label="MCP command"
          />
        </Notice>
      )}
      <form className="form-row" onSubmit={create}>
        <label className="field">
          <span>Name</span>
          <input id="token-name" name="name" placeholder="e.g. laptop MCP" required maxLength={80} />
        </label>
        <label className="field">
          <span>Access</span>
          <select id="token-scope" name="scope" defaultValue="mcp">
            <option value="mcp">Read-only</option>
            <option value="full">Full</option>
          </select>
        </label>
        <label className="field">
          <span>Expires after</span>
          <select id="token-days" name="days" defaultValue="90">
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
          </select>
        </label>
        <div className="actions">
          <button className="btn" type="submit">
            Create token
          </button>
        </div>
      </form>
      {tokens.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Token</th>
                <th>Access</th>
                <th>Last used</th>
                <th>Expires</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}</td>
                  <td className="mono">{t.prefix}…</td>
                  <td>{t.scope === 'mcp' ? 'Read-only' : 'Full'}</td>
                  <td className="num">{formatDate(t.last_used_at)}</td>
                  <td className="num">{formatDate(t.expires_at)}</td>
                  <td>
                    <button className="link-button" type="button" onClick={() => revoke(t.id)}>
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error && <Notice kind="error">{error}</Notice>}
    </section>
  );
}

const tabs = [
  { key: 'profile', label: 'Profile' },
  { key: 'security', label: 'Security' },
  { key: 'tokens', label: 'API tokens' },
  { key: 'notifications', label: 'Notifications' },
  { key: 'appearance', label: 'Appearance' },
] as const;
type Tab = (typeof tabs)[number]['key'];

export function Profile({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const tab: Tab = tabs.find((t) => t.key === params.get('tab'))?.key ?? 'profile';
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{me.name}</h1>
          <p className="hint">
            {[me.job_title, me.team?.name].filter(Boolean).join(' · ') || me.email}
          </p>
        </div>
      </div>
      <nav className="tabs" aria-label="Profile sections">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            aria-current={tab === t.key ? 'page' : undefined}
            onClick={() => setParams(t.key === 'profile' ? {} : { tab: t.key })}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === 'profile' && <AboutYou me={me} />}
      {tab === 'security' && (
        <>
          <Browsers />
          <Password />
          <Authenticator />
          <Backup me={me} />
          <NewDeviceAlerts me={me} />
          <SecurityLog />
        </>
      )}
      {tab === 'tokens' && <Tokens />}
      {tab === 'notifications' && (
        <>
          <Notifications me={me} />
          <Kinds me={me} />
          <QuietHours me={me} />
        </>
      )}
      {tab === 'appearance' && <Appearance me={me} />}
    </>
  );
}
