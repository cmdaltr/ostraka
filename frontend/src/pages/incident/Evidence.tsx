// Exhibits and their chain of custody. Custody moves only when the person offered it accepts, in
// their own words; every step is hash-chained and on the activity log, and nothing here edits one.

import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { CustodyEntry, CustodyKind, Exhibit } from '../../api/evidence';
import type { Finding, Incident } from '../../api/incidents';
import type { Person } from '../../api/types';
import { Notice, formatDate } from '../../components/ui';
import { Rich, fromLocalInput, refTarget, useSave, useSection } from './shared';
import './graph.css';

const SECTIONS = ['exhibits', 'graph', 'findings'];
const stepWords: Record<CustodyKind, string> = {
  received: 'Received',
  offered: 'Offered',
  accepted: 'Accepted',
  declined: 'Declined',
  withdrawn: 'Withdrawn',
};

function useDirectory() {
  return useQuery({ queryKey: ['directory'], queryFn: () => api<Person[]>('/api/users') });
}

function AddExhibit({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const save = useSave(incident, SECTIONS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const size = String(form.get('size_bytes') ?? '').trim();
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/evidence`, {
        method: 'POST',
        json: {
          label: form.get('label'),
          description: form.get('description'),
          sha256: form.get('sha256'),
          sha1: form.get('sha1'),
          md5: form.get('md5'),
          size_bytes: size ? Number(size) : null,
          location: form.get('location'),
          acquired_at: fromLocalInput(form.get('acquired_at')),
        },
      }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <form className="panel" onSubmit={submit} aria-label="Record an exhibit">
      <header>
        <h2>Record an exhibit</h2>
        <p>You hold it from now until someone accepts it from you. Its hashes can’t be changed afterwards.</p>
      </header>
      <label className="field">
        <span>What it is</span>
        <input id="exhibit-label" name="label" required minLength={3} maxLength={200} placeholder="FIN-WS-07 disk image (E01)" />
      </label>
      <label className="field">
        <span>SHA-256</span>
        <input id="exhibit-sha256" name="sha256" required className="mono" pattern="[0-9a-fA-F: ]{64,}" />
      </label>
      <div className="form-row">
        <label className="field">
          <span>MD5 (optional)</span>
          <input name="md5" className="mono" />
        </label>
        <label className="field">
          <span>SHA-1 (optional)</span>
          <input name="sha1" className="mono" />
        </label>
        <label className="field">
          <span>Size in bytes</span>
          <input name="size_bytes" type="number" min={0} className="num" />
        </label>
        <label className="field">
          <span>Acquired</span>
          <input name="acquired_at" type="datetime-local" />
        </label>
      </div>
      <label className="field">
        <span>Where it’s held</span>
        <input id="exhibit-location" name="location" required maxLength={500} placeholder="Evidence safe 2, sealed bag SB-40117" />
      </label>
      <label className="field">
        <span>Description</span>
        <textarea className="input" name="description" rows={2} />
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Record exhibit
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </form>
  );
}

function Offer({ incident, exhibit }: { incident: Incident; exhibit: Exhibit }) {
  const save = useSave(incident, SECTIONS);
  const directory = useDirectory();
  const [open, setOpen] = useState(false);
  const people = (directory.data ?? []).filter((p) => p.is_active && p.role !== 'observer' && p.id !== exhibit.holder?.id);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/evidence/${exhibit.id}/offer`, {
        method: 'POST',
        json: { to_id: form.get('to_id'), purpose: form.get('purpose'), location: form.get('location') },
      }),
    );
    if (done !== undefined) setOpen(false);
  }

  if (!open)
    return (
      <button className="btn" type="button" onClick={() => setOpen(true)}>
        Hand over
      </button>
    );
  return (
    <form className="form-row" onSubmit={submit} aria-label={`Hand over ${exhibit.ref}`}>
      <label className="field">
        <span>To</span>
        <select name="to_id" required defaultValue="">
          <option value="" disabled>
            Choose…
          </option>
          {people.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Why</span>
        <input name="purpose" required minLength={3} placeholder="Timeline analysis" />
      </label>
      <label className="field">
        <span>Where it’ll be held</span>
        <input name="location" placeholder="Unchanged if left empty" />
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Offer
        </button>
        <button className="btn btn-quiet" type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </form>
  );
}

