import { Link } from 'react-router-dom';
import type { Incident, TimelineItem } from '../../api/incidents';
import { formatDate } from '../../components/ui';
import { Rich, refTarget, useSection } from './shared';

const kindNames: Record<TimelineItem['kind'], string> = { sighting: 'Sighting', finding: 'Finding', link: 'Movement' };

// The attack as it happened, built from every record that carries a time; nothing is typed in twice
export function Timeline({ incident }: { incident: Incident }) {
  const items = useSection<TimelineItem[]>(incident, 'timeline');
  if (items.data?.length === 0)
    return (
      <p className="hint">
        Nothing has a time yet. Sightings, findings and movement between assets appear here once they say when they
        happened.
      </p>
    );
  return (
    <ol className="timeline" aria-label="Timeline">
      {items.data?.map((item) => (
        <li key={`${item.kind}:${item.entity_id}:${item.at}`} className={`tl-${item.kind}`}>
          <time className="num" dateTime={item.at}>
            {formatDate(item.at)}
          </time>
          <div>
            <div className="record-head">
              <span className="label">{kindNames[item.kind]}</span>
              <Link className="ref-link mono" to={refTarget(item.ref)}>
                {item.ref}
              </Link>
              {item.asset && <span className="chip">{item.asset}</span>}
              {item.techniques.map((t) => (
                <strong key={t} className="mono technique">
                  {t}
                </strong>
              ))}
            </div>
            <div>{item.title}</div>
            {item.detail && (
              <div className="hint">
                <Rich text={item.detail} />
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
