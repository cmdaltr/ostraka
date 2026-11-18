import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { keys } from '../../api/incidents';
import type { Incident } from '../../api/incidents';
import { searchQueries } from '../../api/siem';
import { Notice, messageOf } from '../../components/ui';
import { QueryList } from '../Queries';

// The SIEM queries run for this record. Only the queries are kept; their rows were the runner's alone
export function IncidentQueries({ incident }: { incident: Incident }) {
  const queries = useQuery({
    queryKey: keys.section(incident.id, 'queries'),
    queryFn: () => searchQueries({ incident_id: incident.id }),
  });
  return (
    <section className="panel" aria-label="Queries">
      <header className="panel-head">
        <div>
          <h2>Queries</h2>
          <p className="hint">What’s been searched for in the SIEMs for this record, and how much came back.</p>
        </div>
        {incident.can_edit && (
          <Link className="btn" to={`/queries?incident=${incident.id}`}>
            Run a query
          </Link>
        )}
      </header>
      {queries.error && <Notice kind="error">{messageOf(queries.error)}</Notice>}
      {queries.data &&
        (queries.data.length ? <QueryList queries={queries.data} /> : <p className="hint">None yet.</p>)}
    </section>
  );
}
