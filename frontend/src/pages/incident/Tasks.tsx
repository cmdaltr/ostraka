import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { columnNames, flowSteps } from '../../api/flow';
import type { Flow, FlowColumn } from '../../api/flow';
import type { Incident } from '../../api/incidents';
import { ANY, defaultParts, dueWords, finished, partsOf, taskStatusNames } from '../../api/work';
import type { AppliedPlaybook, Playbook, Task, TaskLink, TaskStatus } from '../../api/work';
import type { Me, Person, Team } from '../../api/types';
import { PlaybookAnatomy, checksIn } from '../../components/PlaybookAnatomy';
import { Notice, formatDate } from '../../components/ui';
import { Watching, useRecordPresence } from '../../live/presence';
import { Rich, fromLocalInput, toLocalInput, useSave, useSection } from './shared';
import '../playbooks.css';

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
  // a subtask answers the same attack-flow step as its parent unless someone says otherwise
  const stage = parent?.stage;
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
          stage: form.get('stage') || null,
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
        {/* the attack-flow step it answers: its part of the drawing stays open until the task is done */}
        <label className="field">
          <span>Attack-flow step</span>
          <select id="task-stage" name="stage" defaultValue={stage ?? ''}>
            <option value="">None</option>
            {(Object.keys(columnNames) as FlowColumn[]).map((column) => (
              <optgroup key={column} label={columnNames[column]}>
                {flowSteps
                  .filter(([, , c]) => c === column)
                  .map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
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

function TaskCard({
  incident,
  task,
  subtasks = [],
  me,
}: {
  incident: Incident;
  task: Task;
  subtasks?: Task[];
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

      {incident.can_edit && (
        <div className="actions">
          {canClaim && (
            <button className="btn btn-primary" type="button" onClick={() => save.run(() => api(`${base}/claim`, { method: 'POST', json: { version: task.version } }))}>
              Claim
            </button>
          )}
          {!finished(task.status) && (mine || !task.assignee || incident.can_manage) && (
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
            {statuses.map((s) => (
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

// Applying a playbook: its parts drawn on the attack's anatomy, every part shown, the ones it has checks for
// chosen or not (red ochre), those with an "only if" left out until someone picks them
function ApplyPanel({
  incident,
  playbook,
  taken,
  onDone,
}: {
  incident: Incident;
  playbook: Playbook;
  taken: Set<string>;
  onDone: () => void;
}) {
  const save = useSave(incident, ['tasks', 'playbooks', 'flow', 'activity']);
  const flow = useSection<Flow>(incident, 'flow');
  const open = [...partsOf(playbook)].filter((p) => !taken.has(p));
  const [picked, setPicked] = useState<Set<string>>(() => new Set(open.filter((p) => defaultParts(playbook).has(p))));
  const toggle = (key: string) => {
    if (!open.includes(key)) return;
    const next = new Set(picked);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setPicked(next);
  };
  const seen = new Set((flow.data?.stages ?? []).filter((st) => st.layer === 'seen').map((st) => st.key));
  const count = (parts: string[]) => parts.reduce((n, p) => n + checksIn(playbook, p), 0);
  const chosen = open.filter((p) => picked.has(p));
  const attack = chosen.filter((p) => p !== ANY);
  const left = open.filter((p) => !picked.has(p) && p !== ANY);
  const loose = checksIn(playbook, ANY);
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

  async function apply() {
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/playbooks/${playbook.id}`, { method: 'POST', json: { parts: chosen } }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <section className="panel pb-apply" aria-label={`Apply ${playbook.name}`}>
      <header>
        <h2>
          Apply {playbook.name} to {incident.ref}
        </h2>
        <p className="hint">
          Select the parts that fit this record; what you select is in red ochre. Parts with an “only if” start unselected, and
          dashed parts have no checks in this playbook. Each check becomes a task on that part of the record’s attack flow.
        </p>
      </header>
      <div className="actions" role="group" aria-label="Select parts">
        <button className="btn btn-quiet" type="button" onClick={() => setPicked(new Set(open))} disabled={chosen.length === open.length}>
          Select all
        </button>
        <button className="btn btn-quiet" type="button" onClick={() => setPicked(new Set())} disabled={!chosen.length}>
          Select none
        </button>
      </div>
      <PlaybookAnatomy
        playbook={playbook}
        mode="apply"
        chosen={(key) => picked.has(key)}
        choose={toggle}
        taken={taken}
        seen={seen}
        label={`${playbook.name}: the parts to apply`}
      />
      {loose > 0 && open.includes(ANY) && (
        <label className="check">
          <input type="checkbox" checked={picked.has(ANY)} onChange={() => toggle(ANY)} />
          <span>The {plural(loose, 'check')} not tied to a step</span>
        </label>
      )}
      <p>
        {chosen.length ? (
          <>
            Adds <strong>{plural(count(chosen), 'task')}</strong>
            {attack.length > 0 && <> for {plural(attack.length, 'part')} of the attack</>}
            {picked.has(ANY) && <>{attack.length ? ', including' : ':'} the {plural(loose, 'check')} not tied to a step</>}.
          </>
        ) : (
          'Select at least one part.'
        )}
      </p>
      {left.length > 0 && (
        <div>
          <p className="hint">Left out for now, to apply later from here:</p>
          <ul className="pb-left">
            {left.map((p) => (
              <li key={p}>
                {flowSteps.find(([k]) => k === p)?.[1] ?? p}
                {playbook.conditions[p] && <span className="hint"> (only if {playbook.conditions[p]})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="button" disabled={!chosen.length} onClick={apply}>
          Apply
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function ApplyPlaybook({ incident }: { incident: Incident }) {
  const playbooks = useQuery({ queryKey: ['playbooks'], queryFn: () => api<Playbook[]>('/api/playbooks') });
  const applied = useSection<AppliedPlaybook[]>(incident, 'playbooks');
  const [applying, setApplying] = useState<string | null>(null);
  const runOf = (id: string) => applied.data?.find((a) => a.playbook_id === id);
  // a playbook not applied yet, or applied with parts still to take
  const available =
    playbooks.data?.filter((p) => p.is_active && p.applies_to === incident.kind && (!runOf(p.id) || runOf(p.id)!.remaining.length)) ?? [];
  const playbook = playbooks.data?.find((p) => p.id === applying);
  const name = (key: string) => (key === ANY ? 'not tied to a step' : (flowSteps.find(([k]) => k === key)?.[1] ?? key));
  return (
    <>
      {applied.data && applied.data.length > 0 && (
        <p className="hint pb-applied">
          Applied:{' '}
          {applied.data.map((a, i) => (
            <span key={a.playbook_id}>
              {i > 0 && '; '}
              <strong>{a.name}</strong> by {a.applied_by?.name ?? 'someone'}, {formatDate(a.applied_at)}
              {a.remaining.length > 0 && (
                <>
                  {' '}
                  ({a.remaining.length} part{a.remaining.length === 1 ? '' : 's'} left: {a.remaining.map(name).join(', ')}
                  {incident.can_edit && (
                    <>
                      {' · '}
                      <button className="link-button" type="button" onClick={() => setApplying(a.playbook_id)}>
                        apply more
                      </button>
                    </>
                  )}
                  )
                </>
              )}
            </span>
          ))}
        </p>
      )}
      {incident.can_edit && available.length > 0 && (
        <select className="input inline" aria-label="Apply a playbook" value="" onChange={(e) => setApplying(e.target.value)}>
          <option value="">Apply a playbook…</option>
          {available.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {runOf(p.id) ? ' (the rest)' : ''}
            </option>
          ))}
        </select>
      )}
      {playbook && (
        <ApplyPanel
          key={playbook.id}
          incident={incident}
          playbook={playbook}
          taken={new Set(runOf(playbook.id)?.parts ?? [])}
          onDone={() => setApplying(null)}
        />
      )}
    </>
  );
}

export function Tasks({ incident, me }: { incident: Incident; me: Me }) {
  const tasks = useSection<Task[]>(incident, 'tasks');
  const [adding, setAdding] = useState(false);
  const [show, setShow] = useState<'open' | 'finished'>('open');
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
      {tasks.data && shown.length === 0 && (
        <p className="hint">{show === 'open' ? 'Nothing left to do.' : 'Nothing finished yet.'}</p>
      )}
      <ul className="records" aria-label="Tasks">
        {top.map((t) => (
          <TaskCard
            key={t.id}
            incident={incident}
            task={t}
            subtasks={childrenOf(t.id)}
            me={me}
          />
        ))}
      </ul>
    </>
  );
}
