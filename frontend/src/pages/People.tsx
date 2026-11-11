import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { editionNames, getLicence, seatsText } from '../api/installation';
import { availabilityNames, roleNames } from '../api/types';
import type { Invitation, InvitationCreated, Me, Person, Role, Team } from '../api/types';
import { CopyBox, Notice, formatDate, messageOf } from '../components/ui';

const allRoles: Role[] = ['responder', 'observer', 'team_lead', 'admin'];
const leadRoles: Role[] = ['responder', 'observer'];

function LinkToSend({ created, onClose }: { created: InvitationCreated; onClose: () => void }) {
  return (
    <Notice kind="ok">
      <p style={{ margin: '0 0 8px' }}>
        {created.is_reset ? 'Access reset for ' : 'Invitation for '}
        <strong>{created.email}</strong>. Send them this link; it works once and expires{' '}
        {formatDate(created.expires_at)}. Ostraka won’t show it again.
      </p>
      <CopyBox value={created.link} label="Invitation link" />
      <div className="actions" style={{ marginTop: 8 }}>
        <button className="link-button" type="button" onClick={onClose}>
          Done
        </button>
      </div>
    </Notice>
  );
}

function InviteForm({ me, teams, onInvited }: { me: Me; teams: Team[]; onInvited: (c: InvitationCreated) => void }) {
  const isAdmin = me.role === 'admin';
  const [role, setRole] = useState<Role>('responder');
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const team = isAdmin ? String(form.get('team') || '') : me.team?.id;
    try {
      const created = await api<InvitationCreated>('/api/invitations', {
        method: 'POST',
        json: { email: form.get('email'), role, team_id: team || null },
      });
      setError(null);
      formEl.reset();
      setRole('responder');
      onInvited(created);
    } catch (e) {
      setError(messageOf(e));
    }
  }

  const licence = useQuery({ queryKey: ['licence'], queryFn: getLicence });
  const l = licence.data;
  return (
    <form className="panel" onSubmit={submit}>
      <header>
        <h2>Invite someone</h2>
        {l && l.places_held !== null && (
          <p className="num" aria-label="Places">
            {l.places_held} of {seatsText(l)} places taken ({editionNames[l.edition]} edition)
          </p>
        )}
        <p>
          {isAdmin
            ? 'They’ll choose a password and set up an authenticator from the link.'
            : `Team leads invite responders and observers into ${me.team?.name ?? 'their team'}.`}
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Email</span>
          <input id="invite-email" name="email" type="email" required />
        </label>
        <label className="field">
          <span>Role</span>
          <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(isAdmin ? allRoles : leadRoles).map((r) => (
              <option key={r} value={r}>
                {roleNames[r]}
              </option>
            ))}
          </select>
        </label>
        {isAdmin ? (
          <label className="field">
            <span>Team</span>
            <select id="invite-team" name="team" defaultValue="" required={role !== 'admin'}>
              <option value="">{role === 'admin' ? 'No team' : 'Choose a team'}</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className="field">
            <span>Team</span>
            <span className="chip chip-accent">{me.team?.name}</span>
          </div>
        )}
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Create invitation
          </button>
        </div>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
    </form>
  );
}

