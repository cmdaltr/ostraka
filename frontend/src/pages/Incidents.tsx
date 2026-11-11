import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { keys } from '../api/incidents';
import { suggested } from '../api/work';
import type { Playbook } from '../api/work';
import type { Incident, IncidentSummary } from '../api/incidents';
import type { Me, Team } from '../api/types';
import { severities, tlpLevels } from '../brand';
import type { RecordKind, Severity, Tlp } from '../brand';
import { PhaseStrip, SeverityPill, TlpPill } from '../components/Pills';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, formatDate, messageOf } from '../components/ui';
import { splitTags } from './incident/shared';
import './incident/incident.css';

function DeclareForm({ me, kind, onClose }: { me: Me; kind: RecordKind; onClose: () => void }) {
  const hunt = kind === 'hunt';
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams') });
  const [chosen, setChosen] = useState<string[]>(me.team ? [me.team.id] : []);
  const allPlaybooks = useQuery({ queryKey: ['playbooks'], queryFn: () => api<Playbook[]>('/api/playbooks') });
  const playbooks = { data: allPlaybooks.data?.filter((p) => p.applies_to === kind) };
  const [tags, setTags] = useState('');
  // Playbooks matching the tags are ticked for you; any you tick or untick yourself stay as you left them
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const matching = new Set(suggested(playbooks.data ?? [], splitTags(tags)));
  const applies = (id: string) => picked[id] ?? matching.has(id);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const incident = await api<Incident>('/api/incidents', {
        method: 'POST',
        json: {
          kind,
          title: form.get('title'),
          summary: form.get('summary'),
          severity: form.get('severity') as Severity,
          tlp: form.get('tlp') as Tlp,
          team_ids: chosen,
          tags: splitTags(tags),
          playbook_ids: (playbooks.data ?? []).filter((p) => p.is_active && applies(p.id)).map((p) => p.id),
        },
      });
      queryClient.invalidateQueries({ queryKey: ['incidents'] });
      navigate(`/incidents/${incident.ref}`);
    } catch (e) {
      setError(messageOf(e));
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit} aria-label={hunt ? 'Start a hunt' : 'Declare an incident'}>
      <header>
        <h2>{hunt ? 'Start a hunt' : 'Declare an incident'}</h2>
        <p>
          {hunt
            ? 'You lead the hunt. It moves from hypothesis to conclusion, and can be promoted to an incident if it finds something.'
            : 'You become its incident commander. Hand the role over from the incident once someone takes it.'}
        </p>
      </header>
      <label className="field">
        <span>Title</span>
        <input id="declare-title" name="title" required minLength={3} maxLength={200} autoFocus />
      </label>
      <div className="form-row">
        <label className="field">
          <span>Severity</span>
          <select id="declare-severity" name="severity" defaultValue={hunt ? 'low' : 'medium'}>
            {severities.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>TLP</span>
          <select id="declare-tlp" name="tlp" defaultValue="amber">
            {tlpLevels.map((t) => (
              <option key={t} value={t}>
                TLP:{t.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Tags</span>
          <input
            id="declare-tags"
            name="tags"
            placeholder="ransomware, phishing"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
        </label>
      </div>
      <label className="field">
        <span>{hunt ? 'Hypothesis' : 'What’s known so far'}</span>
        <textarea id="declare-summary" name="summary" rows={3} className="input" maxLength={20_000} />
      </label>
      <fieldset className="field team-picks">
        <span>Teams involved</span>
        <div className="actions">
          {teams.data?.map((t) => (
            <label key={t.id} className="check">
              <input
                type="checkbox"
                checked={chosen.includes(t.id)}
                onChange={(e) => setChosen(e.target.checked ? [...chosen, t.id] : chosen.filter((c) => c !== t.id))}
              />
              <span>{t.name}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {playbooks.data && playbooks.data.some((p) => p.is_active) && (
        <fieldset className="field team-picks playbook-picks">
          <span>Start with playbooks</span>
          <div className="stack">
            {playbooks.data
              .filter((p) => p.is_active)
              .map((p) => (
                <label key={p.id} className="check">
                  <input
                    type="checkbox"
                    checked={applies(p.id)}
                    onChange={(e) => setPicked({ ...picked, [p.id]: e.target.checked })}
                  />
                  <span>
                    {p.name} <span className="hint">({p.steps.length} tasks)</span>
                    {matching.has(p.id) && <span className="chip chip-accent">suggested by tag</span>}
                  </span>
                </label>
              ))}
          </div>
        </fieldset>
      )}
      {error && <Notice kind="error">{error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {hunt ? 'Start' : 'Declare'}
        </button>
        <button className="btn btn-quiet" type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** The list's query, shared with the app's start-up so the opening sherd covers the first list too */
export function listQuery(status: 'open' | 'closed', kind: RecordKind | 'all') {
  return {
    queryKey: keys.list(status, kind),
    queryFn: () => api<IncidentSummary[]>(`/api/incidents?status=${status}${kind === 'all' ? '' : `&kind=${kind}`}`),
  };
}

export function Incidents({ me }: { me: Me }) {
  const [status, setStatus] = useState<'open' | 'closed'>('open');
  const [kind, setKind] = useState<RecordKind | 'all'>('all');
  const [declaring, setDeclaring] = useState<RecordKind | null>(null);
  const list = useQuery(listQuery(status, kind));
  const noun = kind === 'hunt' ? 'hunts' : kind === 'incident' ? 'incidents' : 'incidents or hunts';
  const canDeclare = me.role !== 'observer';

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
          <p>Hello, {me.name.split(' ')[0]}. These are the incidents and hunts your teams are involved in.</p>
        </div>
        <div className="actions">
          <div className="segmented" role="group" aria-label="Kind">
            {(
              [
                ['all', 'All'],
                ['incident', 'Incidents'],
                ['hunt', 'Hunts'],
              ] as const
            ).map(([k, name]) => (
              <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>
                {name}
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label="Show">
            {(['open', 'closed'] as const).map((s) => (
              <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
                {s === 'open' ? 'Open' : 'Closed'}
              </button>
            ))}
          </div>
          {canDeclare && !declaring && (
            <>
              <button className="btn" type="button" onClick={() => setDeclaring('hunt')}>
                Start a hunt
              </button>
              <button className="btn btn-primary" type="button" onClick={() => setDeclaring('incident')}>
                Declare an incident
              </button>
            </>
          )}
        </div>
      </div>

      {declaring && <DeclareForm key={declaring} me={me} kind={declaring} onClose={() => setDeclaring(null)} />}

      {list.error && <Notice kind="error">{messageOf(list.error)}</Notice>}
      {/* Keyed by the filter, so one that isn't cached yet assembles afresh */}
      <Loading key={`${status}-${kind}`} ready={!list.isPending} label={`Loading ${status} ${noun}`}>
        {list.data && list.data.length === 0 && (
          <section className="panel">
            <header>
              <h2>{`No ${status} ${noun}`}</h2>
              <p>
                {status === 'open'
                  ? 'Nothing your teams are involved in is open right now.'
                  : `Closed ${noun} your teams worked on appear here.`}
              </p>
            </header>
          </section>
        )}
        {list.data && list.data.length > 0 && (
          <ul className="incident-list" aria-label={`${status} ${noun}`}>
            {list.data.map((i) => (
              <li key={i.id}>
                <Link to={`/incidents/${i.ref}`} className="incident-card">
                  <div className="incident-card-head">
                    <span className="mono ref">{i.ref}</span>
                    {i.kind === 'hunt' && <span className="chip chip-accent">Hunt</span>}
                    <SeverityPill level={i.severity} />
                    <TlpPill level={i.tlp} />
                    <span className="hint">updated {formatDate(i.updated_at)}</span>
                  </div>
                  <h2>{i.title}</h2>
                  <PhaseStrip current={i.phase} kind={i.kind} />
                  <div className="incident-card-foot hint">
                    <span>
                      {i.commander
                        ? `${i.kind === 'hunt' ? 'Lead' : 'Commander'}: ${i.commander.name}`
                        : i.kind === 'hunt'
                          ? 'No lead'
                          : 'No commander'}
                    </span>
                    <span>{i.teams.map((t) => t.name).join(', ')}</span>
                    {i.tags.length > 0 && (
                      <span className="tags">
                        {i.tags.map((t) => (
                          <span key={t} className="chip">
                            {t}
                          </span>
                        ))}
                      </span>
                    )}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Loading>

      {me.backup_codes_left <= 3 && (
        <section className="panel">
          <header>
            <h2>
              {me.backup_codes_left === 0
                ? 'You have no backup codes left'
                : `Only ${me.backup_codes_left} backup codes left`}
            </h2>
            <p>Make a new set in Settings, so losing your authenticator doesn’t lock you out.</p>
          </header>
        </section>
      )}
    </>
  );
}
