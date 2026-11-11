import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Finding, Incident } from '../../api/incidents';
import { statusWords } from '../../api/integrations';
import type { CaseTables, Citation, RemoteFinding, RowanholtLink, RowanholtView, Rows } from '../../api/integrations';
import { useFinishing } from '../../components/sherd/Ostrakon';
import { Progress } from '../../components/sherd/Progress';
import { Notice, messageOf } from '../../components/ui';
import { useSave, useSection } from './shared';

const when = (iso: string) => new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });

function linkName(link: RowanholtLink) {
  return link.job_id ? `Job ${link.job_id.slice(0, 8)}${link.case_number ? ` · case ${link.case_number}` : ''}` : `Case ${link.case_number}`;
}

// Rowanholt's findings for one of a link's jobs: bring them in as leads
function RemoteFindings({ incident, link, job }: { incident: Incident; link: RowanholtLink; job: string }) {
  const base = `/api/incidents/${incident.id}/rowanholt`;
  const remote = useQuery({
    queryKey: ['incident', incident.id, 'rowanholt-findings', link.id, job],
    queryFn: () => api<RemoteFinding[]>(`${base}/links/${link.id}/findings?job_id=${encodeURIComponent(job)}`),
  });
  const save = useSave(incident, ['rowanholt', 'findings', 'timeline']);
  const [chosen, setChosen] = useState<string[]>([]);
  const [done, setDone] = useState<string | null>(null);

  async function bring() {
    const out = await save.run(() =>
      api<{ imported: string[]; skipped: number }>(`${base}/import?link_id=${link.id}`, {
        method: 'POST',
        json: { job_id: job, finding_ids: chosen },
      }),
    );
    if (out) {
      setChosen([]);
      setDone(out.imported.length ? `Brought in as leads: ${out.imported.join(', ')}.` : 'Those are here already.');
      remote.refetch();
    }
  }

  if (remote.error) return <Notice kind="error">{messageOf(remote.error)}</Notice>;
  if (!remote.data) return <p className="hint">Asking Rowanholt…</p>;
  if (!remote.data.length) return <p className="hint">Rowanholt has no findings in this job yet.</p>;
  return (
    <div className="stack" aria-label="Rowanholt findings">
      <p className="hint">
        Whatever Rowanholt calls them, they arrive here as leads: confirming one in Ostraka takes a verification.
      </p>
      <ul className="deliveries">
        {remote.data.map((f) => (
          <li key={f.id}>
            <label className="check">
              <input
                type="checkbox"
                disabled={!!f.imported_as || !incident.can_edit}
                checked={chosen.includes(f.id)}
                onChange={(e) => setChosen(e.target.checked ? [...chosen, f.id] : chosen.filter((c) => c !== f.id))}
              />
              <span>
                {f.title} <span className="chip">{f.status}</span> {f.technique && <code>{f.technique}</code>}{' '}
                <span className="hint mono">{f.cite}</span>
                {f.intact === false && <span className="hint"> · the row has changed since</span>}
                {f.imported_as && <span className="hint"> · here as {f.imported_as}</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {save.error && <Notice kind="error">{save.error}</Notice>}
      {done && <Notice kind="ok">{done}</Notice>}
      {incident.can_edit && (
        <div className="actions">
          <button className="btn" type="button" disabled={!chosen.length} onClick={bring}>
            Bring in as leads
          </button>
        </div>
      )}
    </div>
  );
}

// Choose a row from a linked job and cite it in a finding
function CiteRow({ incident, link, job }: { incident: Incident; link: RowanholtLink; job: string }) {
  const base = `/api/incidents/${incident.id}/rowanholt/links/${link.id}`;
  const tables = useQuery({
    queryKey: ['incident', incident.id, 'rowanholt-case', link.id, job],
    queryFn: () => api<CaseTables>(`${base}/case?job_id=${encodeURIComponent(job)}`),
  });
  const findings = useSection<Finding[]>(incident, 'findings');
  const [table, setTable] = useState<{ host: string; path: string } | null>(null);
  const [rows, setRows] = useState<Rows | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [row, setRow] = useState<string | null>(null);
  const save = useSave(incident, ['rowanholt', 'findings', 'timeline']);
  const [done, setDone] = useState<string | null>(null);

  async function read(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const [host, path] = String(form.get('table')).split('\u0000');
    const q = String(form.get('q') ?? '');
    setTable({ host, path });
    setRow(null);
    try {
      const params = new URLSearchParams({ host, path, q, job_id: job });
      setRows(await api<Rows>(`${base}/rows?${params}`));
      setError(null);
    } catch (e) {
      setError(messageOf(e));
    }
  }

  async function cite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!table || !row) return;
    const form = new FormData(event.currentTarget);
    const finding = String(form.get('finding'));
    const out = await save.run(() =>
      api<Citation>(`/api/incidents/${incident.id}/findings/${finding}/rowanholt`, {
        method: 'POST',
        json: {
          link_id: link.id,
          job_id: job,
          host: table.host,
          artefact: table.path,
          row_ref: row,
          verify: form.get('verify') === 'on',
        },
      }),
    );
    if (out) setDone(`${out.finding_ref} cites ${out.cite}.`);
  }

  if (tables.error) return <Notice kind="error">{messageOf(tables.error)}</Notice>;
  if (!tables.data) return <p className="hint">Asking Rowanholt…</p>;
  return (
    <div className="stack" aria-label="Cite a Rowanholt row">
      <form className="inline-form" onSubmit={read}>
        <select className="input" name="table" aria-label="Table">
          {tables.data.hosts.flatMap((h) =>
            h.files.map((f) => (
              <option key={`${h.host}/${f}`} value={`${h.host}\u0000${f}`}>
                {h.host}: {f}
              </option>
            )),
          )}
        </select>
        <input className="input" name="q" placeholder="Filter rows" aria-label="Filter rows" maxLength={200} />
        <button className="btn" type="submit">
          Read rows
        </button>
      </form>
      {error && <Notice kind="error">{error}</Notice>}
      {rows && (
        <div className="table-wrap">
          <table className="table" aria-label="Rowanholt rows">
            <thead>
              <tr>
                <th>Row</th>
                {rows.columns.slice(0, 6).map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.rows.map((r) => (
                <tr key={r.ref} aria-selected={row === r.ref}>
                  <td>
                    <label className="check">
                      <input type="radio" name="row" checked={row === r.ref} onChange={() => setRow(r.ref)} />
                      <span className="mono">{r.ref}</span>
                    </label>
                  </td>
                  {rows.columns.slice(0, 6).map((c) => (
                    <td key={c} className="mono">
                      {r.cells[c]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">
            {rows.total}
            {rows.exact ? '' : '+'} rows
          </p>
        </div>
      )}
      {row && incident.can_edit && (
        <form className="inline-form" onSubmit={cite} aria-label="Cite this row">
          <select className="input" name="finding" aria-label="In the finding" required>
            {(findings.data ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.ref}: {f.title}
              </option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" name="verify" />
            <span>It verifies the finding (I checked the row)</span>
          </label>
          <button className="btn btn-primary" type="submit" disabled={!findings.data?.length}>
            Cite row {row}
          </button>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      {done && <Notice kind="ok">{done}</Notice>}
    </div>
  );
}

function LinkPanel({ incident, link, onUnlink }: { incident: Incident; link: RowanholtLink; onUnlink: () => void }) {
  const jobs = link.job_id ? [link.job_id] : link.jobs.map((j) => j.id);
  const [job, setJob] = useState(jobs[0] ?? '');
  const [open, setOpen] = useState<'findings' | 'cite' | null>(null);
  return (
    <li className="linked" aria-label={linkName(link)}>
      <div>
        <a href={link.url} target="_blank" rel="noreferrer" className="mono ref-link">
          {linkName(link)}
        </a>{' '}
        <span className="chip">{statusWords(link.status, link.progress)}</span>
        {link.checked_at && <span className="hint"> as of {when(link.checked_at)}</span>}
      </div>
      {!link.job_id && link.jobs.length > 0 && (
        <ul className="hint">
          {link.jobs.map((j) => (
            <li key={j.id}>
              <a href={j.url} target="_blank" rel="noreferrer" className="mono">
                {j.id.slice(0, 8)}
              </a>{' '}
              {statusWords(j.status)}
            </li>
          ))}
        </ul>
      )}
      <div className="actions">
        {jobs.length > 1 && (
          <select className="input" aria-label="Job" value={job} onChange={(e) => setJob(e.target.value)}>
            {jobs.map((j) => (
              <option key={j} value={j}>
                {j.slice(0, 8)}
              </option>
            ))}
          </select>
        )}
        {job && (
          <>
            <button className="btn btn-quiet" type="button" onClick={() => setOpen(open === 'findings' ? null : 'findings')}>
              Rowanholt’s findings
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setOpen(open === 'cite' ? null : 'cite')}>
              Cite a row
            </button>
          </>
        )}
        {incident.can_edit && (
          <button className="link-button" type="button" onClick={onUnlink}>
            Unlink
          </button>
        )}
      </div>
      {open === 'findings' && <RemoteFindings incident={incident} link={link} job={job} />}
      {open === 'cite' && <CiteRow incident={incident} link={link} job={job} />}
    </li>
  );
}

// The incident's Rowanholt jobs and cases with their status, and the rows its findings cite
export function Rowanholt({ incident }: { incident: Incident }) {
  const view = useSection<RowanholtView>(incident, 'rowanholt');
  const save = useSave(incident, ['rowanholt', 'activity']);
  const [linking, setLinking] = useState(false);
  const linkPhase = useFinishing(linking);
  const [linked, setLinked] = useState(false);
  const base = `/api/incidents/${incident.id}/rowanholt`;

  async function link(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const value = String(data.get('target') ?? '').trim();
    const isJob = /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(value);
    setLinking(true);
    const out = await save.run(() =>
      api<RowanholtView>(`${base}/links`, { method: 'POST', json: isJob ? { job_id: value } : { case_number: value } }),
    );
    setLinking(false);
    setLinked(out !== undefined);
    if (out) form.reset();
  }

  const v = view.data;
  if (view.error) return <Notice kind="error">{messageOf(view.error)}</Notice>;
  if (!v) return null;
  return (
    <>
      <section className="panel" aria-label="Rowanholt">
        <header>
          <h2>Rowanholt</h2>
          <p>The evidence stays in Rowanholt. Ostraka keeps which jobs belong to this {incident.kind} and the rows its findings cite.</p>
        </header>
        {!v.configured && (
          <Notice kind="info">No Rowanholt server is connected. An admin connects one under Installation.</Notice>
        )}
        {v.message && <Notice kind={v.reachable === false ? 'error' : 'info'}>{v.message}</Notice>}
        {v.links.length === 0 ? (
          <p className="hint">Not linked to Rowanholt yet.</p>
        ) : (
          <ul className="stack" aria-label="Rowanholt links">
            {v.links.map((l) => (
              <LinkPanel
                key={l.id}
                incident={incident}
                link={l}
                onUnlink={() => save.run(() => api(`${base}/links/${l.id}`, { method: 'DELETE' }))}
              />
            ))}
          </ul>
        )}
        {v.configured && incident.can_edit && (
          <form className="inline-form" onSubmit={link} aria-label="Link Rowanholt">
            <input
              className="input mono"
              name="target"
              required
              maxLength={200}
              placeholder="A job id, or a case number"
              aria-label="Rowanholt job id or case number"
            />
            <button className="btn" type="submit" disabled={linking}>
              <Progress phase={linkPhase} ok={linked} label="Asking Rowanholt" busy="Asking Rowanholt…" done="Linked">
                Link
              </Progress>
            </button>
          </form>
        )}
        {save.error && <Notice kind="error">{save.error}</Notice>}
      </section>
      <section className="panel" aria-label="Cited rows">
        <header>
          <h2>Cited rows</h2>
          <p>Each keeps the row’s hash and how it read when it was cited.</p>
        </header>
        {v.citations.length === 0 ? (
          <p className="hint">No finding cites a Rowanholt row yet.</p>
        ) : (
          <ul className="deliveries">
            {v.citations.map((c) => (
              <li key={c.id}>
                <strong>{c.finding_ref}</strong>{' '}
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer" className="mono">
                    {c.cite}
                  </a>
                ) : (
                  <span className="mono">{c.cite}</span>
                )}
                {c.verification_id && <span className="chip chip-accent">verification</span>}
                {c.remote_status && <span className="hint"> · in Rowanholt: {c.remote_status}</span>}
                {Object.keys(c.excerpt).length > 0 && (
                  <div className="hint mono">
                    {Object.entries(c.excerpt)
                      .slice(0, 6)
                      .map(([k, val]) => `${k}=${val}`)
                      .join('  ')}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
