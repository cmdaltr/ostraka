import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api } from '../api/client';
import type { Outcome, OutcomeAction, Playbook } from '../api/work';
import type { Me, Team } from '../api/types';
import { phasesOf } from '../brand';
import type { RecordKind } from '../brand';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, messageOf } from '../components/ui';
import { Workflow } from '../components/Workflow';
import { splitTags } from './incident/shared';
import './incident/incident.css';

interface Draft {
  title: string;
  description: string;
  team_id: string;
  due_minutes: string;
  checklist: string;
  after: number[];
  phase: string;
  outcomes: Outcome[];
}

const blank: Draft = { title: '', description: '', team_id: '', due_minutes: '', checklist: '', after: [], phase: '', outcomes: [] };

const actionNames: Record<OutcomeAction, string> = {
  none: 'Nothing else',
  close: 'Close the record',
  phase: 'Move to a phase',
  promote: 'Promote to an incident',
};

// Steps refer to each other by position, so moving or removing one renumbers the references
function renumber(steps: Draft[], map: (i: number) => number | null): Draft[] {
  const fix = (list: number[]) => list.map(map).filter((n): n is number => n !== null);
  return steps.map((s) => ({ ...s, after: fix(s.after), outcomes: s.outcomes.map((o) => ({ ...o, opens: fix(o.opens) })) }));
}

function toDrafts(playbook: Playbook | null): Draft[] {
  if (!playbook) return [{ ...blank }];
  return playbook.steps.map((s) => ({
    title: s.title,
    description: s.description,
    team_id: s.team?.id ?? '',
    due_minutes: s.due_minutes === null ? '' : String(s.due_minutes),
    checklist: s.checklist.join('\n'),
    after: s.after,
    phase: s.phase ?? '',
    outcomes: s.outcomes,
  }));
}

