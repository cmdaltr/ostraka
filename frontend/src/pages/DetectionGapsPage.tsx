import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { Gap } from '../api/integrations';
import { SeverityPill, TlpPill } from '../components/Pills';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, messageOf } from '../components/ui';
import { GapRow } from './incident/DetectionGaps';

const filters = [
  ['open', 'Open'],
  ['closed', 'Closed'],
  ['all', 'All'],
] as const;

// Detection gaps across every incident you can see: what the detection team still has to build
export function DetectionGapsPage() {
  const [params, setParams] = useSearchParams();
  const status = filters.find(([k]) => k === params.get('status'))?.[0] ?? 'open';
  const gaps = useQuery({
    queryKey: ['detection-gaps', status],
    queryFn: () => api<Gap[]>(`/api/detection-gaps?status=${status}`),
  });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Detection gaps</h1>
          <p className="hint">Confirmed techniques Shikari has no detection for, in every incident you can see.</p>
        </div>
      </div>
      <nav className="tabs" aria-label="Which gaps">
        {filters.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-current={status === key ? 'page' : undefined}
            onClick={() => setParams(key === 'open' ? {} : { status: key })}
          >
            {label}
          </button>
        ))}
      </nav>
      {gaps.error && <Notice kind="error">{messageOf(gaps.error)}</Notice>}
      <Loading ready={!!gaps.data || !!gaps.error} label="Loading detection gaps">
        {gaps.data && gaps.data.length === 0 && <p className="hint">No {status === 'all' ? '' : status} gaps.</p>}
        {gaps.data && gaps.data.length > 0 && (
          <section className="panel" aria-label="Detection gaps across incidents">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Technique</th>
                    <th>State</th>
                    <th>Task</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {gaps.data.map((g) => (
                    <IncidentGap key={g.id} gap={g} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </Loading>
    </>
  );
}

function IncidentGap({ gap }: { gap: Gap }) {
  const incident = gap.incident!;
  return (
    <>
      <tr className="gap-incident">
        <td colSpan={4}>
          <Link to={`/incidents/${incident.ref}?s=detection-gaps`} className="mono ref-link">
            {incident.ref}
          </Link>{' '}
          {incident.title} <SeverityPill level={incident.severity} /> <TlpPill level={incident.tlp} />
        </td>
      </tr>
      <GapRow gap={gap}>{null}</GapRow>
    </>
  );
}
