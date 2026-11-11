import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { Incident } from '../../api/incidents';
import { dueWords, finished, taskStatusNames } from '../../api/work';
import type { AppliedPlaybook, Outcome, Playbook, Task, TaskLink, TaskStatus } from '../../api/work';
import type { Me, Person, Team } from '../../api/types';
import { severities } from '../../brand';
import { Notice, formatDate } from '../../components/ui';
import { Workflow } from '../../components/Workflow';
import { Watching, useRecordPresence } from '../../live/presence';
import { Rich, fromLocalInput, toLocalInput, useSave, useSection } from './shared';

const statuses: TaskStatus[] = ['open', 'in_progress', 'blocked', 'done', 'cancelled'];

function useTeams() {
  return useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams') });
}

function useDirectory() {
  return useQuery({ queryKey: ['directory'], queryFn: () => api<Person[]>('/api/users') });
}

export function sortTasks(tasks: Task[]): Task[] {
  const due = (t: Task) => (t.due_at ? new Date(t.due_at).getTime() : Infinity);
  return [...tasks].sort(
    (a, b) => Number(finished(a.status)) - Number(finished(b.status)) || due(a) - due(b) || a.ref.localeCompare(b.ref, 'en', { numeric: true }),
  );
}

function AddTask({ incident, parent, onDone }: { incident: Incident; parent?: Task; onDone: () => void }) {
  const save = useSave(incident, ['tasks']);
  const teams = useTeams();
  const directory = useDirectory();
  // A subtask starts with its parent's owner
  const [owner, setOwner] = useState<'team' | 'person'>(parent?.assignee && !parent.team ? 'person' : 'team');
  const [teamId, setTeamId] = useState(parent?.team?.id ?? incident.teams[0]?.id ?? '');
  const involved = new Set(incident.teams.map((t) => t.id));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/tasks`, {
        method: 'POST',
        json: {
          title: form.get('title'),
          parent_id: parent?.id ?? null,
          description: form.get('description'),
          team_id: owner === 'team' ? teamId : null,
          assignee_id: owner === 'person' ? form.get('assignee_id') : null,
          due_at: fromLocalInput(form.get('due_at')),
          checklist: String(form.get('checklist') ?? '').split('\n'),
        },
      }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <form className="panel" onSubmit={submit} aria-label={parent ? `Add a subtask to ${parent.ref}` : 'Add a task'}>
      <header>
        <h2>{parent ? `Add a subtask to ${parent.ref}` : 'Add a task'}</h2>
        <p>Give it to a team and it waits in their queue until someone claims it, or give it to a person.</p>
      </header>
      <label className="field">
        <span>{parent ? 'Subtask' : 'Task'}</span>
        <input id={parent ? `subtask-title-${parent.ref}` : 'task-title'} name="title" required minLength={3} maxLength={200} autoFocus={!!parent} />
      </label>
      <div className="form-row">
        <div className="field">
          <span>For</span>
          <div className="segmented" role="radiogroup" aria-label="Assign to">
            <button type="button" role="radio" aria-checked={owner === 'team'} onClick={() => setOwner('team')}>
              A team
            </button>
            <button type="button" role="radio" aria-checked={owner === 'person'} onClick={() => setOwner('person')}>
              A person
            </button>
          </div>
        </div>
        {owner === 'team' ? (
          <label className="field">
            <span>Team</span>
            <select id="task-team" value={teamId} onChange={(e) => setTeamId(e.target.value)} required>
              {teams.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                  {involved.has(t.id) ? '' : ' (brings them in)'}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className="field">
            <span>Person</span>
            <select id="task-assignee" name="assignee_id" required defaultValue={parent?.assignee?.id ?? ''}>
              <option value="" disabled>
                Choose…
              </option>
              {directory.data
                ?.filter((p) => p.is_active && p.role !== 'observer')
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.team ? ` · ${p.team.name}` : ''}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Due</span>
          <input id="task-due" type="datetime-local" name="due_at" />
        </label>
      </div>
      <label className="field">
        <span>Details</span>
        <textarea className="input" name="description" rows={2} />
      </label>
      <label className="field">
        <span>Checklist, one item per line</span>
        <textarea id="task-checklist" className="input" name="checklist" rows={3} />
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          {parent ? 'Add subtask' : 'Add task'}
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </form>
  );
}

function Checklist({ incident, task }: { incident: Incident; task: Task }) {
  const save = useSave(incident, ['tasks']);
  const base = `/api/incidents/${incident.id}/tasks/${task.id}/checklist`;
  const [text, setText] = useState('');
  const [adding, setAdding] = useState(task.checklist.length > 0);
  const done = task.checklist.filter((i) => i.done).length;
  if (!task.checklist.length && !incident.can_edit) return null;
  return (
    <div className="checklist">
      {task.checklist.length > 0 && (
        <span className="label">
          Checklist <span className="num">{done}/{task.checklist.length}</span>
        </span>
      )}
      <ul>
        {task.checklist.map((item) => (
          <li key={item.id}>
            <label className="check">
              <input
                type="checkbox"
                checked={item.done}
                disabled={!incident.can_edit}
                onChange={(e) =>
                  save.run(() =>
                    api(`${base}/${item.id}`, { method: 'PATCH', json: { version: item.version, done: e.target.checked } }),
                  )
                }
              />
              <span className={item.done ? 'ticked' : undefined}>{item.text}</span>
              {item.done_by && <span className="hint">{item.done_by.name}</span>}
            </label>
          </li>
        ))}
      </ul>
      {incident.can_edit && !finished(task.status) && !adding && (
        <button className="link-button add-check" type="button" onClick={() => setAdding(true)}>
          Add a checklist
        </button>
      )}
      {incident.can_edit && !finished(task.status) && adding && (
        <form
          className="inline-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (text.trim() && (await save.run(() => api(base, { method: 'POST', json: { text } }))) !== undefined) setText('');
          }}
        >
          <input
            className="input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Add an item"
            autoFocus={task.checklist.length === 0}
            aria-label={`Add a checklist item to ${task.ref}`}
          />
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </div>
  );
}

function Dependency({ link, onRemove }: { link: TaskLink; onRemove?: () => void }) {
  return (
    <li className={finished(link.status) ? 'dep dep-done' : 'dep'}>
      <Link className="ref-link mono" to={`/incidents/${link.incident_ref}?s=tasks#${link.ref}`}>
        {link.ref}
      </Link>
      <span>{link.title}</span>
      <span className="hint">
        {taskStatusNames[link.status].toLowerCase()} · {link.incident_ref}
      </span>
      {onRemove && (
        <button className="chip-x" type="button" aria-label={`Stop waiting on ${link.ref}`} onClick={onRemove}>
          ×
        </button>
      )}
    </li>
  );
}