function Editor({ playbook, teams, onDone }: { playbook: Playbook | null; teams: Team[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [steps, setSteps] = useState<Draft[]>(() => toDrafts(playbook));
  const [kind, setKind] = useState<RecordKind>(playbook?.applies_to ?? 'incident');
  const [error, setError] = useState<string | null>(null);

  const change = <K extends keyof Draft>(i: number, field: K, value: Draft[K]) =>
    setSteps(steps.map((s, j) => (j === i ? { ...s, [field]: value } : s)));
  const move = (i: number, by: number) => {
    const next = [...steps];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    // A step can only follow earlier ones: references that would point forward after the swap go
    const swapped = renumber(next, (n) => (n === i ? i + by : n === i + by ? i : n));
    setSteps(swapped.map((s, j) => ({ ...s, after: s.after.filter((a) => a < j) })));
  };
  const remove = (i: number) =>
    setSteps(renumber(steps.filter((_, j) => j !== i), (n) => (n === i ? null : n > i ? n - 1 : n)));
  const toggle = (list: number[], n: number, on: boolean) => (on ? [...list, n].sort((a, b) => a - b) : list.filter((x) => x !== n));
  const setOutcome = (i: number, k: number, patch: Partial<Outcome>) =>
    change(i, 'outcomes', steps[i].outcomes.map((o, j) => (j === k ? { ...o, ...patch } : o)));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const json = {
      name: form.get('name'),
      description: form.get('description'),
      trigger_tags: splitTags(String(form.get('trigger_tags') ?? '')),
      applies_to: form.get('applies_to'),
      is_active: form.get('is_active') === 'on',
      steps: steps.map((s) => ({
        title: s.title,
        description: s.description,
        team_id: s.team_id || null,
        due_minutes: s.due_minutes === '' ? null : Number(s.due_minutes),
        checklist: s.checklist.split('\n'),
        after: s.after,
        phase: s.phase || null,
        outcomes: s.outcomes.map((o) => ({ ...o, phase: o.action === 'phase' ? o.phase : null })),
      })),
    };
    try {
      if (playbook) await api(`/api/playbooks/${playbook.id}`, { method: 'PATCH', json: { version: playbook.version, ...json } });
      else await api('/api/playbooks', { method: 'POST', json });
      await queryClient.invalidateQueries({ queryKey: ['playbooks'] });
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) queryClient.invalidateQueries({ queryKey: ['playbooks'] });
      setError(messageOf(e));
    }
  }

  return (
    <form className="panel" onSubmit={submit} aria-label={playbook ? `Edit ${playbook.name}` : 'New playbook'}>
      <header>
        <h2>{playbook ? `Edit ${playbook.name}` : 'New playbook'}</h2>
        <p>
          Changes apply the next time it’s used; records already following it keep the version they started with.
          A step opens when the steps it follows have finished and the record has reached its phase. A decision opens
          the steps its outcome names.
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Name</span>
          <input id="playbook-name" name="name" defaultValue={playbook?.name} required minLength={3} maxLength={120} />
        </label>
        <label className="field">
          <span>For</span>
          <select id="playbook-for" name="applies_to" value={kind} onChange={(e) => setKind(e.target.value as RecordKind)}>
            <option value="incident">Incidents</option>
            <option value="hunt">Hunts</option>
          </select>
        </label>
        <label className="field">
          <span>Suggested for tags</span>
          <input id="playbook-tags" name="trigger_tags" defaultValue={playbook?.trigger_tags.join(', ')} placeholder="phishing, bec" />
        </label>
      </div>
      <label className="field">
        <span>What it’s for</span>
        <textarea className="input" name="description" rows={2} defaultValue={playbook?.description} />
      </label>
      <label className="check">
        <input type="checkbox" name="is_active" defaultChecked={playbook?.is_active ?? true} />
        <span>In use: offered when declaring an incident or starting a hunt</span>
      </label>

      <ol className="steps" aria-label="Steps">
        {steps.map((step, i) => (
          <li key={i} className="record">
            <div className="record-head">
              <span className="label">Task {i + 1}</span>
              <span className="actions">
                <button className="link-button" type="button" disabled={i === 0} onClick={() => move(i, -1)}>
                  Up
                </button>
                <button className="link-button" type="button" disabled={i === steps.length - 1} onClick={() => move(i, 1)}>
                  Down
                </button>
                <button className="link-button" type="button" disabled={steps.length === 1} onClick={() => remove(i)}>
                  Remove
                </button>
              </span>
            </div>
            <div className="form-row">
              <label className="field">
                <span>Task</span>
                <input
                  aria-label={`Task ${i + 1} title`}
                  value={step.title}
                  required
                  minLength={3}
                  onChange={(e) => change(i, 'title', e.target.value)}
                />
              </label>
              <label className="field">
                <span>For</span>
                <select aria-label={`Task ${i + 1} team`} value={step.team_id} onChange={(e) => change(i, 'team_id', e.target.value)}>
                  <option value="">Whoever applies it</option>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}’s queue
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Due after (minutes)</span>
                <input
                  aria-label={`Task ${i + 1} due after`}
                  type="number"
                  min={0}
                  value={step.due_minutes}
                  onChange={(e) => change(i, 'due_minutes', e.target.value)}
                />
              </label>
            </div>
            <label className="field">
              <span>Checklist, one item per line</span>
              <textarea className="input" rows={2} value={step.checklist} onChange={(e) => change(i, 'checklist', e.target.value)} />
            </label>
            <div className="form-row">
              <label className="field">
                <span>Opens at phase</span>
                <select aria-label={`Task ${i + 1} phase`} value={step.phase} onChange={(e) => change(i, 'phase', e.target.value)}>
                  <option value="">Any phase</option>
                  {phasesOf(kind).map((ph) => (
                    <option key={ph} value={ph}>
                      {ph}
                    </option>
                  ))}
                </select>
              </label>
              {i > 0 && (
                <fieldset className="field team-picks">
                  <span>After</span>
                  <div className="actions">
                    {steps.slice(0, i).map((other, j) => (
                      <label key={j} className="check">
                        <input
                          type="checkbox"
                          aria-label={`Task ${i + 1} after task ${j + 1}`}
                          checked={step.after.includes(j)}
                          onChange={(e) => change(i, 'after', toggle(step.after, j, e.target.checked))}
                        />
                        <span>
                          {j + 1}. {other.title || 'untitled'}
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
            </div>
            {step.outcomes.length > 0 && (
              <fieldset className="field outcomes" aria-label={`Task ${i + 1} outcomes`}>
                <span>A decision: it’s finished by choosing one of these</span>
                {step.outcomes.map((o, k) => (
                  <div key={k} className="outcome-row">
                    <div className="form-row">
                      <input
                        aria-label={`Task ${i + 1} outcome ${k + 1}`}
                        value={o.label}
                        required
                        placeholder="Confirmed: promote to an incident"
                        onChange={(e) => setOutcome(i, k, { label: e.target.value })}
                      />
                      <select
                        aria-label={`Task ${i + 1} outcome ${k + 1} action`}
                        value={o.action}
                        onChange={(e) => setOutcome(i, k, { action: e.target.value as OutcomeAction })}
                      >
                        {(Object.keys(actionNames) as OutcomeAction[])
                          .filter((a) => a !== 'promote' || kind === 'hunt')
                          .map((a) => (
                            <option key={a} value={a}>
                              {actionNames[a]}
                            </option>
                          ))}
                      </select>
                      {o.action === 'phase' && (
                        <select
                          aria-label={`Task ${i + 1} outcome ${k + 1} phase`}
                          value={o.phase ?? ''}
                          required
                          onChange={(e) => setOutcome(i, k, { phase: e.target.value })}
                        >
                          <option value="" disabled>
                            Phase…
                          </option>
                          {phasesOf(kind).map((ph) => (
                            <option key={ph} value={ph}>
                              {ph}
                            </option>
                          ))}
                        </select>
                      )}
                      <button
                        className="link-button"
                        type="button"
                        onClick={() => change(i, 'outcomes', step.outcomes.filter((_, j) => j !== k))}
                      >
                        Remove
                      </button>
                    </div>
                    <div className="actions">
                      <span className="hint">Opens</span>
                      {steps.map((other, j) =>
                        j === i ? null : (
                          <label key={j} className="check">
                            <input
                              type="checkbox"
                              aria-label={`Outcome ${o.label || k + 1} opens task ${j + 1}`}
                              checked={o.opens.includes(j)}
                              onChange={(e) => setOutcome(i, k, { opens: toggle(o.opens, j, e.target.checked) })}
                            />
                            <span>
                              {j + 1}. {other.title || 'untitled'}
                              {j < i && <span className="hint"> (again)</span>}
                            </span>
                          </label>
                        ),
                      )}
                    </div>
                  </div>
                ))}
              </fieldset>
            )}
            <button
              className="link-button"
              type="button"
              onClick={() => change(i, 'outcomes', [...step.outcomes, { label: '', action: 'none', phase: null, opens: [] }])}
            >
              {step.outcomes.length ? 'Add another outcome' : 'Make it a decision'}
            </button>
          </li>
        ))}
      </ol>
      <div className="actions">
        <button className="btn btn-quiet" type="button" onClick={() => setSteps([...steps, { ...blank }])}>
          Add a task
        </button>
      </div>
      <div className="field">
        <span>How it flows</span>
        <Workflow
          steps={steps.map((s) => ({
            title: s.title || 'untitled',
            team: teams.find((t) => t.id === s.team_id) ?? null,
            after: s.after,
            phase: s.phase || null,
            outcomes: s.outcomes,
          }))}
        />
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          {playbook ? 'Save' : 'Create playbook'}
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Card({ playbook, onEdit }: { playbook: Playbook; onEdit: () => void }) {
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <li className="record">
      <div className="record-head">
        <strong>{playbook.name}</strong>
        {playbook.applies_to === 'hunt' && <span className="chip chip-accent">Hunts</span>}
        {!playbook.is_active && <span className="chip">Retired</span>}
        {playbook.trigger_tags.map((t) => (
          <span key={t} className="chip">
            {t}
          </span>
        ))}
      </div>
      {playbook.description && <p className="prose hint">{playbook.description}</p>}
      <Workflow steps={playbook.steps} />
      <div className="actions">
        <button className="btn btn-quiet" type="button" onClick={onEdit}>
          Edit
        </button>
        {deleting ? (
          <>
            <button
              className="btn btn-primary"
              type="button"
              onClick={() =>
                api(`/api/playbooks/${playbook.id}`, { method: 'DELETE' })
                  .then(() => queryClient.invalidateQueries({ queryKey: ['playbooks'] }))
                  .catch((e) => setError(messageOf(e)))
              }
            >
              Delete {playbook.name}
            </button>
            <button className="link-button" type="button" onClick={() => setDeleting(false)}>
              Keep it
            </button>
          </>
        ) : (
          <button className="link-button" type="button" onClick={() => setDeleting(true)}>
            Delete…
          </button>
        )}
      </div>
      {error && <Notice kind="error">{error}</Notice>}
    </li>
  );
}

export function Playbooks({ me }: { me: Me }) {
  const playbooks = useQuery({ queryKey: ['playbooks'], queryFn: () => api<Playbook[]>('/api/playbooks') });
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams') });
  const [editing, setEditing] = useState<Playbook | 'new' | null>(null);
  const writes = me.role === 'admin' || me.role === 'team_lead';

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Playbooks</h1>
          <p>
            How work moves between teams on an incident or a hunt: who does what, what waits for what, and where a
            decision sends it next. A playbook’s tags suggest it when someone declares an incident or starts a hunt.
          </p>
        </div>
        {writes && !editing && (
          <button className="btn btn-primary" type="button" onClick={() => setEditing('new')}>
            New playbook
          </button>
        )}
      </div>
      {editing && (
        <Editor
          key={editing === 'new' ? 'new' : editing.id}
          playbook={editing === 'new' ? null : editing}
          teams={teams.data ?? []}
          onDone={() => setEditing(null)}
        />
      )}
      {playbooks.error && <Notice kind="error">{messageOf(playbooks.error)}</Notice>}
      <Loading ready={!playbooks.isPending} label="Loading playbooks">
        {playbooks.data?.length === 0 && !editing && <p className="hint">No playbooks yet.</p>}
        <ul className="records" aria-label="Playbooks">
          {playbooks.data?.map((p) => (
            <Card key={p.id} playbook={p} onEdit={() => setEditing(p)} />
          ))}
        </ul>
      </Loading>
    </>
  );
}
