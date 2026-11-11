// Regulator clocks: each regime's deadlines counting down from awareness, with an owner, and each
// milestone marked met with a reference. The server works out the deadlines; this counts down to them.

import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { basisNames, countdownWords, gone, stateAt, stateNames, unitNames } from '../../api/clocks';
import type { Basis, Clock, ClockMilestone, ClockState, CustomMilestone, Regime, RegimeKey, Unit } from '../../api/clocks';
import type { Incident } from '../../api/incidents';
import type { Me, Person } from '../../api/types';
import { Notice, formatDate } from '../../components/ui';
import { Rich, fromLocalInput, toLocalInput, useSave, useSection } from './shared';
import './clocks.css';

export function useNow(every = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(timer);
  }, [every]);
  return now;
}

function useRegimes() {
  return useQuery({ queryKey: ['clock-regimes'], queryFn: () => api<Regime[]>('/api/clocks/regimes'), staleTime: Infinity });
}

function useDirectory() {
  return useQuery({ queryKey: ['directory'], queryFn: () => api<Person[]>('/api/users') });
}

// Who may own a clock: anyone who can see the incident and isn't an observer
function owners(incident: Incident, people: Person[]): Person[] {
  const teams = new Set(incident.teams.map((t) => t.id));
  const named = new Set(incident.members.map((m) => m.user.id));
  return people.filter(
    (p) => p.is_active && p.role !== 'observer' && (p.role === 'admin' || named.has(p.id) || (p.team && teams.has(p.team.id))),
  );
}

export function mayManageClocks(incident: Incident, me: Me): boolean {
  if (me.role === 'observer' || !incident.can_edit || incident.kind !== 'incident') return false;
  if (me.role === 'admin') return true;
  const role = incident.members.find((m) => m.user.id === me.id)?.role;
  return role === 'commander' || role === 'comms';
}

const worst: ClockState[] = ['breached', 'closing', 'near', 'running', 'waiting', 'met_late', 'met'];