function AccountRow({
  person,
  me,
  teams,
  onChanged,
  onReset,
}: {
  person: Person;
  me: Me;
  teams: Team[];
  onChanged: () => void;
  onReset: (c: InvitationCreated) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const editable = me.role === 'admin' && person.id !== me.id;

  const change = (json: Record<string, unknown>) =>
    api(`/api/users/${person.id}`, { method: 'PATCH', json })
      .then(() => {
        setError(null);
        onChanged();
      })
      .catch((e) => setError(messageOf(e)));

  return (
    <tr className={person.is_active ? undefined : 'inactive'}>
      <td>
        <strong>{person.name}</strong>
        {person.job_title && <div>{person.job_title}</div>}
        <div className="hint">
          {person.email}
          {person.phone && (
            <>
              {' · '}
              <a href={`tel:${person.phone.replace(/[^+0-9]/g, '')}`} aria-label={`Out-of-hours phone for ${person.name}`}>
                {person.phone}
              </a>
            </>
          )}
        </div>
        {error && <div className="notice-error" style={{ fontSize: 12 }}>{error}</div>}
      </td>
      <td>
        {editable ? (
          <select
            className="input"
            aria-label={`Role for ${person.name}`}
            value={person.role}
            onChange={(e) => change({ role: e.target.value })}
          >
            {allRoles.map((r) => (
              <option key={r} value={r}>
                {roleNames[r]}
              </option>
            ))}
          </select>
        ) : (
          roleNames[person.role]
        )}
      </td>
      <td>
        {editable ? (
          <select
            className="input"
            aria-label={`Team for ${person.name}`}
            value={person.team?.id ?? ''}
            onChange={(e) => change({ team_id: e.target.value || null })}
          >
            <option value="">No team</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        ) : (
          person.team?.name ?? '—'
        )}
      </td>
      <td>
        {person.is_active ? (
          <span className={`chip availability-${person.availability}`}>{availabilityNames[person.availability]}</span>
        ) : (
          <span className="chip">Deactivated</span>
        )}
      </td>
      <td>
        {editable && (
          <div className="actions">
            <button className="link-button" type="button" onClick={() => change({ is_active: !person.is_active })}>
              {person.is_active ? 'Deactivate' : 'Reactivate'}
            </button>
            {person.is_active && (
              <button
                className="link-button"
                type="button"
                onClick={() =>
                  api<InvitationCreated>(`/api/users/${person.id}/reset-access`, { method: 'POST' })
                    .then(onReset)
                    .catch((e) => setError(messageOf(e)))
                }
              >
                Reset access
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function ChannelRow({ team, onChanged }: { team: Team; onChanged: () => void }) {
  const [url, setUrl] = useState('');
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const call = (path: string, init: RequestInit & { json?: unknown }, ok: string) =>
    api(path, init)
      .then(() => {
        setMessage({ kind: 'ok', text: ok });
        setUrl('');
        onChanged();
      })
      .catch((e) => setMessage({ kind: 'error', text: messageOf(e) }));
  const base = `/api/teams/${team.id}/channel`;
  return (
    <tr>
      <td>
        <strong>{team.name}</strong>
        {team.code && <span className="chip mono">{team.code}</span>}
        <div className="hint">{team.has_channel ? 'Connected' : 'Not connected'}</div>
      </td>
      <td>
        <form
          className="copy-box"
          onSubmit={(e) => {
            e.preventDefault();
            call(base, { method: 'PUT', json: { url } }, 'Connected. Send a test to check it reaches the channel.');
          }}
        >
          <input
            className="input"
            type="url"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={team.has_channel ? 'Paste a new URL to replace it' : 'https://….logic.azure.com/workflows/…'}
            aria-label={`Workflows webhook URL for ${team.name}`}
          />
          <button className="btn btn-quiet" type="submit">
            {team.has_channel ? 'Replace' : 'Connect'}
          </button>
        </form>
        {message && <div className={`notice-${message.kind}`} style={{ fontSize: 12, marginTop: 4 }}>{message.text}</div>}
      </td>
      <td>
        {team.has_channel && (
          <div className="actions">
            <button className="link-button" type="button" onClick={() => call(`${base}/test`, { method: 'POST' }, 'Test sent.')}>
              Send a test
            </button>
            <button
              className="link-button"
              type="button"
              onClick={() => call(base, { method: 'PUT', json: { url: '' } }, 'Disconnected.')}
            >
              Disconnect
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

// A team's Teams channel gets its queue's new tasks and their reminders, as Adaptive Cards
function TeamChannels({ me, teams, onChanged }: { me: Me; teams: Team[]; onChanged: () => void }) {
  const mine = me.role === 'admin' ? teams : teams.filter((t) => t.id === me.team?.id);
  if (!mine.length) return null;
  return (
    <section className="panel">
      <header>
        <h2>Microsoft Teams channels</h2>
        <p>
          In the channel, open Workflows and choose “Post to a channel when a webhook request is received”, then paste
          the URL it gives you. Ostraka stores it encrypted and never shows it again.
        </p>
      </header>
      <div className="table-wrap">
        <table className="table">
          <tbody>
            {mine.map((t) => (
              <ChannelRow key={t.id} team={t} onChanged={onChanged} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function People({ me }: { me: Me }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const [filter, setFilter] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const manages = me.role === 'admin' || me.role === 'team_lead';
  const queryClient = useQueryClient();

  const load = useCallback(() => {
    // Places taken move with every invitation sent or withdrawn
    queryClient.invalidateQueries({ queryKey: ['licence'] });
    Promise.all([
      api<Person[]>('/api/users').then(setPeople),
      api<Team[]>('/api/teams').then(setTeams),
      manages ? api<Invitation[]>('/api/invitations').then(setInvitations) : Promise.resolve(),
    ]).catch((e) => setError(messageOf(e)));
  }, [manages, queryClient]);

  useEffect(load, [load]);

  const shown = useMemo(() => (filter ? people.filter((p) => p.team?.id === filter) : people), [people, filter]);

  const handOut = (c: InvitationCreated) => {
    setCreated(c);
    load();
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p>Everyone with an Ostraka account, by team.</p>
        </div>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      {created && <LinkToSend created={created} onClose={() => setCreated(null)} />}
      {manages && <InviteForm me={me} teams={teams} onInvited={handOut} />}

      {manages && invitations.length > 0 && (
        <section className="panel">
          <header>
            <h2>Waiting to join</h2>
          </header>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Team</th>
                  <th>Expires</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {invitations.map((i) => (
                  <tr key={i.id}>
                    <td>
                      {i.email} {i.is_reset && <span className="chip">Access reset</span>}
                    </td>
                    <td>{roleNames[i.role]}</td>
                    <td>{i.team?.name ?? '—'}</td>
                    <td className="num">
                      {new Date(i.expires_at) < new Date() ? 'Expired' : formatDate(i.expires_at)}
                    </td>
                    <td>
                      <button
                        className="link-button"
                        type="button"
                        onClick={() => api(`/api/invitations/${i.id}`, { method: 'DELETE' }).then(load)}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {manages && <TeamChannels me={me} teams={teams} onChanged={load} />}

      <section className="panel">
        <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <h2>Directory</h2>
          <select className="input" style={{ width: 'auto' }} aria-label="Filter by team" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All teams</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </header>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Team</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <AccountRow key={p.id} person={p} me={me} teams={teams} onChanged={load} onReset={handOut} />
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
