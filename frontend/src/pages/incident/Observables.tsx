import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { label } from '../../api/incidents';
import type { Asset, Extracted, Incident, IncidentObservable, Verdict } from '../../api/incidents';
import type { Me } from '../../api/types';
import { Notice, formatDate } from '../../components/ui';
import { Tags, fromLocalInput, useSave, useSection } from './shared';

const verdicts: Verdict[] = ['unknown', 'suspicious', 'malicious', 'benign'];

// Paste anything (an alert, an email, a log excerpt); ostraka finds the indicators and the analyst picks
function Extract({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const save = useSave(incident, ['observables']);
  const [found, setFound] = useState<Extracted[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const base = `/api/incidents/${incident.id}/observables`;

  async function scan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = String(new FormData(event.currentTarget).get('text') ?? '');
    const result = await save.run(() => api<Extracted[]>(`${base}/extract`, { method: 'POST', json: { text } }));
    if (result) {
      setFound(result);
      setPicked(new Set(result.flatMap((r, i) => (r.already_in_incident ? [] : [i]))));
    }
  }

  async function add() {
    if (!found) return;
    const chosen = found.filter((_, i) => picked.has(i));
    const done = await save.run(async () => {
      for (const f of chosen) await api(base, { method: 'POST', json: { value: f.value, kind: f.kind } });
      return true;
    });
    if (done) onDone();
  }

  return (
    <section className="panel">
      <header>
        <h2>Pull indicators from text</h2>
        <p>Paste an alert, an email or a log excerpt. Defanged values are understood. Nothing is saved until you add it.</p>
      </header>
      {!found ? (
        <form className="stack" onSubmit={scan}>
          <textarea id="extract-text" className="input mono" name="text" rows={6} required aria-label="Text to scan" />
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Find indicators
            </button>
            <button className="btn btn-quiet" type="button" onClick={onDone}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="stack">
          {found.length === 0 ? (
            <p className="hint">No indicators found in that text.</p>
          ) : (
            <ul className="pick-list" aria-label="Indicators found">
              {found.map((f, i) => (
                <li key={`${f.kind}:${f.value}`}>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={picked.has(i)}
                      disabled={f.already_in_incident}
                      onChange={(e) => {
                        const next = new Set(picked);
                        if (e.target.checked) next.add(i);
                        else next.delete(i);
                        setPicked(next);
                      }}
                    />
                    <span className="mono value">{f.value}</span>
                    <span className="chip">{label(f.kind)}</span>
                    {f.already_in_incident && <span className="hint">already here</span>}
                  </label>
                </li>
              ))}
            </ul>
          )}
          <div className="actions">
            <button className="btn btn-primary" type="button" disabled={!picked.size} onClick={add}>
              Add {picked.size || ''} to the incident
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setFound(null)}>
              Back
            </button>
          </div>
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

function Sightings({ incident, item, assets }: { incident: Incident; item: IncidentObservable; assets: Asset[] }) {
  const save = useSave(incident, ['observables', 'timeline']);
  const [open, setOpen] = useState(false);
  const host = (id: string | null) => assets.find((a) => a.id === id)?.hostname;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/observables/${item.id}/sightings`, {
        method: 'POST',
        json: {
          seen_at: fromLocalInput(form.get('seen_at')),
          asset_id: form.get('asset_id') || null,
          host: form.get('host'),
          user_account: form.get('user_account'),
          source: form.get('source'),
        },
      }),
    );
    if (done !== undefined) setOpen(false);
  }

  return (
    <div className="sightings">
      {item.sightings.map((s) => (
        <div key={s.id} className="hint num">
          {formatDate(s.seen_at)} · {host(s.asset_id) || s.host || '—'}
          {s.user_account && ` · ${s.user_account}`}
          {s.source && ` · ${s.source}`}
        </div>
      ))}
      {incident.can_edit &&
        (open ? (
          <form className="form-row" onSubmit={submit} aria-label={`Sighting of ${item.ref}`}>
            <label className="field">
              <span>Seen</span>
              <input type="datetime-local" name="seen_at" />
            </label>
            <label className="field">
              <span>Asset</span>
              <select name="asset_id" defaultValue="">
                <option value="">None</option>
                {assets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.hostname}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Host</span>
              <input name="host" />
            </label>
            <label className="field">
              <span>Account</span>
              <input name="user_account" />
            </label>
            <label className="field">
              <span>Source</span>
              <input name="source" placeholder="EDR, proxy, mailbox" />
            </label>
            <div className="actions">
              <button className="btn btn-primary" type="submit">
                Record
              </button>
              <button className="btn btn-quiet" type="button" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <button className="link-button" type="button" onClick={() => setOpen(true)}>
            Record a sighting
          </button>
        ))}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </div>
  );
}

function Elsewhere({ item }: { item: IncidentObservable }) {
  const { visible, hidden, ask } = item.elsewhere;
  if (!visible.length && !hidden) return null;
  return (
    <div className="elsewhere" role="note">
      <strong>Seen in {visible.length + hidden} other incident{visible.length + hidden === 1 ? '' : 's'}.</strong>{' '}
      {visible.map((v) => (
        <Link key={v.id} className="ref-link mono" to={`/incidents/${v.ref}`}>
          #{v.ref}
        </Link>
      ))}
      {hidden > 0 && <span> {hidden} you can’t open; ask {ask.join(' or ')}.</span>}
    </div>
  );
}

function Row({ incident, item, assets }: { incident: Incident; item: IncidentObservable; assets: Asset[] }) {
  const save = useSave(incident, ['observables']);
  const base = `/api/incidents/${incident.id}/observables/${item.id}`;
  return (
    <li className="record" id={item.ref}>
      <div className="record-head">
        <span className="mono ref">{item.ref}</span>
        <span className="chip">{label(item.kind)}</span>
        <span className={`verdict verdict-${item.verdict}`}>{item.verdict}</span>
        <Tags tags={item.tags} />
      </div>
      <div className="mono value">{item.masked ? <span className="masked">[{label(item.kind)}]</span> : item.value}</div>
      {item.note && <p className="prose">{item.note}</p>}
      <Elsewhere item={item} />
      <Sightings incident={incident} item={item} assets={assets} />
      {incident.can_edit && (
        <div className="actions">
          <select
            className="input inline"
            aria-label={`Verdict for ${item.ref}`}
            value={item.verdict}
            onChange={(e) =>
              save.run(() => api(base, { method: 'PATCH', json: { version: item.version, verdict: e.target.value } }))
            }
          >
            {verdicts.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <button className="link-button" type="button" onClick={() => save.run(() => api(base, { method: 'DELETE' }))}>
            Remove from incident
          </button>
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </li>
  );
}

export function Observables({ incident, me }: { incident: Incident; me: Me }) {
  const list = useSection<IncidentObservable[]>(incident, 'observables');
  const assets = useSection<Asset[]>(incident, 'assets');
  const save = useSave(incident, ['observables']);
  const [extracting, setExtracting] = useState(false);

  async function addOne(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const value = String(new FormData(formEl).get('value') ?? '').trim();
    if (!value) return;
    const done = await save.run(() => api(`/api/incidents/${incident.id}/observables`, { method: 'POST', json: { value } }));
    if (done !== undefined) formEl.reset();
  }

  return (
    <>
      {incident.can_edit &&
        (extracting ? (
          <Extract incident={incident} onDone={() => setExtracting(false)} />
        ) : (
          <form className="panel" onSubmit={addOne} aria-label="Add an observable">
            <div className="inline-form">
              <input
                id="observable-value"
                className="input mono"
                name="value"
                placeholder="An IP, domain, hash, URL, path… (its type is detected)"
                aria-label="Observable"
              />
              <button className="btn btn-primary" type="submit">
                Add
              </button>
              <button className="btn btn-quiet" type="button" onClick={() => setExtracting(true)}>
                Paste text…
              </button>
            </div>
            {save.error && <Notice kind="error">{save.error}</Notice>}
          </form>
        ))}
      {me.role === 'observer' && !me.show_raw_iocs && (
        <Notice>Indicator values are hidden in the briefing view. Turn on raw indicators in Settings to see them.</Notice>
      )}
      {list.data?.length === 0 && <p className="hint">No observables yet.</p>}
      <ul className="records" aria-label="Observables">
        {list.data?.map((item) => <Row key={item.id} incident={incident} item={item} assets={assets.data ?? []} />)}
      </ul>
    </>
  );
}