function OwnerSelect({ incident, value, me }: { incident: Incident; value?: string; me: Me }) {
  const directory = useDirectory();
  const choices = owners(incident, directory.data ?? []);
  return (
    <label className="field">
      <span>Owner</span>
      <select name="owner_id" defaultValue={value ?? me.id} key={choices.length}>
        {!choices.some((p) => p.id === (value ?? me.id)) && <option value={value ?? me.id}>{value ? 'Current owner' : me.name}</option>}
        {choices.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function MilestoneEditor({ value, onChange }: { value: CustomMilestone[]; onChange: (v: CustomMilestone[]) => void }) {
  const set = (i: number, patch: Partial<CustomMilestone>) => onChange(value.map((m, j) => (i === j ? { ...m, ...patch } : m)));
  return (
    <fieldset className="clock-milestones-edit">
      <legend>Milestones</legend>
      {value.map((m, i) => (
        <div className="form-row" key={i}>
          <label className="field">
            <span>Name</span>
            <input value={m.name} required maxLength={120} onChange={(e) => set(i, { name: e.target.value })} aria-label={`Milestone ${i + 1} name`} />
          </label>
          <label className="field">
            <span>Within</span>
            <input
              type="number"
              min={1}
              value={m.amount}
              required
              onChange={(e) => set(i, { amount: Number(e.target.value) })}
              aria-label={`Milestone ${i + 1} amount`}
            />
          </label>
          <label className="field">
            <span>Unit</span>
            <select value={m.unit} onChange={(e) => set(i, { unit: e.target.value as Unit })} aria-label={`Milestone ${i + 1} unit`}>
              {(Object.keys(unitNames) as Unit[]).map((u) => (
                <option key={u} value={u}>
                  {unitNames[u]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Counted</span>
            <select value={m.basis} onChange={(e) => set(i, { basis: e.target.value as Basis })} aria-label={`Milestone ${i + 1} counted`}>
              {(Object.keys(basisNames) as Basis[])
                .filter((b) => i > 0 || b !== 'previous')
                .map((b) => (
                  <option key={b} value={b}>
                    {basisNames[b]}
                  </option>
                ))}
            </select>
          </label>
          {value.length > 1 && (
            <button className="btn btn-quiet" type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}>
              Remove
            </button>
          )}
        </div>
      ))}
      {value.length < 10 && (
        <button
          className="btn btn-quiet"
          type="button"
          onClick={() => onChange([...value, { name: '', amount: 24, unit: 'hours', basis: value.length ? 'previous' : 'aware' }])}
        >
          Add a milestone
        </button>
      )}
    </fieldset>
  );
}

function AddClock({ incident, me, onDone }: { incident: Incident; me: Me; onDone: () => void }) {
  const save = useSave(incident, ['clocks']);
  const regimes = useRegimes();
  const [key, setKey] = useState<RegimeKey>('uk_gdpr');
  const [custom, setCustom] = useState<CustomMilestone[]>([{ name: '', amount: 48, unit: 'hours', basis: 'aware' }]);
  const regime = regimes.data?.find((r) => r.key === key);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/clocks`, {
        method: 'POST',
        json: {
          regime: key,
          title: form.get('title') ?? '',
          owner_id: form.get('owner_id'),
          classified_at: fromLocalInput(form.get('classified_at')),
          milestones: regime?.custom ? custom : [],
        },
      }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <form className="panel" onSubmit={submit} aria-label="Start a regulator clock">
      <header>
        <h2>Start a regulator clock</h2>
        <p>
          Deadlines count from when you became aware ({formatDate(incident.aware_at)}), or from classification where the regime
          says so.
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Regime</span>
          <select name="regime" value={key} onChange={(e) => setKey(e.target.value as RegimeKey)}>
            {(regimes.data ?? []).map((r) => (
              <option key={r.key} value={r.key}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <OwnerSelect incident={incident} me={me} />
        {regime?.needs_classification && (
          <label className="field">
            <span>Classified as major</span>
            <input type="datetime-local" name="classified_at" />
          </label>
        )}
      </div>
      {regime && !regime.custom && (
        <ul className="clock-rules">
          {regime.milestones.map((m) => (
            <li key={m.key}>
              <strong>{m.name}</strong> <span className="hint">{m.counted}</span>
            </li>
          ))}
          <li className="hint">{regime.source}</li>
        </ul>
      )}
      <label className="field">
        <span>{regime?.custom ? 'Whose deadline' : 'Note (optional)'}</span>
        <input
          name="title"
          maxLength={200}
          required={regime?.custom}
          placeholder={regime?.custom ? 'Cyber insurer, a contract, the board' : 'What it covers'}
        />
      </label>
      {regime?.custom && <MilestoneEditor value={custom} onChange={setCustom} />}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Start the clock
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function EditClock({ clock, incident, me, onDone }: { clock: Clock; incident: Incident; me: Me; onDone: () => void }) {
  const save = useSave(incident, ['clocks']);
  const [custom, setCustom] = useState<CustomMilestone[]>(clock.custom_milestones);
  const isCustom = clock.regime === 'custom';

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {
      version: clock.version,
      title: form.get('title'),
      owner_id: form.get('owner_id'),
      starts_at: fromLocalInput(form.get('starts_at')),
    };
    if (clock.needs_classification) body.classified_at = fromLocalInput(form.get('classified_at'));
    if (isCustom) body.milestones = custom;
    const done = await save.run(() => api(`/api/incidents/${incident.id}/clocks/${clock.id}`, { method: 'PATCH', json: body }));
    if (done !== undefined) onDone();
  }

  return (
    <form className="clock-edit" onSubmit={submit} aria-label={`Change ${clock.ref}`}>
      <div className="form-row">
        <OwnerSelect incident={incident} me={me} value={clock.owner?.id} />
        <label className="field">
          <span>{isCustom ? 'Whose deadline' : 'Note'}</span>
          <input name="title" defaultValue={clock.title} maxLength={200} required={isCustom} />
        </label>
        <label className="field">
          <span>Aware from (blank: the incident’s)</span>
          <input type="datetime-local" name="starts_at" defaultValue={toLocalInput(clock.starts_at)} />
        </label>
        {clock.needs_classification && (
          <label className="field">
            <span>Classified as major</span>
            <input type="datetime-local" name="classified_at" defaultValue={toLocalInput(clock.classified_at)} />
          </label>
        )}
      </div>
      {isCustom && <MilestoneEditor value={custom} onChange={setCustom} />}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Save
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function MarkMet({ clock, milestone, incident, onDone }: { clock: Clock; milestone: ClockMilestone; incident: Incident; onDone: () => void }) {
  const save = useSave(incident, ['clocks']);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/clocks/${clock.id}/milestones/${milestone.key}/met`, {
        method: 'POST',
        json: {
          version: clock.version,
          reference: form.get('reference'),
          note: form.get('note'),
          met_at: fromLocalInput(form.get('met_at')),
        },
      }),
    );
    if (done !== undefined) onDone();
  }
  return (
    <form className="clock-edit" onSubmit={submit} aria-label={`Mark ${milestone.name} met`}>
      <div className="form-row">
        <label className="field">
          <span>Reference</span>
          <input name="reference" maxLength={200} placeholder="The regulator’s case number" autoFocus />
        </label>
        <label className="field">
          <span>Submitted (blank: now)</span>
          <input type="datetime-local" name="met_at" />
        </label>
      </div>
      <label className="field">
        <span>Note</span>
        <textarea className="input" name="note" rows={2} />
      </label>
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Mark met
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Milestone({ clock, m, incident, now }: { clock: Clock; m: ClockMilestone; incident: Incident; now: number }) {
  const [marking, setMarking] = useState(false);
  const save = useSave(incident, ['clocks']);
  const state = clock.closed_at && !m.met_at ? 'waiting' : stateAt(m, now);
  const live = !clock.closed_at && !m.met_at && m.due_at;
  return (
    <li className={`clock-milestone clock-${state}`} data-state={state}>
      <div className="record-head">
        <strong>{m.name}</strong>
        <span className={`clock-state clock-state-${state}`}>{clock.closed_at && !m.met_at ? 'Stopped' : stateNames[state]}</span>
        {live && (
          <span className="clock-countdown mono num" role="timer" aria-label={`${m.name} countdown`}>
            {countdownWords(m.due_at!, now)}
          </span>
        )}
      </div>
      {live && (
        <div className="clock-bar" aria-hidden>
          <span style={{ width: `${Math.round(gone(m, now) * 100)}%` }} />
        </div>
      )}
      <p className="hint">
        {m.due_at ? `Due ${formatDate(m.due_at)}: ` : ''}
        {m.counted}
      </p>
      {m.met_at && (
        <p>
          Met {formatDate(m.met_at)} by {m.met_by?.name ?? 'a former member'}
          {m.reference && (
            <>
              {' '}
              · <span className="mono">{m.reference}</span>
            </>
          )}
          {m.note && (
            <>
              <br />
              <Rich text={m.note} />
            </>
          )}
        </p>
      )}
      {clock.can_mark &&
        (marking ? (
          <MarkMet clock={clock} milestone={m} incident={incident} onDone={() => setMarking(false)} />
        ) : (
          <div className="actions">
            {!m.met_at ? (
              <button className="btn" type="button" onClick={() => setMarking(true)}>
                Mark met
              </button>
            ) : (
              <button
                className="btn btn-quiet"
                type="button"
                onClick={() =>
                  save.run(() =>
                    api(`/api/incidents/${incident.id}/clocks/${clock.id}/milestones/${m.key}/unmet`, {
                      method: 'POST',
                      json: { version: clock.version },
                    }),
                  )
                }
              >
                Undo met
              </button>
            )}
          </div>
        ))}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </li>
  );
}

function CloseClock({ clock, incident, onDone }: { clock: Clock; incident: Incident; onDone: () => void }) {
  const save = useSave(incident, ['clocks']);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = new FormData(event.currentTarget).get('reason');
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/clocks/${clock.id}/close`, { method: 'POST', json: { version: clock.version, reason } }),
    );
    if (done !== undefined) onDone();
  }
  return (
    <form className="clock-edit" onSubmit={submit} aria-label={`Close ${clock.ref}`}>
      <label className="field">
        <span>Why it stops</span>
        <input name="reason" required minLength={3} maxLength={2000} placeholder="Not reportable: …, or every report is in" autoFocus />
      </label>
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Close the clock
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ClockRecord({ clock, incident, me, now }: { clock: Clock; incident: Incident; me: Me; now: number }) {
  const [mode, setMode] = useState<'view' | 'edit' | 'close'>('view');
  const save = useSave(incident, ['clocks']);
  const states = clock.milestones.map((m) => stateAt(m, now));
  const overall = clock.closed_at ? null : worst.find((s) => states.includes(s));
  return (
    <article className={`record clock clock-${overall ?? 'waiting'}${clock.closed_at ? ' clock-closed' : ''}`} id={clock.ref}>
      <div className="record-head">
        <span className="mono ref">{clock.ref}</span>
        <h3>{clock.regime_name}</h3>
        {clock.title && <span>{clock.title}</span>}
        {overall && <span className={`clock-state clock-state-${overall}`}>{stateNames[overall]}</span>}
        {clock.closed_at && <span className="chip">Closed</span>}
      </div>
      <p className="hint">
        {clock.authority && `Told: ${clock.authority}. `}Owner: {clock.owner?.name ?? 'nobody'}. Aware from {formatDate(clock.aware_at)}
        {clock.classified_at && `, classified ${formatDate(clock.classified_at)}`}
        {clock.needs_classification && !clock.classified_at && ', not classified yet'}.
      </p>
      {clock.closed_at && (
        <p className="hint">
          Closed {formatDate(clock.closed_at)} by {clock.closed_by?.name}: <Rich text={clock.close_reason} />
        </p>
      )}
      <ol className="clock-milestones">
        {clock.milestones.map((m) => (
          <Milestone key={m.key} clock={clock} m={m} incident={incident} now={now} />
        ))}
      </ol>
      {mode === 'edit' && <EditClock clock={clock} incident={incident} me={me} onDone={() => setMode('view')} />}
      {mode === 'close' && <CloseClock clock={clock} incident={incident} onDone={() => setMode('view')} />}
      {clock.can_manage && mode === 'view' && (
        <div className="actions">
          {!clock.closed_at ? (
            <>
              <button className="btn btn-quiet" type="button" onClick={() => setMode('edit')}>
                Change
              </button>
              <button className="btn btn-quiet" type="button" onClick={() => setMode('close')}>
                Close
              </button>
            </>
          ) : (
            <button
              className="btn btn-quiet"
              type="button"
              onClick={() =>
                save.run(() =>
                  api(`/api/incidents/${incident.id}/clocks/${clock.id}/reopen`, { method: 'POST', json: { version: clock.version } }),
                )
              }
            >
              Reopen
            </button>
          )}
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </article>
  );
}

export function Clocks({ incident, me }: { incident: Incident; me: Me }) {
  const clocks = useSection<Clock[]>(incident, 'clocks');
  const now = useNow();
  const [adding, setAdding] = useState(false);
  const all = clocks.data ?? [];
  const running = all.filter((c) => !c.closed_at);
  const closed = all.filter((c) => c.closed_at);
  const manage = mayManageClocks(incident, me);
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Regulator clocks</h2>
          {manage && !adding && (
            <button className="btn" type="button" onClick={() => setAdding(true)}>
              Start a clock
            </button>
          )}
        </header>
        {clocks.error && <Notice kind="error">{String(clocks.error)}</Notice>}
        {clocks.data && running.length === 0 && (
          <p className="hint">
            No clocks running.{' '}
            {incident.kind === 'incident'
              ? 'The commander or comms starts one when a regulator, insurer or contract needs telling.'
              : 'Hunts don’t take regulator clocks.'}
          </p>
        )}
        {running.map((c) => (
          <ClockRecord key={c.id} clock={c} incident={incident} me={me} now={now} />
        ))}
      </section>
      {adding && <AddClock incident={incident} me={me} onDone={() => setAdding(false)} />}
      {closed.length > 0 && (
        <section className="panel">
          <header className="panel-head">
            <h2>Closed</h2>
          </header>
          {closed.map((c) => (
            <ClockRecord key={c.id} clock={c} incident={incident} me={me} now={now} />
          ))}
        </section>
      )}
    </>
  );
}
