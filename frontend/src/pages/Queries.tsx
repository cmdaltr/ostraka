import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { IncidentSummary } from '../api/incidents';
import { cell, getConnections, getKinds, searchQueries } from '../api/siem';
import type { SiemQuery, SiemRun } from '../api/siem';
import type { Me } from '../api/types';
import { Notice, formatDate, messageOf } from '../components/ui';
import './queries.css';

const windows = [
  ['1h', 'Last hour', 1],
  ['24h', 'Last 24 hours', 24],
  ['7d', 'Last 7 days', 24 * 7],
  ['30d', 'Last 30 days', 24 * 30],
] as const;

// A term shown where it matches in a query's text
function Marked({ text, term }: { text: string; term: string }) {
  if (!term) return <>{text}</>;
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  const find = term.toLowerCase();
  let from = 0;
  for (let at = lower.indexOf(find); at !== -1; at = lower.indexOf(find, from)) {
    parts.push(text.slice(from, at), <mark key={at}>{text.slice(at, at + term.length)}</mark>);
    from = at + term.length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}

function useDebounced(value: string, ms = 250) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/** Queries run, as a list: each one's text, where, when, by whom, for which record and how many rows */
export function QueryList({
  queries,
  term = '',
  onUse,
}: {
  queries: SiemQuery[];
  term?: string;
  onUse?: (query: SiemQuery) => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  async function copy(q: SiemQuery) {
    try {
      await navigator.clipboard.writeText(q.text);
      setCopied(q.id);
    } catch {
      setCopied(null);
    }
  }
  return (
    <ol className="query-list" aria-label="Queries">
      {queries.map((q) => (
        <li key={q.id} className={q.outcome === 'failed' ? 'query-failed' : ''}>
          <pre className="query-text">
            <Marked text={q.text} term={term} />
          </pre>
          <p className="hint">
            <span className="mono">{q.ref}</span> · {q.connection_name} ({q.language}) · {q.run_by?.name ?? 'someone'},{' '}
            {formatDate(q.run_at)}
            {q.incident && (
              <>
                {' '}
                · for{' '}
                <Link className="ref-link mono" to={`/incidents/${q.incident.ref}`}>
                  {q.incident.ref}
                </Link>
              </>
            )}{' '}
            ·{' '}
            {q.outcome === 'failed'
              ? 'failed'
              : `${q.row_count} row${q.row_count === 1 ? '' : 's'}${q.truncated ? ' (more were there)' : ''}`}
          </p>
          <div className="actions">
            {onUse && (
              <button className="link-button" type="button" onClick={() => onUse(q)}>
                Use this query
              </button>
            )}
            <button className="link-button" type="button" onClick={() => copy(q)}>
              {copied === q.id ? 'Copied' : 'Copy'}
            </button>
          </div>
        </li>
      ))}
    </ol>
  );
}

function Results({ run }: { run: SiemRun }) {
  const { query } = run;
  return (
    <section className="panel" aria-label="Results">
      <header>
        <h2>
          Results of <span className="mono">{query.ref}</span>
        </h2>
        <p className="hint">
          {run.problem
            ? `${query.connection_name} refused it. The query is kept, as failed.`
            : `${run.rows.length} row${run.rows.length === 1 ? '' : 's'} from ${query.connection_name}${
                run.truncated ? `; more were there than the ${run.rows.length} shown` : ''
              }. Only the query is kept: these rows go when you leave this page.`}
        </p>
      </header>
      {run.problem ? (
        <Notice kind="error">{run.problem}</Notice>
      ) : run.rows.length === 0 ? (
        <p className="hint">Nothing matched.</p>
      ) : (
        <div className="results-table">
          <table>
            <thead>
              <tr>
                {run.columns.map((c) => (
                  <th key={c} scope="col">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {run.rows.map((row, i) => (
                <tr key={i}>
                  {run.columns.map((c) => (
                    <td key={c}>{cell(row[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const pad = (n: number) => String(n).padStart(2, '0');
const local = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

// Run a query against a connected SIEM, and search every query run
export function Queries({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const canRun = me.role !== 'observer';
  const connections = useQuery({ queryKey: ['siem-connections'], queryFn: getConnections });
  const kinds = useQuery({ queryKey: ['siem-kinds'], queryFn: getKinds, staleTime: Infinity });
  const incidents = useQuery({
    queryKey: ['incidents'],
    queryFn: () => api<IncidentSummary[]>('/api/incidents'),
    enabled: canRun,
  });

  const usable = (connections.data ?? []).filter((c) => c.enabled);
  const [connectionId, setConnectionId] = useState('');
  const [text, setText] = useState('');
  const [incidentId, setIncidentId] = useState(params.get('incident') ?? '');
  const [span, setSpan] = useState<string>('24h');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<SiemRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [term, setTerm] = useState('');
  const [kind, setKind] = useState('');
  const [mine, setMine] = useState(false);
  const searched = useDebounced(term.trim());
  const past = useQuery({
    queryKey: ['siem-queries', searched, kind, mine],
    queryFn: () => searchQueries({ q: searched, kind, mine }),
  });

  const chosen = usable.find((c) => c.id === connectionId) ?? usable[0];
  const about = kinds.data?.find((k) => k.kind === chosen?.kind);

  function window(): { start: string | null; end: string | null } {
    if (span === 'custom') {
      return { start: start ? new Date(start).toISOString() : null, end: end ? new Date(end).toISOString() : null };
    }
    const hours = windows.find(([k]) => k === span)?.[2] ?? 24;
    return { start: new Date(Date.now() - hours * 3600_000).toISOString(), end: null };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!chosen) return;
    setRunning(true);
    setError(null);
    setRun(null);
    try {
      const out = await api<SiemRun>('/api/siem/queries', {
        method: 'POST',
        json: { connection_id: chosen.id, text, incident_id: incidentId || null, ...window() },
      });
      setRun(out);
      queryClient.invalidateQueries({ queryKey: ['siem-queries'] });
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setRunning(false);
    }
  }

  function use(q: SiemQuery) {
    const same = usable.find((c) => c.id === q.connection_id) ?? usable.find((c) => c.kind === q.kind);
    if (same) setConnectionId(same.id);
    setText(q.text);
    if (q.incident) setIncidentId(q.incident.id);
    if (q.window_start) {
      setSpan('custom');
      setStart(local(new Date(q.window_start)));
      setEnd(q.window_end ? local(new Date(q.window_end)) : '');
    }
    document.getElementById('query-text')?.focus();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Queries</h1>
          <p className="hint">
            Search your SIEMs from here, and find any query anyone has run. Only the queries are kept, never what they
            returned.
          </p>
        </div>
      </div>

      {canRun &&
        (connections.data && usable.length === 0 ? (
          <Notice kind="info">
            No SIEM is connected yet.{' '}
            {me.role === 'admin' ? (
              <Link to="/config?tab=siems">Connect one</Link>
            ) : (
              'An admin connects them under Config.'
            )}
          </Notice>
        ) : (
          chosen && (
            <form className="panel query-form" onSubmit={submit} aria-label="Run a query">
              <div className="form-row">
                <label className="field">
                  <span>SIEM</span>
                  <select id="query-siem" value={chosen.id} onChange={(e) => setConnectionId(e.target.value)}>
                    {usable.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} ({c.language})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>For</span>
                  <select id="query-for" value={incidentId} onChange={(e) => setIncidentId(e.target.value)}>
                    <option value="">No record</option>
                    {(incidents.data ?? [])
                      .filter((i) => i.status === 'open' || i.id === incidentId)
                      .map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.ref} {i.codename} · {i.title}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="field">
                  <span>Window</span>
                  <select id="query-window" value={span} onChange={(e) => setSpan(e.target.value)}>
                    {windows.map(([k, label]) => (
                      <option key={k} value={k}>
                        {label}
                      </option>
                    ))}
                    <option value="custom">From and to…</option>
                  </select>
                </label>
              </div>
              {span === 'custom' && (
                <div className="form-row">
                  <label className="field">
                    <span>From</span>
                    <input id="query-start" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
                  </label>
                  <label className="field">
                    <span>To</span>
                    <input id="query-end" type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
                  </label>
                </div>
              )}
              <label className="field">
                <span>Query ({chosen.language})</span>
                <textarea
                  id="query-text"
                  className="input mono"
                  rows={6}
                  required
                  spellCheck={false}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </label>
              {about && <p className="hint">Search window: {about.window}</p>}
              {error && <Notice kind="error">{error}</Notice>}
              <div className="actions">
                <button className="btn btn-primary" type="submit" disabled={running || !text.trim()}>
                  {running ? 'Running…' : 'Run'}
                </button>
              </div>
            </form>
          )
        ))}

      {run && <Results run={run} />}

      <section className="panel" aria-label="Past queries">
        <header>
          <h2>Past queries</h2>
          <p className="hint">Every query run for a record you can see, and those run for none, newest first.</p>
        </header>
        <div className="form-row">
          <label className="field">
            <span>Containing</span>
            <input
              id="query-search"
              className="input mono"
              type="search"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="rclone.exe, 203.0.113.7, EventCode=4624…"
            />
          </label>
          <label className="field">
            <span>Language</span>
            <select id="query-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Any</option>
              {kinds.data?.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.language} ({k.name})
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
          <span>Only mine</span>
        </label>
        {past.error && <Notice kind="error">{messageOf(past.error)}</Notice>}
        {past.data &&
          (past.data.length ? (
            <QueryList queries={past.data} term={searched} onUse={canRun && usable.length ? use : undefined} />
          ) : (
            <p className="hint">{searched ? `No query contains “${searched}”.` : 'Nobody has run a query yet.'}</p>
          ))}
      </section>
    </>
  );
}
