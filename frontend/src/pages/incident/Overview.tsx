import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { getLicence } from '../../api/installation';
import type { Brand } from '../../api/installation';
import { roleLabels } from '../../api/incidents';
import type { Incident, IncidentRole } from '../../api/incidents';
import type { Me, Person, Team } from '../../api/types';
import { kindName, phasesOf, severities, tlpLevels } from '../../brand';
import { announce } from '../../live/live';
import { Notice, formatDate } from '../../components/ui';
import { Links } from './Links';
import { Rich, Tags, splitTags, useSave } from './shared';

const roles = Object.keys(roleLabels) as IncidentRole[];

// Services: the brand this incident's reports carry, from the installation's library
function ReportBrand({ incident, onChoose }: { incident: Incident; onChoose: (id: string | null) => void }) {
  const licence = useQuery({ queryKey: ['licence'], queryFn: getLicence });
  const brands = useQuery({
    queryKey: ['brands'],
    queryFn: () => api<Brand[]>('/api/brands'),
    enabled: !!licence.data?.brand_library,
  });
  const list = brands.data ?? [];
  if (!licence.data?.brand_library || list.length < 2) return null;
  const fallback = list.find((b) => b.is_default);
  return (
    <div>
      <dt>Report brand</dt>
      <dd>
        <select
          aria-label="Report brand"
          value={incident.brand_id ?? ''}
          disabled={!incident.can_manage}
          onChange={(e) => onChoose(e.target.value || null)}
        >
          <option value="">{fallback ? `${fallback.name} (default)` : 'The default'}</option>
          {list
            .filter((b) => !b.is_default)
            .map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
        </select>
      </dd>
    </div>
  );
}

function Details({ incident }: { incident: Incident }) {
  const [editing, setEditing] = useState(false);
  const save = useSave(incident, []);
  const patch = (json: Record<string, unknown>) =>
    save.run(() => api(`/api/incidents/${incident.id}`, { method: 'PATCH', json: { version: incident.version, ...json } }));

  const edit = (on: boolean) => {
    setEditing(on);
    announce(incident.id, 'overview', on);
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await patch({
      title: form.get('title'),
      summary: form.get('summary'),
      tags: splitTags(String(form.get('tags') ?? '')),
    });
    if (done !== undefined) edit(false);
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Summary</h2>
        {incident.can_edit && !editing && (
          <button className="link-button" type="button" onClick={() => edit(true)}>
            Edit
          </button>
        )}
      </header>
      {editing ? (
        <form className="stack" onSubmit={submit}>
          <label className="field">
            <span>Title</span>
            <input name="title" defaultValue={incident.title} required minLength={3} maxLength={200} />
          </label>
          <label className="field">
            <span>Summary</span>
            <textarea className="input" name="summary" rows={6} defaultValue={incident.summary} />
          </label>
          <label className="field">
            <span>Tags</span>
            <input name="tags" defaultValue={incident.tags.join(', ')} />
          </label>
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Save
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => edit(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <p className="prose">{incident.summary ? <Rich text={incident.summary} /> : <span className="hint">No summary yet.</span>}</p>
          <Tags tags={incident.tags} />
        </>
      )}

      <dl className="facts">
        <div>
          <dt>Severity</dt>
          <dd>
            <select
              className="input"
              aria-label="Severity"
              value={incident.severity}
              disabled={!incident.can_edit}
              onChange={(e) => patch({ severity: e.target.value })}
            >
              {severities.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </dd>
        </div>
        <div>
          <dt>Phase</dt>
          <dd>
            <select
              className="input"
              aria-label="Phase"
              value={incident.phase}
              disabled={!incident.can_edit}
              onChange={(e) => patch({ phase: e.target.value })}
            >
              {phasesOf(incident.kind).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </dd>
        </div>
        <div>
          <dt>TLP</dt>
          <dd>
            <select
              className="input"
              aria-label="TLP"
              value={incident.tlp}
              disabled={!incident.can_manage}
              onChange={(e) => patch({ tlp: e.target.value })}
            >
              {tlpLevels.map((t) => (
                <option key={t} value={t}>
                  TLP:{t.toUpperCase()}
                </option>
              ))}
            </select>
          </dd>
        </div>
        <div>
          <dt>Exports</dt>
          <dd>
            <label className="check">
              <input
                type="checkbox"
                aria-label="Sensitive"
                checked={incident.sensitive}
                disabled={!incident.can_manage}
                onChange={(e) => patch({ sensitive: e.target.checked })}
              />
              <span>{incident.sensitive ? 'Sensitive: commander only' : 'Anyone who worked on it'}</span>
            </label>
          </dd>
        </div>
        <ReportBrand incident={incident} onChoose={(brand_id) => patch({ brand_id })} />
        <div>
          <dt>Declared</dt>
          <dd className="num">{formatDate(incident.declared_at)}</dd>
        </div>
        <div>
          <dt>Aware since</dt>
          <dd className="num">{formatDate(incident.aware_at)}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>
            {incident.can_manage ? (
              <button
                className="btn btn-quiet"
                type="button"
                onClick={() => patch({ status: incident.status === 'open' ? 'closed' : 'open' })}
              >
                {incident.status === 'open' ? `Close ${kindName(incident.kind)}` : `Reopen ${kindName(incident.kind)}`}
              </button>
            ) : (
              incident.status
            )}
          </dd>
        </div>
      </dl>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

function People({ incident, me }: { incident: Incident; me: Me }) {
  const save = useSave(incident, []);
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams') });
  const directory = useQuery({
    queryKey: ['directory'],
    queryFn: () => api<Person[]>('/api/users'),
    enabled: incident.can_manage,
  });
  const base = `/api/incidents/${incident.id}`;
  const involved = new Set(incident.teams.map((t) => t.id));
  const members = new Set(incident.members.map((m) => m.user.id));
  const setRole = (userId: string, role: IncidentRole) =>
    save.run(() => api(`${base}/members/${userId}`, { method: 'PUT', json: { role } }));

  return (
    <section className="panel">
      <header>
        <h2>Teams and people</h2>
        <p>
          Everyone in an involved team can see and work on this {kindName(incident.kind)}. People added by name can
          too.
        </p>
      </header>

      <div className="stack">
        <span className="label">Teams</span>
        <div className="actions">
          {incident.teams.map((t) => (
            <span key={t.id} className="chip chip-accent">
              {t.name}
              {incident.can_manage && incident.teams.length > 1 && (
                <button
                  className="chip-x"
                  type="button"
                  aria-label={`Remove ${t.name}`}
                  onClick={() => save.run(() => api(`${base}/teams/${t.id}`, { method: 'DELETE' }))}
                >
                  ×
                </button>
              )}
            </span>
          ))}
          {incident.can_manage && (
            <select
              className="input inline"
              aria-label="Involve a team"
              value=""
              onChange={(e) => save.run(() => api(`${base}/teams`, { method: 'POST', json: { team_id: e.target.value } }))}
            >
              <option value="">Involve a team…</option>
              {teams.data
                ?.filter((t) => !involved.has(t.id))
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </select>
          )}
        </div>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Person</th>
            <th>Role here</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {incident.members.map((m) => (
            <tr key={m.user.id}>
              <td>
                {m.user.name}
                {m.user.id === me.id && <span className="hint"> (you)</span>}
              </td>
              <td>
                {incident.can_manage ? (
                  <select
                    className="input"
                    aria-label={`Role for ${m.user.name}`}
                    value={m.role}
                    onChange={(e) => setRole(m.user.id, e.target.value as IncidentRole)}
                  >
                    {roles.map((r) => (
                      <option key={r} value={r}>
                        {roleLabels[r]}
                      </option>
                    ))}
                  </select>
                ) : (
                  roleLabels[m.role]
                )}
              </td>
              <td>
                {incident.can_manage && m.role !== 'commander' && (
                  <button
                    className="link-button"
                    type="button"
                    onClick={() => save.run(() => api(`${base}/members/${m.user.id}`, { method: 'DELETE' }))}
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {incident.can_manage && (
        <select
          className="input inline"
          aria-label="Add a person"
          value=""
          onChange={(e) => setRole(e.target.value, 'responder')}
        >
          <option value="">Add a person…</option>
          {directory.data
            ?.filter((p) => p.is_active && !members.has(p.id))
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.team ? ` · ${p.team.name}` : ''}
              </option>
            ))}
        </select>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

export function Overview({ incident, me }: { incident: Incident; me: Me }) {
  return (
    <>
      <Details incident={incident} />
      <Links incident={incident} />
      <People incident={incident} me={me} />
    </>
  );
}
