import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../api/client';
import type { Incident } from '../../api/incidents';
import { runSummary } from '../../api/integrations';
import type { RunKind, ShikariRun } from '../../api/integrations';
import { useFinishing } from '../../components/sherd/Ostrakon';
import { Progress } from '../../components/sherd/Progress';
import { Notice, formatDate, messageOf } from '../../components/ui';
import { useSave, useSection } from './shared';

const kinds: [RunKind, string, string][] = [
  ['group', 'A threat group', 'APT29'],
  ['cve', 'A CVE', 'CVE-2024-3400'],
  ['techniques', 'ATT&CK techniques', 'T1059.001, T1003.001'],
];

function Run({ incident, run }: { incident: Incident; run: ShikariRun }) {
  const save = useSave(incident, ['shikari-runs']);
  const counted = run.counts.filter((c) => c.hits !== null || c.error || c.skipped);
  return (
    <li className="linked">
      <div>
        <strong>
          {kinds.find(([k]) => k === run.kind)?.[1]}: <span className="mono">{run.target}</span>
        </strong>{' '}
        <span className="chip">{run.status}</span>
        {run.link && (
          <a href={run.link} target="_blank" rel="noreferrer" className="hint">
            {' '}
            Open in Shikari
          </a>
        )}
      </div>
      <p className="hint">
        {runSummary(run)}
        {run.execute ? ` · queries over the last ${run.window_days} days` : ' · detections only'} · started{' '}
        {formatDate(run.started_at)}
        {run.started_by ? ` by ${run.started_by.name}` : ''}
      </p>
      {counted.length > 0 && (
        <div className="table-wrap">
          <table className="table" aria-label={`Counts for ${run.target}`}>
            <thead>
              <tr>
                <th>Detection</th>
                <th>Technique</th>
                <th>SIEM</th>
                <th>Results</th>
              </tr>
            </thead>
            <tbody>
              {counted.map((c) => (
                <tr key={`${c.file}-${c.platform}-${c.query_index}`}>
                  <td className="mono">{c.file}</td>
                  <td className="mono">{c.technique}</td>
                  <td>{c.platform}</td>
                  <td>{c.skipped ? 'no connection' : c.error ? `failed: ${c.error}` : c.hits}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="actions">
        <button
          className="link-button"
          type="button"
          onClick={() => save.run(() => api(`/api/incidents/${incident.id}/shikari/runs/${run.id}/refresh`, { method: 'POST' }))}
        >
          Refresh
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </li>
  );
}

// Shikari hunt runs for this record: detections, and how many results each query found (never the rows)
export function ShikariRuns({ incident }: { incident: Incident }) {
  const runs = useSection<ShikariRun[]>(incident, 'shikari-runs', 'shikari/runs');
  const save = useSave(incident, ['shikari-runs', 'activity']);
  const [kind, setKind] = useState<RunKind>(incident.kind === 'hunt' ? 'techniques' : 'group');
  const [starting, setStarting] = useState(false);
  const phase = useFinishing(starting);
  const [started, setStarted] = useState(false);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setStarting(true);
    const out = await save.run(() =>
      api(`/api/incidents/${incident.id}/shikari/runs`, {
        method: 'POST',
        json: {
          kind,
          target: String(data.get('target') ?? '').trim(),
          execute: data.get('execute') === 'on',
          window_days: Number(data.get('window_days') ?? 7),
        },
      }),
    );
    setStarting(false);
    setStarted(out !== undefined);
    if (out !== undefined) form.reset();
  }

  if (runs.error && !String(messageOf(runs.error)).includes('Shikari')) return null;
  const list = runs.data ?? [];
  const example = kinds.find(([k]) => k === kind)?.[2] ?? '';
  return (
    <section className="panel" aria-label="Shikari hunts">
      <header>
        <h2>Shikari hunts</h2>
        <p>
          Shikari writes the detections and, if you ask, runs their queries against its SIEM connections. Ostraka keeps
          how many results each query found, never the results.
        </p>
      </header>
      {list.length === 0 ? (
        <p className="hint">No Shikari hunt for this {incident.kind} yet.</p>
      ) : (
        <ul className="stack" aria-label="Shikari runs">
          {list.map((r) => (
            <Run key={r.id} incident={incident} run={r} />
          ))}
        </ul>
      )}
      {incident.can_edit && (
        <form className="stack" onSubmit={start} aria-label="Start a Shikari hunt">
          <div className="inline-form">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value as RunKind)} aria-label="Hunt for">
              {kinds.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
            <input className="input mono" name="target" required maxLength={500} placeholder={example} aria-label="Target" />
          </div>
          <div className="inline-form">
            <label className="check">
              <input type="checkbox" name="execute" defaultChecked />
              <span>Run the queries over the last</span>
            </label>
            <input className="input" type="number" name="window_days" min={1} max={90} defaultValue={7} aria-label="Days" style={{ width: '5em' }} />
            <span>days</span>
            <button className="btn btn-primary" type="submit" disabled={starting}>
              <Progress phase={phase} ok={started} label="Starting the hunt" busy="Starting…" done="Started">
                Start the hunt
              </Progress>
            </button>
          </div>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}
