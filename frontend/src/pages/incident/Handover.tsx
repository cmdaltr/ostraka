// "Since you left" and the shift handover: what changed while you were away, and a note for whoever
// takes over, drafted by Ostraka from the activity log (no AI), edited and confirmed by you

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { describeChanges } from '../../api/handover';
import type { HandoverDraft, HandoverNote, SinceYouLeft } from '../../api/handover';
import type { Incident } from '../../api/incidents';
import type { Me } from '../../api/types';
import { Notice, formatDate } from '../../components/ui';
import { Rich, useSave, useSection } from './shared';
import './handover.css';

const seen = (incidentId: string, position?: number) =>
  api(`/api/incidents/${incidentId}/seen`, {
    method: 'POST',
    json: position === undefined ? {} : { position },
  }).catch(() => undefined);

// Shown once when the incident opens: what others did after the last entry you'd seen
export function SinceYouLeftBanner({ incident }: { incident: Incident }) {
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const marked = useRef(false);
  const since = useQuery({
    queryKey: ['since', incident.id],
    queryFn: () => api<SinceYouLeft>(`/api/incidents/${incident.id}/since`),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });

  // Your place moves on once this has been shown, and again as you leave, past what you saw live
  useEffect(() => {
    if (since.data && !marked.current) {
      marked.current = true;
      seen(incident.id, since.data.through_position);
    }
  }, [since.data, incident.id]);
  useEffect(
    () => () => {
      if (marked.current) void seen(incident.id);
    },
    [incident.id],
  );

  const data = since.data;
  if (!data || data.first_visit || !data.total || dismissed) return null;
  return (
    <section className="since" aria-label="Since you left">
      <div className="since-head">
        <p>
          <strong>Since you were last here</strong>
          {data.last_viewed_at && <span className="hint"> ({formatDate(data.last_viewed_at)})</span>}:{' '}
          {describeChanges(data.changes)}
          {data.people.length > 0 && <> by {data.people.join(', ')}</>}.
        </p>
        <div className="actions">
          <button className="link-button" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Hide' : `Show ${data.total === 1 ? 'it' : `all ${data.total}`}`}
          </button>
          <button className="link-button" type="button" onClick={() => setDismissed(true)}>
            Dismiss
          </button>
        </div>
      </div>
      {open && (
        <ol className="activity" aria-label="Changes since you left">
          {data.events.map((e) => (
            <li key={e.position}>
              <span className="num hint">{e.position}</span>
              <time className="num hint" dateTime={e.at}>
                {formatDate(e.at)}
              </time>
              <span>
                <Rich text={e.summary} />
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Compose({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const save = useSave(incident, ['handover', 'activity']);
  const draft = useQuery({
    queryKey: ['handover-draft', incident.id],
    queryFn: () => api<HandoverDraft>(`/api/incidents/${incident.id}/handover/draft`),
    staleTime: 0,
    gcTime: 0,
  });
  const [text, setText] = useState<string | null>(null);
  const body = text ?? draft.data?.text ?? '';

  async function post() {
    if (!draft.data) return;
    const done = await save.run(() =>
      api<HandoverNote>(`/api/incidents/${incident.id}/handover`, {
        method: 'POST',
        json: {
          body,
          from_position: draft.data.from_position,
          through_position: draft.data.through_position,
        },
      }),
    );
    if (done) onDone();
  }

  return (
    <section className="panel" aria-label="Write the handover">
      <header>
        <h2>Hand over</h2>
        <p>
          Ostraka drafted this from the activity log and the incident as it stands: open tasks and their owners,
          deadlines, new findings and open leads. Add what the log can’t know, then post it to the incident.
        </p>
      </header>
      {draft.isLoading && <p className="hint">Drafting…</p>}
      {draft.error && <Notice kind="error">The draft couldn’t be made. Try again.</Notice>}
      {draft.data && (
        <>
          <p className="hint">
            Covers {formatDate(draft.data.since)} onwards, log entries{' '}
            {draft.data.through_position > draft.data.from_position
              ? `${draft.data.from_position + 1}–${draft.data.through_position}`
              : 'none new'}
            .
          </p>
          <textarea
            className="input mono handover-text"
            aria-label="Handover note"
            rows={18}
            value={body}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="actions">
            <button className="btn btn-primary" type="button" disabled={!body.trim()} onClick={post}>
              Post the handover
            </button>
            {text !== null && (
              <button className="btn btn-quiet" type="button" onClick={() => setText(null)}>
                Back to Ostraka’s draft
              </button>
            )}
            <button className="btn btn-quiet" type="button" onClick={onDone}>
              Cancel
            </button>
          </div>
        </>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

export function Handover({ incident, me }: { incident: Incident; me: Me }) {
  const notes = useSection<HandoverNote[]>(incident, 'handover');
  const [writing, setWriting] = useState(false);
  const canWrite = incident.can_edit && me.role !== 'observer';

  return (
    <>
      {canWrite &&
        (writing ? (
          <Compose incident={incident} onDone={() => setWriting(false)} />
        ) : (
          <div className="section-bar">
            <p className="hint">End of your shift? Ostraka drafts the handover from everything since the last one.</p>
            <button className="btn btn-primary" type="button" onClick={() => setWriting(true)}>
              Write the handover
            </button>
          </div>
        ))}
      {notes.data?.length === 0 && <p className="hint">No handovers posted yet.</p>}
      <ul className="records" aria-label="Handovers">
        {notes.data?.map((n) => (
          <li key={n.id} className="record" id={n.ref}>
            <div className="record-head">
              <span className="mono ref">{n.ref}</span>
              <strong>{n.author?.name ?? 'Former member'}</strong>
              <time className="hint num" dateTime={n.created_at}>
                {formatDate(n.created_at)}
              </time>
              {n.edited && <span className="chip">edited from the draft</span>}
            </div>
            <div className="handover-body">
              <Rich text={n.body} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
