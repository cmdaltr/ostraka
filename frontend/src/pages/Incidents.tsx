import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { keys } from '../api/incidents';
import { defaultParts, suggested } from '../api/work';
import type { Playbook } from '../api/work';

import type { Incident, IncidentSummary } from '../api/incidents';
import type { Me, Team } from '../api/types';
import { huntPhases, phases, phasesOf, severities, titleMax, tlpLevels } from '../brand';
import type { Phase, RecordKind, Severity, Tlp } from '../brand';
import { PhasePill, PhaseStrip, SeverityPill, TlpPill } from '../components/Pills';
import { PillSelect } from '../components/PillSelect';
import { CodenameField } from '../components/CodenameField';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, formatDate, messageOf } from '../components/ui';
import { splitTags } from './incident/shared';
import './incident/incident.css';

// How many tasks a playbook starts a record with: the checks in its parts without an only if
const startsWith = (p: Playbook) => p.checks.filter((c) => defaultParts(p).has(c.part)).length;

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
          codename: form.get('codename') || null,
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
            ? 'You lead the hunt. It moves from hypothesis to report, and can be promoted to an incident if it finds something.'
            : 'You become its incident commander. Hand the role over from the incident once someone takes it.'}
        </p>
      </header>
      <label className="field">
        <span>Title</span>
        <input
          id="declare-title"
          name="title"
          required
          minLength={3}
          maxLength={titleMax}
          placeholder={hunt ? 'Scheduled-task persistence' : 'Finance ransomware'}
          aria-describedby="declare-title-hint"
          autoFocus
        />
        <small id="declare-title-hint" className="hint">
          A few words, as people will say it. The detail goes in the {hunt ? 'hypothesis' : 'summary'}.
        </small>
      </label>
      <CodenameField id="declare-codename" />
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
                    {p.name} <span className="hint">({startsWith(p)} tasks)</span>
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
  // Declaring and starting a hunt live in the header, on every page: they open the form here
  const [params, setParams] = useSearchParams();
  const asked = params.get('declare');
  const declaring: RecordKind | null = asked === 'incident' || asked === 'hunt' ? asked : null;
  const stopDeclaring = () => setParams((p) => (p.delete('declare'), p), { replace: true });
  const list = useQuery(listQuery(status, kind));
  // Each filter is any of what's ticked; nothing ticked is anything
  const [severity, setSeverity] = useState<Severity[]>([]);
  const [tlp, setTlp] = useState<Tlp[]>([]);
  const [phase, setPhase] = useState<Phase[]>([]);
  const among = <T,>(chosen: T[], v: T) => chosen.length === 0 || chosen.includes(v);
  const shown = list.data?.filter((i) => among(severity, i.severity) && among(tlp, i.tlp) && among(phase, i.phase));
  const filtered = severity.length + tlp.length + phase.length > 0;
  const cap = (word: string) => word[0].toUpperCase() + word.slice(1);
  const noun = kind === 'hunt' ? 'hunts' : kind === 'incident' ? 'incidents' : 'incidents or hunts';

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
        </div>
      </div>

      <div className="list-toolbar" role="group" aria-label="Filter">
        <div className="segmented" role="group" aria-label="Kind">
          {(
            [
              ['all', 'All'],
              ['incident', 'Incidents'],
              ['hunt', 'Hunts'],
            ] as const
          ).map(([k, name]) => (
            <button
              key={k}
              type="button"
              aria-pressed={kind === k}
              onClick={() => {
                setKind(k);
                // a hunt's phases aren't an incident's
                if (k !== 'all') setPhase(phase.filter((p) => (phasesOf(k) as readonly string[]).includes(p)));
              }}
            >
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
        <PillSelect
          id="filter-severity"
          label="Severity"
          any="Any severity"
          value={severity}
          onChange={setSeverity}
          options={severities.map((v) => ({ value: v, name: cap(v), look: <SeverityPill level={v} /> }))}
        />
        <PillSelect
          id="filter-tlp"
          label="TLP"
          any="Any TLP"
          value={tlp}
          onChange={setTlp}
          options={tlpLevels.map((v) => ({ value: v, name: `TLP:${v.toUpperCase()}`, look: <TlpPill level={v} /> }))}
        />
        <PillSelect
          id="filter-phase"
          label="Phase"
          any="Any phase"
          value={phase}
          onChange={setPhase}
          options={[
            ...(kind !== 'hunt' ? phases.map((v) => ({ value: v as Phase, group: 'Incidents' })) : []),
            ...(kind !== 'incident' ? huntPhases.map((v) => ({ value: v as Phase, group: 'Hunts' })) : []),
          ].map((o) => ({ ...o, name: cap(o.value), look: <PhasePill phase={o.value} /> }))}
        />
        {filtered && (
          <button
            className="btn btn-quiet"
            type="button"
            onClick={() => {
              setSeverity([]);
              setTlp([]);
              setPhase([]);
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {declaring && me.role !== 'observer' && <DeclareForm key={declaring} me={me} kind={declaring} onClose={stopDeclaring} />}

      {list.error && <Notice kind="error">{messageOf(list.error)}</Notice>}
      {/* Keyed by the filter, so one that isn't cached yet assembles afresh */}
      <Loading key={`${status}-${kind}`} ready={!list.isPending} label={`Loading ${status} ${noun}`}>
        {shown && shown.length === 0 && (
          <section className="panel">
            <header>
              <h2>{filtered ? `No ${status} ${noun} match` : `No ${status} ${noun}`}</h2>
              <p>
                {filtered
                  ? 'Nothing matches those filters. Clear them to see the rest.'
                  : status === 'open'
                    ? 'Nothing your teams are involved in is open right now.'
                    : `Closed ${noun} your teams worked on appear here.`}
              </p>
            </header>
          </section>
        )}
        {shown && shown.length > 0 && (
          <ul className="incident-list" aria-label={`${status} ${noun}`}>
            {shown.map((i) => (
              <li key={i.id}>
                <Link to={`/incidents/${i.ref}`} className={`incident-card sev-edge-${i.severity} kind-${i.kind}`}>
                  <div className="incident-card-head">
                    <span className="mono ref">{i.ref}</span>
                    {i.kind === 'hunt' && <span className="chip chip-hunt">Hunt</span>}
                    <SeverityPill level={i.severity} />
                    <TlpPill level={i.tlp} />
                    <span className="hint">updated {formatDate(i.updated_at)}</span>
                  </div>
                  <h2>
                    {i.codename} <span className="incident-card-title">{i.title}</span>
                  </h2>
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
