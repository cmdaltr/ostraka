import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { archiveWords, askedFor, stateNames, timeLeft } from '../../api/catalogue';
import type { ArchiveState, Incident } from '../../api/incidents';
import { Notice, formatDate } from '../../components/ui';
import { useSave } from './shared';
import '../catalogue.css';

type Asking = null | 'restore' | 'archive';

/** A record's keeping: when it's archived and put in cold store, archiving it early, and asking for it back */
export function Archiving({ incident }: { incident: Incident }) {
  const a = incident.archive;
  const save = useSave(incident, ['lessons', 'activity']);
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState<Asking>(null);
  const [auto, setAuto] = useState(a?.auto ?? true);
  const [target, setTarget] = useState<ArchiveState | ''>('');
  if (!a || (incident.status === 'open' && !a.can_set)) return null;
  const base = `/api/incidents/${incident.id}`;
  const request = a.request;
  const archiveTo = a.archive_to ?? [];
  const chosen = target && archiveTo.includes(target) ? target : archiveTo[0];

  const run = async (change: () => Promise<unknown>) => {
    const done = await save.run(change);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['catalogue'] }),
      queryClient.invalidateQueries({ queryKey: ['archive-requests'] }),
    ]);
    return done;
  };

  async function setDays(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await run(() =>
      api(`${base}/archiving`, {
        method: 'PUT',
        json: {
          version: incident.version,
          auto,
          archive_after_days: Number(form.get('archive_after_days')),
          cold_after_days: Number(form.get('cold_after_days')),
        },
      }),
    );
    if (done !== undefined) setEditing(false);
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = new FormData(event.currentTarget).get('reason');
    const done = await run(() =>
      asking === 'restore'
        ? api(`${base}/restore`, { method: 'POST', json: { reason } })
        : api(`${base}/archive`, { method: 'POST', json: { reason, to_state: chosen } }),
    );
    if (done !== undefined) setAsking(null);
  }

  const decide = (verb: 'approve' | 'decline', note: string) =>
    request && run(() => api(`${base}/requests/${request.id}/${verb}`, { method: 'POST', json: { note } }));

  return (
    <section className="panel" aria-labelledby="archiving-head">
      <header className="panel-head">
        <h2 id="archiving-head">
          Archiving{' '}
          {incident.status === 'closed' && <span className={`chip archive-${a.state}`}>{stateNames[a.state]}</span>}
        </h2>
        <span className="actions">
          {a.can_set && !editing && (
            <button className="btn btn-quiet" type="button" onClick={() => setEditing(true)}>
              Change
            </button>
          )}
          {archiveTo.length > 0 && !asking && (
            <button className="btn btn-quiet" type="button" onClick={() => setAsking('archive')}>
              {a.state === 'archived' ? 'Cold store now' : 'Archive now'}
            </button>
          )}
          {a.can_restore && !asking && (
            <button className="btn" type="button" onClick={() => setAsking('restore')}>
              Ask for it back
            </button>
          )}
        </span>
      </header>
      <p>{archiveWords(a, incident.status)}</p>
      {a.state === 'archived' && <p className="hint">Archived records are read only until a restore is approved.</p>}
      {a.state === 'cold' && (
        <p className="hint">Its catalogue entry, lessons and exhibits’ custody stay open; the rest waits for a restore.</p>
      )}
      {a.restored_at && a.state === 'active' && <p className="hint">Restored {formatDate(a.restored_at)}.</p>}

      {editing && (
        <form className="stack" onSubmit={setDays} aria-label="Archiving days">
          <label className="check">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            <span>Archive this record once it’s closed</span>
          </label>
          <div className="form-row">
            <label className="field">
              <span>Archive after (days)</span>
              <input name="archive_after_days" type="number" min={1} max={3649} required disabled={!auto} defaultValue={a.archive_after_days} />
            </label>
            <label className="field">
              <span>Cold store after (days)</span>
              <input name="cold_after_days" type="number" min={2} max={3650} required disabled={!auto} defaultValue={a.cold_after_days} />
            </label>
          </div>
          <p className="hint">Counted from closing, or from a restore if it’s brought back.</p>
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Save
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {asking && (
        <form className="stack" onSubmit={ask} aria-label={asking === 'restore' ? 'Ask for it back' : 'Archive it now'}>
          {asking === 'archive' && archiveTo.length > 1 && (
            <fieldset className="segmented" aria-label="Archive to">
              {archiveTo.map((s) => (
                <button key={s} type="button" aria-pressed={chosen === s} onClick={() => setTarget(s)}>
                  {stateNames[s]}
                </button>
              ))}
            </fieldset>
          )}
          <label className="field">
            <span>{asking === 'restore' ? 'Why you need it back' : 'Why it should be archived now'}</span>
            <textarea className="input" name="reason" rows={3} required minLength={5} maxLength={1000} />
          </label>
          <p className="hint">
            An admin, or the lead of a team on the record, has to approve it, and not you. If nobody does within 24 hours it
            lapses.
          </p>
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Ask
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setAsking(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {request && (
        <Request
          incident={incident}
          onDecide={decide}
          onWithdraw={() => run(() => api(`${base}/requests/${request.id}`, { method: 'DELETE' }))}
        />
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

function Request({
  incident,
  onDecide,
  onWithdraw,
}: {
  incident: Incident;
  onDecide: (verb: 'approve' | 'decline', note: string) => unknown;
  onWithdraw: () => unknown;
}) {
  const request = incident.archive!.request!;
  const [note, setNote] = useState('');
  const what = askedFor(request);
  const waiting = <span className="hint">Waiting for an admin or a lead of a team on it.</span>;
  return (
    <div className="record archive-request" aria-label="Archive request">
      <div className="record-head">
        <strong>
          {request.requested_by?.name ?? 'A former member'} asked to {what}
        </strong>
        <span className="hint">
          {formatDate(request.requested_at)} · {timeLeft(request.expires_at)}
        </span>
      </div>
      <p>{request.reason}</p>
      {request.can_decide && (
        <>
          <label className="field">
            <span>Note (optional)</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
          </label>
          <div className="actions">
            <button className="btn btn-primary" type="button" onClick={() => onDecide('approve', note)}>
              {request.action === 'restore' ? 'Approve and restore' : `Approve and ${what.replace(' it', '')}`}
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => onDecide('decline', note)}>
              Decline
            </button>
          </div>
        </>
      )}
      {request.can_withdraw && (
        <div className="actions">
          {waiting}
          <button className="btn btn-quiet" type="button" onClick={() => onWithdraw()}>
            Withdraw
          </button>
        </div>
      )}
      {!request.can_decide && !request.can_withdraw && <p>{waiting}</p>}
    </div>
  );
}
