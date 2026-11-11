import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { Incident } from '../../api/incidents';
import { gapState } from '../../api/integrations';
import type { Gap, Gaps } from '../../api/integrations';
import type { Me } from '../../api/types';
import { useFinishing } from '../../components/sherd/Ostrakon';
import { Progress } from '../../components/sherd/Progress';
import { Notice, messageOf } from '../../components/ui';
import { FollowUp } from './FollowUp';
import { useSave, useSection } from './shared';

export function GapRow({ gap, children }: { gap: Gap; children?: ReactNode }) {
  return (
    <tr className={gap.status === 'closed' ? 'inactive' : ''}>
      <td>
        <a href={gap.attack_url} target="_blank" rel="noreferrer" className="mono">
          {gap.technique}
        </a>
        {gap.technique_name && <div className="hint">{gap.technique_name}</div>}
      </td>
      <td>{gapState(gap)}</td>
      <td className="mono">
        {gap.task ? (
          <Link to={`/incidents/${gap.incident?.ref ?? ''}?s=tasks#${gap.task.ref}`} className="ref-link">
            {gap.task.ref}
          </Link>
        ) : (
          '—'
        )}
      </td>
      <td className="hint">{gap.status === 'closed' ? gap.closed_by : gap.check_error || (gap.finding_ref ?? '')}</td>
      {children}
    </tr>
  );
}

// Every confirmed technique, and whether Shikari has a detection for it
export function DetectionGaps({ incident, me }: { incident: Incident; me: Me }) {
  const gaps = useSection<Gaps>(incident, 'detection-gaps');
  const save = useSave(incident, ['detection-gaps', 'tasks', 'activity']);
  const [checking, setChecking] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const checkPhase = useFinishing(checking);
  const [giving, setGiving] = useState<string | null>(null);

  async function check() {
    setChecking(true);
    const out = await save.run(() =>
      api<Gaps>(`/api/incidents/${incident.id}/detection-gaps/check`, { method: 'POST' }),
    );
    setChecking(false);
    if (out) setAnswer(out.message);
  }

  const g = gaps.data;
  if (gaps.error) return <Notice kind="error">{messageOf(gaps.error)}</Notice>;
  if (!g) return null;
  const open = g.gaps.filter((x) => x.status === 'open').length;
  // Rows here link within this incident
  const rows = g.gaps.map((x) => ({ ...x, incident: x.incident ?? { id: incident.id, ref: incident.ref, title: incident.title, severity: incident.severity, tlp: incident.tlp } }));
  return (
    <section className="panel" aria-label="Detection gaps">
      <header>
        <h2>Detection gaps</h2>
        <p>
          Each technique a confirmed finding shows, and whether Shikari can detect it. A gap closes only when Shikari has
          a detection for it; finishing its task doesn’t.
        </p>
      </header>
      {!g.configured && (
        <Notice kind="info">No Shikari server is connected, so detection gaps aren’t tracked. An admin connects one under Installation.</Notice>
      )}
      {g.configured && !g.automation && (
        <p className="hint">Ask Shikari from here when a detection has been built. With Pro, Shikari’s webhook asks after every run.</p>
      )}
      {rows.length === 0 ? (
        <p className="hint">No confirmed techniques yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="table" aria-label="Techniques">
            <thead>
              <tr>
                <th>Technique</th>
                <th>State</th>
                <th>Task</th>
                <th>Why</th>
                {incident.can_edit && <th />}
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => (
                <GapRow key={x.id} gap={x}>
                  {incident.can_edit && (
                    <td>
                      {x.status === 'open' && (!x.task || x.task.status === 'cancelled') &&
                        (giving === x.id ? (
                          <FollowUp
                            incident={incident}
                            me={me}
                            label={`Task for ${x.technique}`}
                            onCancel={() => setGiving(null)}
                            onSubmit={async (owner) => {
                              const out = await save.run(() =>
                                api<Gaps>(`/api/incidents/${incident.id}/detection-gaps/${x.id}/task`, {
                                  method: 'POST',
                                  json: owner,
                                }),
                              );
                              if (out) setGiving(null);
                            }}
                          />
                        ) : (
                          <button className="btn btn-quiet" type="button" onClick={() => setGiving(x.id)}>
                            Give it a task
                          </button>
                        ))}
                    </td>
                  )}
                </GapRow>
              ))}
            </tbody>
          </table>
          <p className="hint">
            {open} open of {rows.length}.{' '}
            {g.coverage_url && (
              <a href={g.coverage_url} target="_blank" rel="noreferrer">
                Shikari’s coverage
              </a>
            )}
          </p>
        </div>
      )}
      {answer && <Notice kind="ok">{answer}</Notice>}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      {g.configured && incident.can_edit && (
        <div className="actions">
          <button className="btn" type="button" onClick={check} disabled={checking}>
            <Progress phase={checkPhase} ok={!save.error} label="Asking Shikari" busy="Asking Shikari…" done="Checked">
              Ask Shikari again
            </Progress>
          </button>
        </div>
      )}
    </section>
  );
}
