import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { getLicence } from '../../api/installation';
import type { Brand } from '../../api/installation';
import { roleLabels } from '../../api/incidents';
import type { Incident, IncidentRole } from '../../api/incidents';
import type { Me, Person, Team } from '../../api/types';
import { kindName, phasesOf, severities, titleMax, tlpLevels } from '../../brand';
import { announce } from '../../live/live';
import { initials } from '../../components/AppShell';
import { PencilIcon } from '../../components/PencilIcon';
import { PillSelect } from '../../components/PillSelect';
import { CodenameField } from '../../components/CodenameField';
import { ChannelForm, ChannelHelp, channelStatus } from '../../components/ChatChannel';
import { keys } from '../../api/incidents';
import { PhasePill, SeverityPill, TlpPill } from '../../components/Pills';
import { Notice, formatDate } from '../../components/ui';
import { ExecutiveSummary } from './ExecutiveSummary';
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
          className="input"
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
    save.run(() =>
      api(`/api/incidents/${incident.id}`, { method: 'PATCH', json: { version: incident.version, ...json } }),
    );

  const edit = (on: boolean) => {
    setEditing(on);
    announce(incident.id, 'overview', on);
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const codename = form.get('codename');
    const done = await patch({
      ...(codename && codename !== incident.codename ? { codename } : {}),
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
        {/* closing or reopening is an action on the record, so it sits with the pencil, not among the facts */}
        <span className="actions">
          {incident.can_manage && (
            <button
              className="btn btn-quiet"
              type="button"
              onClick={() => patch({ status: incident.status === 'open' ? 'closed' : 'open' })}
            >
              {incident.status === 'open' ? `Close ${kindName(incident.kind)}` : `Reopen ${kindName(incident.kind)}`}
            </button>
          )}
          {incident.can_edit && !editing && (
            <button
              className="icon-button"
              type="button"
              onClick={() => edit(true)}
              aria-label="Edit the summary"
              title="Edit"
            >
              <PencilIcon />
            </button>
          )}
        </span>
      </header>
      {editing ? (
        <form className="stack" onSubmit={submit}>
          <label className="field">
            <span>Title</span>
            <input name="title" defaultValue={incident.title} required minLength={3} maxLength={titleMax} />
          </label>
          {incident.can_manage && <CodenameField id="edit-codename" current={incident.codename} />}
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
          <p className="prose prose-wide">
            {incident.summary ? <Rich text={incident.summary} /> : <span className="hint">No summary yet.</span>}
          </p>
          <Tags tags={incident.tags} />
        </>
      )}

      <dl className="facts">
        <div>
          <dt>Severity</dt>
          <dd>
            <PillSelect
              label="Severity"
              single
              value={[incident.severity]}
              disabled={!incident.can_edit}
              onChange={([v]) => patch({ severity: v })}
              options={severities.map((v) => ({
                value: v,
                name: v[0].toUpperCase() + v.slice(1),
                look: <SeverityPill level={v} />,
              }))}
            />
          </dd>
        </div>
        <div>
          <dt>Phase</dt>
          <dd>
            <PillSelect
              label="Phase"
              single
              value={[incident.phase]}
              disabled={!incident.can_edit}
              onChange={([v]) => patch({ phase: v })}
              options={phasesOf(incident.kind).map((v) => ({
                value: v,
                name: v[0].toUpperCase() + v.slice(1),
                look: <PhasePill phase={v} />,
              }))}
            />
          </dd>
        </div>
        <div>
          <dt>TLP</dt>
          <dd>
            <PillSelect
              label="TLP"
              single
              value={[incident.tlp]}
              disabled={!incident.can_manage}
              onChange={([v]) => patch({ tlp: v })}
              options={tlpLevels.map((v) => ({
                value: v,
                name: `TLP:${v.toUpperCase()}`,
                look: <TlpPill level={v} />,
              }))}
            />
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
              <span>Commander only</span>
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
      <header className="links-head">
        <h2>Teams and people</h2>
        <p>Everyone in these teams, and anyone named here, can see and work on this {kindName(incident.kind)}.</p>
      </header>

      {/* one row of teams and one of people, each ending in the control that adds another */}
      <dl className="roster">
        <dt>Teams</dt>
        <dd>
          {incident.teams.map((t) => (
            <span key={t.id} className="chip chip-chosen">
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
              onChange={(e) =>
                save.run(() => api(`${base}/teams`, { method: 'POST', json: { team_id: e.target.value } }))
              }
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
        </dd>
        <dt>People</dt>
        <dd>
          {incident.members.map((m) => (
            <span key={m.user.id} className="chip person-chip">
              <span className="person-initials" aria-hidden="true">
                {initials(m.user.name)}
              </span>
              <span>
                {m.user.name}
                {m.user.id === me.id && <span className="hint"> (you)</span>}
              </span>
              {incident.can_manage ? (
                <select
                  className="person-role"
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
                <span className="person-role">{roleLabels[m.role]}</span>
              )}
              {incident.can_manage && m.role !== 'commander' && (
                <button
                  className="chip-x"
                  type="button"
                  aria-label={`Remove ${m.user.name}`}
                  onClick={() => save.run(() => api(`${base}/members/${m.user.id}`, { method: 'DELETE' }))}
                >
                  ×
                </button>
              )}
            </span>
          ))}
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
        </dd>
      </dl>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

// The record's own channel in Teams or Slack, for its turns: phase, severity and TLP, confirmed findings,
// stakeholder updates, handovers and breached deadlines. Its managers connect it; everyone sees whether it is
function Channel({ incident }: { incident: Incident }) {
  const queryClient = useQueryClient();
  const kind = incident.channel_kind ?? '';
  if (!incident.can_manage && !kind) return null;
  return (
    <section className="panel" aria-labelledby="incident-channel">
      <header className="links-head">
        <h2 id="incident-channel">Channel</h2>
        <p>
          {channelStatus(kind, !!kind)}. Phase, severity and TLP changes, confirmed findings, stakeholder updates, handovers
          and breached deadlines are posted there, indicators masked
          {incident.tlp === 'red' || incident.tlp === 'amber+strict' ? `; as ${incident.ref} is restricted, only its reference and code name` : ''}.
        </p>
        {incident.can_manage && <ChannelHelp />}
      </header>
      {incident.can_manage && (
        <ChannelForm
          base={`/api/incidents/${incident.id}/channel`}
          name={incident.ref}
          connected={!!kind}
          onChanged={() => queryClient.invalidateQueries({ queryKey: keys.incident(incident.id), exact: true })}
        />
      )}
    </section>
  );
}

export function Overview({ incident, me }: { incident: Incident; me: Me }) {
  return (
    <>
      <ExecutiveSummary incident={incident} />
      <Details incident={incident} />
      <Links incident={incident} />
      <People incident={incident} me={me} />
      <Channel incident={incident} />
    </>
  );
}