// What this task waits on and what waits on it, across incidents
function Dependencies({ incident, task }: { incident: Incident; task: Task }) {
  const save = useSave(incident, ['tasks']);
  const base = `/api/incidents/${incident.id}/tasks/${task.id}/blocked-by`;
  const waiting = task.blocked_by.filter((b) => !finished(b.status)).length;
  if (!task.blocked_by.length && !task.blocks.length && !task.hidden_links) return null;
  return (
    <div className="deps">
      {task.blocked_by.length > 0 && (
        <>
          <span className="label">
            Waits on{' '}
            {waiting === 0 ? <span className="hint">· all finished, it can go ahead</span> : <span className="hint">· {waiting} to go</span>}
          </span>
          <ul aria-label={`${task.ref} waits on`}>
            {task.blocked_by.map((b) => (
              <Dependency
                key={b.id}
                link={b}
                onRemove={incident.can_edit ? () => save.run(() => api(`${base}/${b.id}`, { method: 'DELETE' })) : undefined}
              />
            ))}
          </ul>
        </>
      )}
      {task.blocks.length > 0 && (
        <>
          <span className="label">Holding up</span>
          <ul aria-label={`Waiting on ${task.ref}`}>
            {task.blocks.map((b) => (
              <Dependency key={b.id} link={b} />
            ))}
          </ul>
        </>
      )}
      {task.hidden_links > 0 && (
        <p className="hint">
          Also linked to {task.hidden_links === 1 ? 'a task' : `${task.hidden_links} tasks`} in incidents you can’t open.
        </p>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </div>
  );
}

// A decision: choose how it went. An outcome that promotes the hunt asks what the incident is called first.
function Decide({ incident, task, outcomes }: { incident: Incident; task: Task; outcomes: Outcome[] }) {
  const save = useSave(incident, ['tasks', 'playbooks', 'record-links', 'activity']);
  const queryClient = useQueryClient();
  const [promoting, setPromoting] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const action = (label: string) => outcomes.find((o) => o.label === label)?.action ?? 'none';
  const choose = async (outcome: string, promote?: Record<string, unknown>) => {
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/tasks/${task.id}/decide`, {
        method: 'POST',
        json: { version: task.version, outcome, note, promote },
      }),
    );
    if (done !== undefined) {
      setPromoting(null);
      queryClient.invalidateQueries({ queryKey: ['incidents'] });
    }
  };
  return (
    <div className="decision" role="group" aria-label={`Decide ${task.ref}`}>
      <span className="label">How did it go?</span>
      <input
        className="input"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Why (optional, goes on the activity log)"
        aria-label={`Why, for ${task.ref}`}
        maxLength={5000}
      />
      <div className="actions">
        {task.outcomes.map((label) => (
          <button
            key={label}
            className="btn"
            type="button"
            onClick={() => (action(label) === 'promote' ? setPromoting(label) : choose(label))}
          >
            {label}
          </button>
        ))}
      </div>
      {promoting && (
        <form
          className="inline-form"
          aria-label="The incident it becomes"
          onSubmit={(e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            choose(promoting, { title: form.get('title'), severity: form.get('severity'), summary: form.get('summary') ?? '' });
          }}
        >
          <input className="input" name="title" required minLength={3} maxLength={200} placeholder="Incident title" aria-label="Incident title" autoFocus />
          <select name="severity" defaultValue="high" aria-label="Incident severity">
            {severities.map((sv) => (
              <option key={sv} value={sv}>
                {sv}
              </option>
            ))}
          </select>
          <button className="btn btn-primary" type="submit">
            Promote
          </button>
          <button className="btn btn-quiet" type="button" onClick={() => setPromoting(null)}>
            Cancel
          </button>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </div>
  );
}

function TaskCard({
  incident,
  task,
  subtasks = [],
  outcomes = [],
  me,
}: {
  incident: Incident;
  task: Task;
  subtasks?: Task[];
  outcomes?: Outcome[];
  me: Me;
}) {
  const save = useSave(incident, ['tasks', 'playbooks']);
  const [adding, setAdding] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [blocking, setBlocking] = useState(false);
  const [nudging, setNudging] = useState(false);
  const [note, setNote] = useState('');
  const [nudged, setNudged] = useState(false);
  const base = `/api/incidents/${incident.id}/tasks/${task.id}`;
  const patch = (json: Record<string, unknown>) =>
    save.run(() => api(base, { method: 'PATCH', json: { version: task.version, ...json } }));

  const mine = task.assignee?.id === me.id;
  const canClaim = !task.assignee && !finished(task.status) && (task.team?.id === me.team?.id || me.role === 'admin');
  const canRelease = (mine || incident.can_manage) && !!task.assignee && !!task.team && !finished(task.status);
  const canNudge = !finished(task.status) && !mine;
  const owner = task.assignee ? task.assignee.name : `${task.team?.name}’s queue`;
  const focus = `task:${task.id}`;
  const presence = useRecordPresence(focus, adding || waiting || blocking || nudging);

  return (
    <li className={`record task task-${task.status}${task.overdue ? ' task-overdue' : ''}`} id={task.ref} {...presence}>
      <div className="record-head">
        <span className="mono ref">{task.ref}</span>
        <Watching focus={focus} />
        <span className={`status status-${task.status}`}>{taskStatusNames[task.status]}</span>
        <span className="chip">{owner}</span>
        {task.assignee && task.team && <span className="hint">{task.team.name}</span>}
        <span className={task.overdue ? 'due due-late' : 'due hint'}>
          {finished(task.status) ? `Finished ${formatDate(task.completed_at)}` : dueWords(task.due_at)}
        </span>
      </div>
      <h3>{task.title}</h3>
      {task.description && (
        <p className="prose">
          <Rich text={task.description} />
        </p>
      )}
      {task.status === 'blocked' && task.blocked_reason && (
        <p className="blocked">
          <strong>Blocked:</strong> {task.blocked_reason}
        </p>
      )}
      <Checklist incident={incident} task={task} />
      <Dependencies incident={incident} task={task} />
      {task.outcome && (
        <p className="decided">
          <strong>Decided:</strong> {task.outcome}
        </p>
      )}
      {task.outcomes.length > 0 && !finished(task.status) && incident.can_edit && (
        <Decide incident={incident} task={task} outcomes={outcomes} />
      )}

      {incident.can_edit && (
        <div className="actions">
          {canClaim && (
            <button className="btn btn-primary" type="button" onClick={() => save.run(() => api(`${base}/claim`, { method: 'POST', json: { version: task.version } }))}>
              Claim
            </button>
          )}
          {!finished(task.status) && !task.outcomes.length && (mine || !task.assignee || incident.can_manage) && (
            <button className="btn btn-quiet" type="button" onClick={() => patch({ status: 'done' })}>
              Mark done
            </button>
          )}
          <select
            className="input inline"
            aria-label={`Status of ${task.ref}`}
            value={task.status}
            onChange={(e) => (e.target.value === 'blocked' ? setBlocking(true) : patch({ status: e.target.value }))}
          >
            {statuses
              .filter((s) => s !== 'done' || !task.outcomes.length || task.status === 'done')
              .map((s) => (
                <option key={s} value={s}>
                  {taskStatusNames[s]}
                </option>
              ))}
          </select>
          <input
            className="input inline"
            type="datetime-local"
            aria-label={`Due date of ${task.ref}`}
            defaultValue={toLocalInput(task.due_at)}
            key={task.due_at ?? 'none'}
            onBlur={(e) => {
              const next = fromLocalInput(e.target.value);
              if (next !== task.due_at) patch({ due_at: next });
            }}
          />
          {canRelease && (
            <button className="link-button" type="button" onClick={() => save.run(() => api(`${base}/release`, { method: 'POST', json: { version: task.version } }))}>
              Return to queue
            </button>
          )}
          {canNudge && !nudging && (
            <button className="link-button" type="button" disabled={nudged} onClick={() => setNudging(true)}>
              {nudged ? 'Nudged' : 'Nudge'}
            </button>
          )}
          {!finished(task.status) && !waiting && (
            <button className="link-button" type="button" onClick={() => setWaiting(true)}>
              Waits on…
            </button>
          )}
          {!task.parent_id && !finished(task.status) && !adding && (
            <button className="link-button" type="button" onClick={() => setAdding(true)}>
              Add a subtask
            </button>
          )}
        </div>
      )}
      {waiting && (
        <form
          className="inline-form"
          onSubmit={async (e) => {
            e.preventDefault();
            const ref = String(new FormData(e.currentTarget).get('ref') ?? '').trim();
            if ((await save.run(() => api(`${base}/blocked-by`, { method: 'POST', json: { ref } }))) !== undefined) setWaiting(false);
          }}
        >
          <input
            className="input"
            name="ref"
            autoFocus
            required
            pattern="#?[Tt][Ss][Kk]-\d+"
            placeholder="TSK-12, in this incident or another"
            aria-label={`Task that ${task.ref} waits on`}
          />
          <button className="btn btn-primary" type="submit">
            Wait on it
          </button>
          <button className="btn btn-quiet" type="button" onClick={() => setWaiting(false)}>
            Cancel
          </button>
        </form>
      )}
      {blocking && (
        <form
          className="inline-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if ((await patch({ status: 'blocked', blocked_reason: note })) !== undefined) {
              setBlocking(false);
              setNote('');
            }
          }}
        >
          <input
            className="input"
            autoFocus
            required
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What is it waiting on?"
            aria-label={`Why ${task.ref} is blocked`}
          />
          <button className="btn btn-primary" type="submit">
            Mark blocked
          </button>
          <button className="btn btn-quiet" type="button" onClick={() => setBlocking(false)}>
            Cancel
          </button>
        </form>
      )}
      {nudging && (
        <form
          className="inline-form"
          onSubmit={async (e) => {
            e.preventDefault();
            // 204 has no body, so the change returns true itself; undefined means it failed
            const sent = await save.run(async () => {
              await api(`${base}/nudge`, { method: 'POST', json: { message: note } });
              return true;
            });
            setNudging(false);
            setNote('');
            if (sent !== undefined) setNudged(true);
          }}
        >
          <input
            className="input"
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={`A note for ${owner} (optional)`}
            aria-label={`Nudge about ${task.ref}`}
            maxLength={500}
          />
          <button className="btn btn-primary" type="submit">
            Send nudge
          </button>
          <button className="btn btn-quiet" type="button" onClick={() => setNudging(false)}>
            Cancel
          </button>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      {adding && <AddTask incident={incident} parent={task} onDone={() => setAdding(false)} />}
      {subtasks.length > 0 && (
        <div className="subtasks">
          <span className="label">
            Subtasks{' '}
            <span className="num">
              {subtasks.filter((t) => finished(t.status)).length}/{subtasks.length}
            </span>
          </span>
          <ul className="records" aria-label={`Subtasks of ${task.ref}`}>
            {subtasks.map((t) => (
              <TaskCard key={t.id} incident={incident} task={t} me={me} />
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function ApplyPlaybook({ incident }: { incident: Incident }) {
  const save = useSave(incident, ['tasks', 'playbooks']);
  const playbooks = useQuery({ queryKey: ['playbooks'], queryFn: () => api<Playbook[]>('/api/playbooks') });
  const applied = useSection<AppliedPlaybook[]>(incident, 'playbooks');
  const done = new Set(applied.data?.map((a) => a.playbook_id));
  const available = playbooks.data?.filter((p) => p.is_active && p.applies_to === incident.kind && !done.has(p.id)) ?? [];
  const waiting = applied.data?.flatMap((a) => a.steps).filter((st) => st.state === 'waiting').length ?? 0;
  return (
    <>
      {applied.data && applied.data.length > 0 && (
        <p className="hint">
          Applied:{' '}
          {applied.data.map((a, i) => (
            <span key={a.playbook_id}>
              {i > 0 && ', '}
              <strong>{a.name}</strong> by {a.applied_by?.name ?? 'someone'}, {formatDate(a.applied_at)}
            </span>
          ))}
          {waiting > 0 && ` · ${waiting} more step${waiting === 1 ? '' : 's'} to come`}
        </p>
      )}
      {incident.can_edit && available.length > 0 && (
        <select
          className="input inline"
          aria-label="Apply a playbook"
          value=""
          onChange={(e) => save.run(() => api(`/api/incidents/${incident.id}/playbooks/${e.target.value}`, { method: 'POST' }))}
        >
          <option value="">Apply a playbook…</option>
          {available.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.steps.length} tasks)
            </option>
          ))}
        </select>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </>
  );
}

export function Tasks({ incident, me }: { incident: Incident; me: Me }) {
  const tasks = useSection<Task[]>(incident, 'tasks');
  const applied = useSection<AppliedPlaybook[]>(incident, 'playbooks');
  const [adding, setAdding] = useState(false);
  const [show, setShow] = useState<'open' | 'finished'>('open');
  const [view, setView] = useState<'list' | 'workflow'>('list');
  // A decision's outcomes, with what each does, come from its playbook step
  const outcomesOf = new Map(
    (applied.data ?? []).flatMap((a) => a.steps.filter((st) => st.task_id).map((st) => [st.task_id as string, st.outcomes] as const)),
  );
  const all = sortTasks(tasks.data ?? []);
  const shown = all.filter((t) => (show === 'open') !== finished(t.status));
  // Subtasks sit under their parent, all of them, whichever tab the parent is in
  const ids = new Set(all.map((t) => t.id));
  const childrenOf = (id: string) => all.filter((t) => t.parent_id === id);
  const top = shown.filter((t) => !t.parent_id || !ids.has(t.parent_id));
  const overdue = all.filter((t) => t.overdue).length;

  return (
    <>
      <div className="section-bar">
        {applied.data && applied.data.length > 0 && (
          <div className="segmented" role="group" aria-label="View">
            <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>
              List
            </button>
            <button type="button" aria-pressed={view === 'workflow'} onClick={() => setView('workflow')}>
              Workflow
            </button>
          </div>
        )}
        <div className="segmented" role="group" aria-label="Show tasks">
          <button type="button" aria-pressed={show === 'open'} onClick={() => setShow('open')}>
            To do <span className="count num">{all.filter((t) => !finished(t.status)).length}</span>
          </button>
          <button type="button" aria-pressed={show === 'finished'} onClick={() => setShow('finished')}>
            Finished <span className="count num">{all.filter((t) => finished(t.status)).length}</span>
          </button>
        </div>
        <div className="actions">
          {overdue > 0 && <span className="due due-late">{overdue} overdue</span>}
          <ApplyPlaybook incident={incident} />
          {incident.can_edit && !adding && (
            <button className="btn btn-primary" type="button" onClick={() => setAdding(true)}>
              Add a task
            </button>
          )}
        </div>
      </div>
      {adding && <AddTask incident={incident} onDone={() => setAdding(false)} />}
      {view === 'workflow' &&
        applied.data?.map((a) => (
          <section key={a.playbook_id} className="panel" aria-label={`${a.name} workflow`}>
            <header>
              <h2>{a.name}</h2>
            </header>
            <Workflow
              incidentRef={incident.ref}
              steps={a.steps.map((st) => ({ ...st, taskRef: st.task_ref }))}
            />
          </section>
        ))}
      {tasks.data && shown.length === 0 && (
        <p className="hint">{show === 'open' ? 'Nothing left to do.' : 'Nothing finished yet.'}</p>
      )}
      <ul className="records" aria-label="Tasks" hidden={view === 'workflow'}>
        {top.map((t) => (
          <TaskCard
            key={t.id}
            incident={incident}
            task={t}
            subtasks={childrenOf(t.id)}
            outcomes={outcomesOf.get(t.id)}
            me={me}
          />
        ))}
      </ul>
    </>
  );
}
