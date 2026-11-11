// Your work: the next regulator deadline on each clock you own or whose incident you command,
// soonest first, counting down. Hidden when there are none.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { countdownWords, stateAt, stateNames } from '../api/clocks';
import type { MyDeadline } from '../api/clocks';
import { SeverityPill } from '../components/Pills';
import { formatDate } from '../components/ui';
import { useNow } from './incident/Clocks';
import './incident/clocks.css';

export function WorkDeadlines() {
  const deadlines = useQuery({ queryKey: ['my-clocks'], queryFn: () => api<MyDeadline[]>('/api/clocks/mine') });
  const now = useNow();
  if (!deadlines.data?.length) return null;
  return (
    <section className="stack" aria-label="Regulator deadlines">
      <h2 className="label">Regulator deadlines</h2>
      <ul className="records">
        {deadlines.data.map((d) => {
          const state = stateAt({ ...d, met_at: null }, now);
          return (
            <li key={`${d.clock_id}-${d.key}`} className={`record clock clock-${state}`}>
              <div className="record-head">
                <Link className="ref-link mono" to={`/incidents/${d.incident.ref}?s=clocks#${d.clock_ref}`}>
                  {d.clock_ref}
                </Link>
                <span className={`clock-state clock-state-${state}`}>{stateNames[state]}</span>
                <span className="clock-countdown mono num" role="timer">
                  {countdownWords(d.due_at, now)}
                </span>
              </div>
              <h3>
                {d.regime_name}
                {d.title && `: ${d.title}`}. {d.name}
              </h3>
              <div className="incident-card-head hint">
                <span className="mono">{d.incident.ref}</span>
                <SeverityPill level={d.incident.severity} />
                <span>{d.incident.title}</span>
                <span>
                  Due {formatDate(d.due_at)}
                  {d.provisional && ' (provisional)'} · {d.as_owner ? 'You own it' : `Owner: ${d.owner?.name ?? 'nobody'}`}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
