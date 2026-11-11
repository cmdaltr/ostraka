import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, download } from '../../api/client';
import { keys } from '../../api/incidents';
import type { Incident } from '../../api/incidents';
import type { Me } from '../../api/types';
import { severities } from '../../brand';
import { PhaseStrip, SeverityPill, TlpPill } from '../../components/Pills';
import { Assembling, Loading, useFinishing } from '../../components/sherd/Ostrakon';
import { Notice, messageOf } from '../../components/ui';
import { useLiveIncident } from '../../live/live';
import type { Present } from '../../live/live';
import { PresenceProvider } from '../../live/presence';
import { AchMatrix } from './AchMatrix';
import { Activity } from './Activity';
import { Assessments } from './Assessments';
import { Assets } from './Assets';
import { Clocks } from './Clocks';
import { Comments } from './Comments';
import { DetectionGaps } from './DetectionGaps';
import { Findings } from './Findings';
import { Handover, SinceYouLeftBanner } from './Handover';
import { IntelExports } from './IntelExports';
import { Lessons } from './Lessons';
import { Observables } from './Observables';
import { Overview } from './Overview';
import { Rowanholt } from './Rowanholt';
import { Tasks } from './Tasks';
import { Timeline } from './Timeline';
import './incident.css';

const sections = [
  ['overview', 'Overview'],
  ['tasks', 'Tasks'],
  ['clocks', 'Clocks'],
  ['observables', 'Observables'],
  ['assets', 'Assets'],
  ['findings', 'Findings'],
  ['rowanholt', 'Rowanholt'],
  ['detection-gaps', 'Detection gaps'],
  ['assessments', 'Assessments'],
  ['ach', 'ACH'],
  ['timeline', 'Timeline'],
  ['lessons', 'Lessons'],
  ['handover', 'Handover'],
  ['activity', 'Activity'],
] as const;
type Section = (typeof sections)[number][0];

function Here({ people, me }: { people: Present[]; me: Me }) {
  const others = people.filter((p) => p.user_id !== me.id);
  if (!others.length) return null;
  return (
    <ul className="presence" aria-label="Also here">
      {others.map((p) => (
        <li key={p.user_id} title={p.editing ? `${p.name} is editing` : `${p.name} is here`}>
          <span className={p.editing ? 'dot editing' : 'dot'} aria-hidden />
          {p.name}
          {p.editing && <span className="hint"> editing</span>}
        </li>
      ))}
    </ul>
  );
}

