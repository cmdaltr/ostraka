import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../api/client';
import type { Asset, Finding, FindingKind, FindingStatus, Incident, Verification } from '../../api/incidents';
import { Notice, formatDate } from '../../components/ui';
import { Watching, useRecordPresence } from '../../live/presence';
import { Rich, Tags, fromLocalInput, splitTags, useSave, useSection } from './shared';

const kinds: [FindingKind, string, string][] = [
  ['evidence', 'Evidence', 'Something seen in this environment'],
  ['intelligence', 'Intelligence', 'Reporting from outside, graded by source'],
  ['potential', 'Potential', 'A possibility still to test'],
];
const statusNames: Record<FindingStatus, string> = { lead: 'Lead', confirmed: 'Confirmed', refuted: 'Refuted' };

// Admiralty grading, with the UK policing handling code (P: lawful sharing allowed; C: conditions apply)
const reliability = [
  ['A', 'Completely reliable'],
  ['B', 'Usually reliable'],
  ['C', 'Fairly reliable'],
  ['D', 'Not usually reliable'],
  ['E', 'Unreliable'],
  ['F', 'Reliability can’t be judged'],
];
const credibility = [
  ['1', 'Confirmed by other sources'],
  ['2', 'Probably true'],
  ['3', 'Possibly true'],
  ['4', 'Doubtful'],
  ['5', 'Improbable'],
  ['6', 'Truth can’t be judged'],
];

function AddFinding({ incident, assets, onDone }: { incident: Incident; assets: Asset[]; onDone: () => void }) {
  const save = useSave(incident, ['findings', 'timeline']);
  const [kind, setKind] = useState<FindingKind>('evidence');
  const [handling, setHandling] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const graded = kind === 'intelligence';
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/findings`, {
        method: 'POST',
        json: {
          kind,
          title: form.get('title'),
          statement: form.get('statement'),
          occurred_at: fromLocalInput(form.get('occurred_at')),
          asset_id: form.get('asset_id') || null,
          techniques: splitTags(String(form.get('techniques') ?? '')),
          tags: splitTags(String(form.get('tags') ?? '')),
          source_reliability: graded ? form.get('source_reliability') || null : null,
          information_credibility: graded ? form.get('information_credibility') || null : null,
          handling_code: graded ? handling || null : null,
          handling_conditions: graded ? form.get('handling_conditions') ?? '' : '',
        },
      }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <form className="panel" onSubmit={submit} aria-label="Record a finding">
      <header>
        <h2>Record a finding</h2>
        <p>It starts as a lead. An analyst confirms it once a search on that host or source shows it.</p>
      </header>
      <div className="segmented" role="radiogroup" aria-label="Kind">
        {kinds.map(([k, name, hint]) => (
          <button key={k} type="button" role="radio" aria-checked={kind === k} title={hint} onClick={() => setKind(k)}>
            {name}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Title</span>
        <input id="finding-title" name="title" required minLength={3} maxLength={200} />
      </label>
      <label className="field">
        <span>Statement</span>
        <textarea
          id="finding-statement"
          className="input"
          name="statement"
          rows={4}
          placeholder="Based on X and Y, we determine Z. There is no evidence of A."
        />
      </label>
      <div className="form-row">
        <label className="field">
          <span>When it happened</span>
          <input type="datetime-local" name="occurred_at" />
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
          <span>ATT&amp;CK techniques</span>
          <input name="techniques" className="mono" placeholder="T1059.001, T1021.002" />
        </label>
        <label className="field">
          <span>Tags</span>
          <input name="tags" />
        </label>
      </div>
      {kind === 'intelligence' && (
        <div className="form-row">
          <label className="field">
            <span>Source reliability</span>
            <select name="source_reliability" defaultValue="">
              <option value="">Not graded</option>
              {reliability.map(([v, t]) => (
                <option key={v} value={v}>
                  {v}: {t}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Information credibility</span>
            <select name="information_credibility" defaultValue="">
              <option value="">Not graded</option>
              {credibility.map(([v, t]) => (
                <option key={v} value={v}>
                  {v}: {t}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Handling</span>
            <select value={handling} onChange={(e) => setHandling(e.target.value)}>
              <option value="">Not set</option>
              <option value="P">P: may be shared lawfully</option>
              <option value="C">C: conditions apply</option>
            </select>
          </label>
          {handling === 'C' && (
            <label className="field">
              <span>Conditions</span>
              <input name="handling_conditions" required />
            </label>
          )}
        </div>
      )}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Record lead
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </form>
  );
}

function AddVerification({ incident, finding, onDone }: { incident: Incident; finding: Finding; onDone: () => void }) {
  const save = useSave(incident, ['findings']);
  const [method, setMethod] = useState<Verification['method']>('search');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const count = String(form.get('result_count') ?? '');
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/findings/${finding.id}/verifications`, {
        method: 'POST',
        json: {
          method,
          platform: form.get('platform') ?? '',
          query: form.get('query') ?? '',
          scope: form.get('scope'),
          time_from: fromLocalInput(form.get('time_from')),
          time_to: fromLocalInput(form.get('time_to')),
          result_count: count === '' ? null : Number(count),
          reference: form.get('reference') ?? '',
          note: form.get('note') ?? '',
        },
      }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <form className="verify-form stack" onSubmit={submit} aria-label={`Verify ${finding.ref}`}>
      <div className="segmented" role="radiogroup" aria-label="Verified by">
        {(['search', 'rowanholt', 'other'] as const).map((m) => (
          <button key={m} type="button" role="radio" aria-checked={method === m} onClick={() => setMethod(m)}>
            {m === 'search' ? 'A search' : m === 'rowanholt' ? 'Rowanholt' : 'Other'}
          </button>
        ))}
      </div>
      <div className="form-row">
        <label className="field">
          <span>Host or source searched</span>
          <input id="verify-scope" name="scope" required />
        </label>
        {method === 'search' && (
          <label className="field">
            <span>Platform</span>
            <input id="verify-platform" name="platform" required placeholder="Defender XDR, Splunk…" />
          </label>
        )}
        {method === 'rowanholt' && (
          <label className="field">
            <span>Reference</span>
            <input id="verify-reference" name="reference" required placeholder="Job, host, artefact and row" />
          </label>
        )}
        <label className="field">
          <span>From</span>
          <input type="datetime-local" name="time_from" />
        </label>
        <label className="field">
          <span>To</span>
          <input type="datetime-local" name="time_to" />
        </label>
        <label className="field">
          <span>Results</span>
          <input type="number" min={0} name="result_count" />
        </label>
      </div>
      {method === 'search' && (
        <label className="field">
          <span>Query</span>
          <textarea id="verify-query" className="input mono" name="query" rows={3} required />
        </label>
      )}
      <label className="field">
        <span>What it showed</span>
        <input name="note" />
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Add verification
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </form>
  );
}

