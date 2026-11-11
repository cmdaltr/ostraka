import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { dueWords, taskStatusNames } from '../api/work';
import type { MyTask } from '../api/work';
import type { Me } from '../api/types';
import { SeverityPill } from '../components/Pills';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, messageOf } from '../components/ui';
import { WorkDeadlines } from './WorkDeadlines';
import './incident/incident.css';

function Row({ task, onClaimed }: { task: MyTask; onClaimed: () => void }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <li className={`record task${task.overdue ? ' task-overdue' : ''}`}>
      <div className="record-head">
        <Link className="ref-link mono" to={`/incidents/${task.incident.ref}?s=tasks#${task.ref}`}>
          {task.ref}
        </Link>
        <span className={`status status-${task.status}`}>{taskStatusNames[task.status]}</span>
        <span className={task.overdue ? 'due due-late' : 'due hint'}>{dueWords(task.due_at)}</span>
      </div>
      <h3>{task.title}</h3>
      <div className="incident-card-head hint">
        <span className="mono">{task.incident.ref}</span>
        <SeverityPill level={task.incident.severity} />
        <span>{task.incident.title}</span>
        {task.checklist.length > 0 && (
          <span className="num">
            {task.checklist.filter((i) => i.done).length}/{task.checklist.length} done
          </span>
        )}
      </div>
      {task.queued && (
        <div className="actions">
          <button
            className="btn btn-primary"
            type="button"
            onClick={() =>
              api(`/api/incidents/${task.incident.id}/tasks/${task.id}/claim`, {
                method: 'POST',
                json: { version: task.version },
              })
                .then(onClaimed)
                .catch((e) => setError(messageOf(e)))
            }
          >
            Claim
          </button>
        </div>
      )}
      {error && <Notice kind="error">{error}</Notice>}
    </li>
  );
}

// Everything waiting on you, across every open incident you're in, soonest due first
export function Work({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const tasks = useQuery({ queryKey: ['my-tasks'], queryFn: () => api<MyTask[]>('/api/tasks/mine') });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['my-tasks'] });
  const mine = tasks.data?.filter((t) => !t.queued) ?? [];
  const queue = tasks.data?.filter((t) => t.queued) ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Your work</h1>
          <p>Tasks assigned to you, and the ones waiting in {me.team ? `${me.team.name}’s` : 'your team’s'} queue.</p>
        </div>
      </div>
      {tasks.error && <Notice kind="error">{messageOf(tasks.error)}</Notice>}
      <Loading ready={!tasks.isPending} label="Loading your work">
        <WorkDeadlines />
        <section className="stack" aria-label="Assigned to you">
          <h2 className="label">Assigned to you</h2>
          {tasks.data && mine.length === 0 && <p className="hint">Nothing assigned to you.</p>}
          <ul className="records">
            {mine.map((t) => (
              <Row key={t.id} task={t} onClaimed={refresh} />
            ))}
          </ul>
        </section>
        {me.team && (
          <section className="stack" aria-label="Team queue">
            <h2 className="label">{me.team.name} queue</h2>
            {tasks.data && queue.length === 0 && <p className="hint">The queue is empty.</p>}
            <ul className="records">
              {queue.map((t) => (
                <Row key={t.id} task={t} onClaimed={refresh} />
              ))}
            </ul>
          </section>
        )}
      </Loading>
    </>
  );
}
