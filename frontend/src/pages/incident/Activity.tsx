import { useState } from 'react';
import { api } from '../../api/client';
import type { ActivityEvent, Incident } from '../../api/incidents';
import { Notice, formatDate, messageOf } from '../../components/ui';
import { useSection } from './shared';

interface ChainCheck {
  ok: boolean;
  events: number;
  broken_at: number | null;
}

// Every change, in order. Each entry's hash covers the one before it, so an edit to history shows.
export function Activity({ incident }: { incident: Incident }) {
  const events = useSection<ActivityEvent[]>(incident, 'activity');
  const [check, setCheck] = useState<ChainCheck | null>(null);
  const [error, setError] = useState<string | null>(null);

  const verify = () =>
    api<ChainCheck>(`/api/incidents/${incident.id}/activity/verify`)
      .then((c) => {
        setCheck(c);
        setError(null);
      })
      .catch((e) => setError(messageOf(e)));

  return (
    <>
      <div className="section-bar">
        <p className="hint">Each entry is chained to the one before it, so history can’t be changed quietly.</p>
        <button className="btn btn-quiet" type="button" onClick={verify}>
          Check the chain
        </button>
      </div>
      {check &&
        (check.ok ? (
          <Notice kind="ok">All {check.events} entries are intact.</Notice>
        ) : (
          <Notice kind="error">The chain breaks at entry {check.broken_at}. History has been altered from there on.</Notice>
        ))}
      {error && <Notice kind="error">{error}</Notice>}
      <ol className="activity" aria-label="Activity">
        {[...(events.data ?? [])].reverse().map((e) => (
          <li key={e.position}>
            <span className="num hint">{e.position}</span>
            <time className="num hint" dateTime={e.at}>
              {formatDate(e.at)}
            </time>
            <span>{e.summary}</span>
          </li>
        ))}
      </ol>
    </>
  );
}