function Pending({ incident, exhibit, offer }: { incident: Incident; exhibit: Exhibit; offer: CustodyEntry }) {
  const save = useSave(incident, SECTIONS);
  const base = `/api/incidents/${incident.id}/evidence/${exhibit.id}`;

  async function accept(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await save.run(() =>
      api(`${base}/accept`, {
        method: 'POST',
        json: { acknowledgement: form.get('acknowledgement'), sha256: form.get('sha256') },
      }),
    );
  }

  return (
    <div className="panel" aria-label="Handover waiting">
      <p>
        <strong>Waiting for {offer.to_person?.name ?? 'someone'} to accept.</strong> Offered by {offer.actor?.name}
        {offer.from_person && offer.actor?.id !== offer.from_person.id && ` on behalf of ${offer.from_person.name}`}, {formatDate(offer.at)}:{' '}
        <Rich text={offer.purpose} />
        {offer.location && <span className="hint"> · to be held at {offer.location}</span>}
      </p>
      {exhibit.can_answer && (
        <form onSubmit={accept} aria-label={`Accept ${exhibit.ref}`}>
          <label className="field">
            <span>Your acknowledgement</span>
            <textarea
              id="custody-acknowledgement"
              className="input"
              name="acknowledgement"
              rows={2}
              required
              minLength={3}
              placeholder="Received in sealed bag SB-40117, seal intact."
            />
          </label>
          <label className="field">
            <span>SHA-256 you computed (optional)</span>
            <input name="sha256" className="mono" />
          </label>
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Accept custody
            </button>
            <button
              className="btn btn-quiet"
              type="button"
              onClick={() => save.run(() => api(`${base}/decline`, { method: 'POST', json: { note: '' } }))}
            >
              Decline
            </button>
          </div>
        </form>
      )}
      {exhibit.can_withdraw && !exhibit.can_answer && (
        <div className="actions">
          <button
            className="btn btn-quiet"
            type="button"
            onClick={() => save.run(() => api(`${base}/withdraw`, { method: 'POST', json: { note: '' } }))}
          >
            Withdraw the offer
          </button>
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </div>
  );
}

function Cite({ incident, exhibit }: { incident: Incident; exhibit: Exhibit }) {
  const findings = useSection<Finding[]>(incident, 'findings');
  const save = useSave(incident, SECTIONS);
  const cited = new Set(exhibit.cited_by.map((c) => c.id));
  const choices = (findings.data ?? []).filter((f) => !cited.has(f.id));
  if (!choices.length) return null;
  return (
    <select
      className="input inline"
      aria-label={`Cite ${exhibit.ref} from a finding`}
      value=""
      onChange={(e) =>
        e.target.value &&
        save.run(() =>
          api(`/api/incidents/${incident.id}/findings/${e.target.value}/evidence`, {
            method: 'POST',
            json: { evidence_id: exhibit.id },
          }),
        )
      }
    >
      <option value="">Cite from a finding…</option>
      {choices.map((f) => (
        <option key={f.id} value={f.id}>
          {f.ref}: {f.title}
        </option>
      ))}
    </select>
  );
}

function ExhibitCard({ incident, exhibit }: { incident: Incident; exhibit: Exhibit }) {
  const x = exhibit;
  return (
    <li className="record" id={x.ref}>
      <div className="record-head">
        <span className="mono ref">{x.ref}</span>
        <strong>{x.label}</strong>
        {x.holder && <span className="chip">Held by {x.holder.name}</span>}
        {x.chain.ok ? (
          <span className="chip chain-ok" title={x.chain.head}>
            Chain intact · {x.chain.entries}
          </span>
        ) : (
          <span className="chip chain-broken">Chain broken at entry {x.chain.broken_at}</span>
        )}
        {x.hash_mismatch && <span className="chip chain-broken">SHA-256 mismatch on receipt</span>}
      </div>
      <p className="hint">
        Held at {x.location}
        {x.acquired_at && ` · acquired ${formatDate(x.acquired_at)}`}
        {x.size_bytes !== null && ` · ${x.size_bytes.toLocaleString('en-GB')} bytes`}
      </p>
      {x.description && <Rich text={x.description} />}
      {x.masked ? (
        <p className="hint">Hashes are withheld in the briefing view.</p>
      ) : (
        <dl className="hashes">
          <dt>SHA-256</dt>
          <dd>{x.sha256}</dd>
          {x.sha1 && (
            <>
              <dt>SHA-1</dt>
              <dd>{x.sha1}</dd>
            </>
          )}
          {x.md5 && (
            <>
              <dt>MD5</dt>
              <dd>{x.md5}</dd>
            </>
          )}
        </dl>
      )}
      <p>
        Cited by{' '}
        {x.cited_by.length
          ? x.cited_by.map((c, i) => (
              <span key={c.id}>
                {i > 0 && ', '}
                <Link className="ref-link mono" to={refTarget(c.ref)}>
                  {c.ref}
                </Link>
              </span>
            ))
          : 'no finding yet'}
        {incident.can_edit && (
          <>
            {' '}
            <Cite incident={incident} exhibit={x} />
          </>
        )}
      </p>
      {x.pending && <Pending incident={incident} exhibit={x} offer={x.pending} />}
      {x.can_offer && !x.pending && (
        <div className="actions">
          <Offer incident={incident} exhibit={x} />
        </div>
      )}
      <details>
        <summary>Chain of custody ({x.entries.length})</summary>
        <table className="table custody">
          <thead>
            <tr>
              <th>#</th>
              <th>When</th>
              <th>Step</th>
              <th>From</th>
              <th>To</th>
              <th>Detail</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {x.entries.map((e) => (
              <tr key={e.position}>
                <td className="num">{e.position}</td>
                <td className="num">{formatDate(e.at)}</td>
                <td>{stepWords[e.kind]}</td>
                <td>{e.from_person?.name ?? '—'}</td>
                <td>{e.to_person?.name ?? '—'}</td>
                <td>
                  {[e.purpose, e.acknowledgement].filter(Boolean).join('. ')}
                  {e.hash_check === 'match' && <span className="chain-ok"> SHA-256 matches.</span>}
                  {e.hash_check === 'mismatch' && <span className="chain-broken"> SHA-256 does not match.</span>}
                </td>
                <td className="hash" title={e.hash}>
                  {e.hash.slice(0, 12)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </li>
  );
}

export function Evidence({ incident }: { incident: Incident }) {
  const exhibits = useSection<Exhibit[]>(incident, 'exhibits', 'evidence');
  const [adding, setAdding] = useState(false);
  return (
    <>
      {incident.can_edit &&
        (adding ? (
          <AddExhibit incident={incident} onDone={() => setAdding(false)} />
        ) : (
          <div className="actions">
            <button className="btn btn-primary" type="button" onClick={() => setAdding(true)}>
              Record an exhibit
            </button>
          </div>
        ))}
      {exhibits.data?.length === 0 && <p className="hint">No exhibits yet.</p>}
      <ul className="records" aria-label="Exhibits">
        {exhibits.data?.map((x) => <ExhibitCard key={x.id} incident={incident} exhibit={x} />)}
      </ul>
    </>
  );
}