// A hunt that found something: declare an incident from it, linked back, with the hunt's teams and tags
function Promote({ hunt, onClose }: { hunt: Incident; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const incident = await api<Incident>(`/api/incidents/${hunt.id}/promote`, {
        method: 'POST',
        json: { title: form.get('title'), severity: form.get('severity'), summary: form.get('summary') },
      });
      queryClient.invalidateQueries({ queryKey: ['incidents'] });
      queryClient.invalidateQueries({ queryKey: keys.section(hunt.id, 'record-links') });
      navigate(`/incidents/${incident.ref}`);
    } catch (e) {
      setError(messageOf(e));
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit} aria-label="Promote to an incident">
      <header>
        <h2>Promote to an incident</h2>
        <p>
          The incident takes this hunt’s teams, tags and TLP, and links back to {hunt.ref}. The hunt stays open until
          you conclude it. You become the incident commander.
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Incident title</span>
          <input name="title" required minLength={3} maxLength={200} autoFocus />
        </label>
        <label className="field">
          <span>Severity</span>
          <select name="severity" defaultValue="high">
            {severities.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>What the hunt found</span>
        <textarea className="input" name="summary" rows={3} placeholder={`Found by ${hunt.ref}: ${hunt.title}.`} />
      </label>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          Declare the incident
        </button>
        <button className="btn btn-quiet" type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// The full technical report or the shorter briefing, as PDF or Word
function Export({ incident }: { incident: Incident }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const phase = useFinishing(busy);
  const get = async (variant: 'report' | 'briefing', format: 'pdf' | 'docx') => {
    setBusy(true);
    try {
      await download(`/api/incidents/${incident.id}/export?variant=${variant}&format=${format}`);
      setError(null);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="export">
      <summary className="btn">
        {phase === 'idle' ? (
          'Export'
        ) : (
          <>
            <Assembling inline width="1.6em" done={phase === 'finished'} label="Preparing the export" />{' '}
            {phase === 'finished' ? 'Ready' : 'Preparing…'}
          </>
        )}
      </summary>
      <div className="export-menu" role="menu">
        {(
          [
            ['report', 'Technical report'],
            ['briefing', 'Briefing'],
          ] as const
        ).map(([variant, name]) => (
          <div key={variant} className="export-row">
            <span>{name}</span>
            <button className="link-button" type="button" role="menuitem" disabled={busy} onClick={() => get(variant, 'pdf')}>
              PDF
            </button>
            <button className="link-button" type="button" role="menuitem" disabled={busy} onClick={() => get(variant, 'docx')}>
              Word
            </button>
          </div>
        ))}
        {incident.sensitive && <p className="hint">Sensitive: only the commander can export.</p>}
        {error && <Notice kind="error">{error}</Notice>}
        <IntelExports incident={incident} />
      </div>
    </details>
  );
}

export function IncidentPage({ me }: { me: Me }) {
  const [promoting, setPromoting] = useState(false);
  const { ref = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const section = (sections.find(([s]) => s === params.get('s'))?.[0] ?? 'overview') as Section;

  // Found by reference first, then cached and kept fresh by id like every section
  const found = useQuery({
    queryKey: ['incident-ref', ref.toUpperCase()],
    queryFn: () => api<Incident>(`/api/incidents/by-ref/${encodeURIComponent(ref)}`),
    staleTime: Infinity,
  });
  const id = found.data?.id;
  const live = useQuery({
    queryKey: keys.incident(id ?? ''),
    queryFn: () => api<Incident>(`/api/incidents/${id}`),
    enabled: !!id,
    initialData: found.data,
  });
  const people = useLiveIncident(id, queryClient);
  const incident = live.data;
  const { hash } = useLocation();

  // A #reference link names a record; sections load on demand, so wait briefly for it to appear
  useEffect(() => {
    if (!hash) return;
    let tries = 0;
    const timer = setInterval(() => {
      const el = document.getElementById(decodeURIComponent(hash.slice(1)));
      if (el || ++tries > 20) clearInterval(timer);
      if (el) {
        el.scrollIntoView({ block: 'center' });
        el.classList.add('flash');
        setTimeout(() => el.classList.remove('flash'), 1600);
      }
    }, 100);
    return () => clearInterval(timer);
  }, [hash, section]);

  if (found.error)
    return (
      <>
        <Notice kind="error">{messageOf(found.error)}</Notice>
        <Link to="/">Back to incidents and hunts</Link>
      </>
    );
  return (
    <Loading ready={!!incident} label={`Opening ${ref.toUpperCase()}`}>
      {incident && (
    <div className="incident">
      <header className="incident-head">
        <div className="incident-title">
          <div className="incident-card-head">
            <span className="mono ref">{incident.ref}</span>
            <SeverityPill level={incident.severity} />
            <TlpPill level={incident.tlp} />
            {incident.kind === 'hunt' && <span className="chip chip-accent">Hunt</span>}
            {incident.status === 'closed' && <span className="chip">Closed</span>}
            {!incident.can_edit && <span className="chip">Read only</span>}
          </div>
          <h1>{incident.title}</h1>
        </div>
        <div className="incident-head-side">
          <Here people={people} me={me} />
          {incident.can_export && <Export incident={incident} />}
          {incident.kind === 'hunt' && incident.can_edit && me.role !== 'observer' && !promoting && (
            <button className="btn" type="button" onClick={() => setPromoting(true)}>
              Promote to an incident
            </button>
          )}
        </div>
        <PhaseStrip current={incident.phase} kind={incident.kind} />
      </header>

      {promoting && <Promote hunt={incident} onClose={() => setPromoting(false)} />}

      <SinceYouLeftBanner incident={incident} />

      <nav className="tabs" aria-label="Incident sections">
        {sections.map(([key, name]) => {
          const count = (incident.counts as Record<string, number>)[key];
          return (
            <button
              key={key}
              type="button"
              aria-current={section === key ? 'page' : undefined}
              onClick={() => setParams(key === 'overview' ? {} : { s: key }, { replace: true })}
            >
              {name}
              {count !== undefined && <span className="count num">{count}</span>}
            </button>
          );
        })}
      </nav>

      <PresenceProvider value={{ incidentId: incident.id, meId: me.id, people }}>
      <div className="incident-body">
        <div className="incident-main">
          {section === 'overview' && <Overview incident={incident} me={me} />}
          {section === 'tasks' && <Tasks incident={incident} me={me} />}
          {section === 'clocks' && <Clocks incident={incident} me={me} />}
          {section === 'observables' && <Observables incident={incident} me={me} />}
          {section === 'assets' && <Assets incident={incident} />}
          {section === 'findings' && <Findings incident={incident} />}
          {section === 'rowanholt' && <Rowanholt incident={incident} />}
          {section === 'detection-gaps' && <DetectionGaps incident={incident} me={me} />}
          {section === 'assessments' && <Assessments incident={incident} />}
          {section === 'ach' && <AchMatrix incident={incident} me={me} />}
          {section === 'timeline' && <Timeline incident={incident} />}
          {section === 'lessons' && <Lessons incident={incident} me={me} />}
          {section === 'handover' && <Handover incident={incident} me={me} />}
          {section === 'activity' && <Activity incident={incident} />}
        </div>
        <aside className="incident-rail" aria-label="Discussion">
          <Comments incident={incident} me={me} />
        </aside>
      </div>
      </PresenceProvider>
    </div>
      )}
    </Loading>
  );
}