function VerificationItem({ v }: { v: Verification }) {
  return (
    <li>
      <div>
        <strong>{v.method === 'search' ? v.platform : v.method === 'rowanholt' ? 'Rowanholt' : 'Other'}</strong> on{' '}
        <span className="mono">{v.scope}</span>
        {v.result_count !== null && <span className="num"> · {v.result_count} results</span>}
        {(v.time_from || v.time_to) && (
          <span className="hint num">
            {' '}
            · {formatDate(v.time_from)} to {formatDate(v.time_to)}
          </span>
        )}
      </div>
      {v.query && <pre className="query">{v.query}</pre>}
      {v.reference && <div className="mono">{v.reference}</div>}
      {v.note && <div><Rich text={v.note} /></div>}
      <div className="hint">
        {v.ran_by?.name ?? 'Someone'} · {formatDate(v.ran_at)}
      </div>
    </li>
  );
}

function FindingCard({ incident, finding, assets }: { incident: Incident; finding: Finding; assets: Asset[] }) {
  const save = useSave(incident, ['findings', 'timeline']);
  const [verifying, setVerifying] = useState(false);
  const [deciding, setDeciding] = useState<'refute' | null>(null);
  const [note, setNote] = useState('');
  const base = `/api/incidents/${incident.id}/findings/${finding.id}`;
  const decide = (action: 'confirm' | 'refute' | 'reopen', decisionNote = '') =>
    save.run(() => api(`${base}/${action}`, { method: 'POST', json: { version: finding.version, note: decisionNote } }));
  const asset = assets.find((a) => a.id === finding.asset_id);
  const grade = [finding.source_reliability, finding.information_credibility].filter(Boolean).join('');
  const verified = finding.verifications.length > 0;
  const focus = `finding:${finding.id}`;
  const presence = useRecordPresence(focus, verifying || deciding !== null);

  return (
    <li className={`record finding finding-${finding.status}`} id={finding.ref} {...presence}>
      <div className="record-head">
        <span className="mono ref">{finding.ref}</span>
        <Watching focus={focus} />
        <span className={`status status-${finding.status}`}>{statusNames[finding.status]}</span>
        <span className="chip">{finding.kind}</span>
        {grade && <span className="chip mono" title="Admiralty grade">{grade}</span>}
        {finding.handling_code && (
          <span className="chip mono" title={finding.handling_conditions || 'Handling code'}>
            {finding.handling_code}
          </span>
        )}
        {finding.techniques.map((t) => (
          <strong key={t} className="mono technique">
            {t}
          </strong>
        ))}
        <Tags tags={finding.tags} />
      </div>
      <h3>{finding.title}</h3>
      {finding.statement && (
        <p className="prose">
          <Rich text={finding.statement} />
        </p>
      )}
      <div className="hint num">
        {finding.occurred_at && `${formatDate(finding.occurred_at)} · `}
        {asset && `${asset.hostname} · `}
        recorded by {finding.created_by?.name ?? 'someone'}
      </div>
      {finding.decided_by && (
        <div className="hint">
          {statusNames[finding.status]} by {finding.decided_by.name}, {formatDate(finding.decided_at)}
          {finding.decision_note && <>: <Rich text={finding.decision_note} /></>}
        </div>
      )}

      <div className="verifications">
        <span className="label">Verified by</span>
        {verified ? (
          <ul>
            {finding.verifications.map((v) => (
              <VerificationItem key={v.id} v={v} />
            ))}
          </ul>
        ) : (
          <p className="hint">Nothing yet. Add the search that shows this on that specific host or source.</p>
        )}
      </div>

      {incident.can_edit && verifying && (
        <AddVerification incident={incident} finding={finding} onDone={() => setVerifying(false)} />
      )}
      {incident.can_edit && deciding === 'refute' && (
        <div className="stack">
          <label className="field">
            <span>Why it’s refuted</span>
            <input
              id={`refute-${finding.id}`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Based on…, this was…"
            />
          </label>
          <div className="actions">
            <button
              className="btn btn-primary"
              type="button"
              disabled={!note.trim()}
              onClick={async () => {
                if ((await decide('refute', note)) !== undefined) setDeciding(null);
              }}
            >
              Refute
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setDeciding(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {incident.can_edit && !verifying && !deciding && (
        <div className="actions">
          {finding.status !== 'refuted' && (
            <button className="btn btn-quiet" type="button" onClick={() => setVerifying(true)}>
              Add verification
            </button>
          )}
          {finding.status === 'lead' && (
            <>
              <button
                className="btn btn-primary"
                type="button"
                disabled={!verified}
                title={verified ? undefined : 'Confirming needs at least one verification'}
                onClick={() => decide('confirm')}
              >
                Confirm
              </button>
              <button className="btn btn-quiet" type="button" onClick={() => setDeciding('refute')}>
                Refute…
              </button>
            </>
          )}
          {finding.status !== 'lead' && (
            <button className="link-button" type="button" onClick={() => decide('reopen')}>
              Reopen as a lead
            </button>
          )}
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </li>
  );
}

export function Findings({ incident }: { incident: Incident }) {
  const findings = useSection<Finding[]>(incident, 'findings');
  const assets = useSection<Asset[]>(incident, 'assets');
  const [adding, setAdding] = useState(false);
  const [show, setShow] = useState<FindingStatus | 'all'>('all');
  const shown = findings.data?.filter((f) => show === 'all' || f.status === show) ?? [];
  const count = (s: FindingStatus) => findings.data?.filter((f) => f.status === s).length ?? 0;

  return (
    <>
      <div className="section-bar">
        <div className="segmented" role="group" aria-label="Show findings">
          {(['all', 'lead', 'confirmed', 'refuted'] as const).map((s) => (
            <button key={s} type="button" aria-pressed={show === s} onClick={() => setShow(s)}>
              {s === 'all' ? 'All' : `${statusNames[s]}s`}
              {s !== 'all' && <span className="count num">{count(s)}</span>}
            </button>
          ))}
        </div>
        {incident.can_edit && !adding && (
          <button className="btn btn-primary" type="button" onClick={() => setAdding(true)}>
            Record a finding
          </button>
        )}
      </div>
      {adding && <AddFinding incident={incident} assets={assets.data ?? []} onDone={() => setAdding(false)} />}
      {findings.data?.length === 0 && <p className="hint">No findings yet.</p>}
      <ul className="records" aria-label="Findings">
        {shown.map((f) => (
          <FindingCard key={f.id} incident={incident} finding={f} assets={assets.data ?? []} />
        ))}
      </ul>
    </>
  );
}
