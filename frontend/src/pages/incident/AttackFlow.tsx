import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { columnNames } from '../../api/flow';
import type { Flow, FlowColumn, FlowStage, StageCall } from '../../api/flow';
import type { Incident } from '../../api/incidents';
import { Anatomy } from '../../components/Anatomy';
import { Notice, formatDate } from '../../components/ui';
import { refTarget, useSave, useSection } from './shared';

const COVERAGE_KEY = 'ostraka.flow.coverage';
const coverageNames = { covered: 'Covered', focused: 'Being worked', open: 'To check' } as const;
const layerNames = { seen: 'Seen in the record', expected: 'Expected from ATT&CK', added: 'Expected by an analyst' } as const;

function remembered(): boolean {
  try {
    return localStorage.getItem(COVERAGE_KEY) !== 'off';
  } catch {
    return true;
  }
}

function attackUrl(id: string) {
  return `https://attack.mitre.org/techniques/${id.replace('.', '/')}/`;
}

// What a node says under its name, a few short lines: what the record names there (CVEs, tools, services),
// else its techniques, else what to check
function gist(s: FlowStage): string[] {
  const more = (list: string[], n: number) => (list.length > n ? [...list.slice(0, n), `+${list.length - n} more`] : list);
  if (s.items.length) return more(s.items, 2);
  if (s.techniques.length) return more(s.techniques.map((t) => t.name), 1);
  if (s.links.length) return [`${s.links.length} movement${s.links.length === 1 ? '' : 's'} between assets`];
  if (s.suggestions.length) return [`Check: ${s.suggestions[0].name}`];
  return [];
}

// The actions share one node; its line in, and its look, are the actions' together
function together(actions: FlowStage[]): { layer: string; coverage: string } {
  return {
    layer: actions.some((a) => a.layer === 'seen') ? 'seen' : 'expected',
    coverage: actions.every((a) => a.coverage === 'covered')
      ? 'covered'
      : actions.some((a) => a.coverage !== 'open')
        ? 'focused'
        : 'open',
  };
}

function FlowDrawing({
  flow,
  overlay,
  chosen,
  choose,
}: {
  flow: Flow;
  overlay: boolean;
  chosen: string | null;
  choose: (key: string) => void;
}) {
  const look = (s: { layer: string; coverage: string }) =>
    `layer-${s.layer === 'seen' ? 'seen' : 'expected'}${overlay ? ` cov-${s.coverage}` : ''}`;
  const label = (s: FlowStage) => `${s.name}${overlay ? `, ${coverageNames[s.coverage].toLowerCase()}` : ''}`;
  return (
    <Anatomy
      parts={flow.stages.map((s) => ({
        key: s.key,
        name: s.name,
        column: s.column,
        verb: s.verb,
        look: look(s),
        lines: gist(s),
        label: label(s),
        tick: overlay && s.coverage === 'covered',
      }))}
      actor={{ title: flow.group?.name ?? 'Attacker', lines: [flow.group ? flow.group.id : 'Unattributed'] }}
      actions={{ look: look(together(flow.stages.filter((s) => s.column === 'action'))) }}
      chosen={(key) => chosen === key}
      choose={choose}
    />
  );
}

function Detail({ incident, flow, stage }: { incident: Incident; flow: Flow; stage: FlowStage }) {
  const save = useSave(incident, ['flow']);
  const [call, setCall] = useState<StageCall>('cleared');
  const base = `/api/incidents/${incident.id}/flow/stages/${stage.key}`;

  async function decide(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const note = String(new FormData(form).get('note') ?? '').trim();
    if ((await save.run(() => api(base, { method: 'PUT', json: { call, note } }))) !== undefined) form.reset();
  }

  return (
    <section className="flow-detail" aria-label={stage.name}>
      <header>
        <h3>{stage.name}</h3>
        <p className="hint">
          {columnNames[stage.column]} · {stage.says} · {layerNames[stage.layer]} · <span className={`flow-cov cov-${stage.coverage}`}>{coverageNames[stage.coverage]}</span>
        </p>
        {stage.why && <p className="hint">{stage.why}</p>}
      </header>

      {stage.items.length > 0 && (
        <div>
          <h4>Named in the record</h4>
          <ul className="flow-list">
            {stage.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {stage.techniques.length > 0 && (
        <div>
          <h4>Seen here</h4>
          <ul className="flow-list">
            {stage.techniques.map((t) => (
              <li key={t.id}>
                <a className="mono" href={attackUrl(t.id)} target="_blank" rel="noreferrer">
                  {t.id}
                </a>{' '}
                {t.name}
              </li>
            ))}
          </ul>
        </div>
      )}
      {stage.findings.length > 0 && (
        <ul className="flow-list">
          {stage.findings.map((f) => (
            <li key={f.ref}>
              <Link className="ref-link mono" to={refTarget(f.ref)}>
                {f.ref}
              </Link>{' '}
              {f.title} <span className={`chip status-${f.status}`}>{f.status}</span>
            </li>
          ))}
        </ul>
      )}
      {stage.links.length > 0 && (
        <ul className="flow-list">
          {stage.links.map((l, i) => (
            <li key={i}>
              {l.source} → {l.target}
              {l.technique && <span className="mono hint"> {l.technique}</span>}
            </li>
          ))}
        </ul>
      )}
      {stage.suggestions.length > 0 && (
        <div>
          <h4>Still to check</h4>
          <ul className="flow-list">
            {stage.suggestions.map((t) => (
              <li key={t.id}>
                <a className="mono" href={attackUrl(t.id)} target="_blank" rel="noreferrer">
                  {t.id}
                </a>{' '}
                {t.name}{' '}
                <span className="hint">
                  · {flow.group ? flow.group.name : `${t.groups} of ${flow.peers} groups`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(stage.tasks_open.length > 0 || stage.tasks_done > 0) && (
        <div>
          <h4>Tasks</h4>
          <ul className="flow-list">
            {stage.tasks_open.map((t) => (
              <li key={t.ref}>
                <Link className="ref-link mono" to={refTarget(t.ref)}>
                  {t.ref}
                </Link>{' '}
                {t.title}
              </li>
            ))}
            {stage.tasks_done > 0 && <li className="hint">{stage.tasks_done} done</li>}
          </ul>
        </div>
      )}

      {stage.call && (
        <p className="flow-call">
          <strong>{stage.call.call === 'cleared' ? 'Checked, nothing found' : 'Expected'}:</strong> {stage.call.note}{' '}
          <span className="hint">
            ({stage.call.by}, {formatDate(stage.call.at)})
          </span>{' '}
          {flow.can_edit && (
            <button className="link-button" type="button" onClick={() => save.run(() => api(base, { method: 'DELETE' }))}>
              Withdraw
            </button>
          )}
        </p>
      )}
      {flow.can_edit && (
        <form className="flow-decide" onSubmit={decide} aria-label={`A call on ${stage.name}`}>
          <select className="input inline" value={call} onChange={(e) => setCall(e.target.value as StageCall)} aria-label="Call">
            <option value="cleared">Checked, nothing found</option>
            <option value="dismissed">Not applicable here</option>
          </select>
          <input
            className="input"
            name="note"
            required
            minLength={3}
            maxLength={2000}
            aria-label={call === 'cleared' ? 'Where you looked' : 'Why it doesn’t apply'}
            placeholder={call === 'cleared' ? 'Where you looked, and over what period' : 'Why it doesn’t apply'}
          />
          <button className="btn" type="submit">
            Save
          </button>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

// The attack flow: the record's attack as Shikari draws one (how they get in, what they run, what they do and
// where it goes), what it's likely to have done as well, and with the overlay on, how far the team has
// answered each step. Covering every step measures how thorough the work has been; it's never needed to
// close the record.
export function AttackFlow({ incident }: { incident: Incident }) {
  const flow = useSection<Flow>(incident, 'flow');
  const groups = useQuery({
    queryKey: ['attack-groups'],
    queryFn: () => api<{ id: string; name: string }[]>('/api/attack/groups'),
    enabled: !!flow.data?.can_edit,
    staleTime: Infinity,
  });
  const save = useSave(incident, ['flow']);
  const [overlay, setOverlay] = useState(remembered);
  const [chosen, setChosen] = useState<string | null>(null);
  const [adding, setAdding] = useState('');

  const data = flow.data;
  if (flow.error || !data) return null;
  const shown = new Set(data.stages.map((s) => s.key));
  const ruled = new Set(data.dismissed.map((d) => d.key));
  const addable = data.all_stages.filter((s) => !shown.has(s.id) && !ruled.has(s.id));
  const selected = data.stages.find((s) => s.key === chosen) ?? null;

  function toggle(on: boolean) {
    setOverlay(on);
    try {
      localStorage.setItem(COVERAGE_KEY, on ? 'on' : 'off');
    } catch {
      // a private window: it just won't be remembered
    }
  }

  async function expect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const note = String(new FormData(form).get('note') ?? '').trim();
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/flow/stages/${adding}`, { method: 'PUT', json: { call: 'expected', note } }),
    );
    if (done !== undefined) {
      form.reset();
      setAdding('');
    }
  }

  return (
    <section className="panel attack-flow">
      <header className="panel-head">
        <div>
          <h2>Attack flow</h2>
          <p className="hint">
            How they get in, what they run, what they do and where it goes, from this record and ATT&amp;CK
            {data.stages.length > 0 && (
              <>
                {' '}
                · <strong>{data.covered}</strong> of {data.stages.length} steps covered
              </>
            )}
          </p>
        </div>
        <label className="check">
          <input type="checkbox" checked={overlay} onChange={(e) => toggle(e.target.checked)} />
          <span>Show coverage</span>
        </label>
      </header>

      <div className="flow-attribution">
        <span className="label">Attributed to</span>
        {data.can_edit ? (
          <select
            className="input inline"
            aria-label="Attributed to"
            value={data.group?.id ?? ''}
            onChange={(e) =>
              save.run(() =>
                api(`/api/incidents/${incident.id}/flow/attribution`, { method: 'PUT', json: { group: e.target.value || null } }),
              )
            }
          >
            <option value="">Nobody: expect what groups like it do</option>
            {data.group && !groups.data && <option value={data.group.id}>{data.group.name}</option>}
            {groups.data?.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name} ({g.id})
              </option>
            ))}
          </select>
        ) : (
          <span>{data.group ? `${data.group.name} (${data.group.id})` : 'Nobody'}</span>
        )}
      </div>

      {data.stages.length === 0 ? (
        <p className="hint">
          Nothing to draw yet. Give findings their ATT&amp;CK techniques, or name the tools and services in them,
          and the flow builds itself, with what the attack is likely to do next.
        </p>
      ) : (
        <>
          <FlowDrawing flow={data} overlay={overlay} chosen={chosen} choose={(key) => setChosen(chosen === key ? null : key)} />
          <p className="flow-legend hint">
            <span className="flow-key layer-seen" /> seen in the record <span className="flow-key layer-expected" />{' '}
            expected from ATT&amp;CK or by an analyst
            {overlay && (
              <>
                {' '}
                · <span className="flow-cov cov-covered">covered</span> answered: confirmed with its tasks done, or
                checked and nothing found · <span className="flow-cov cov-focused">being worked</span> a lead or open
                task
              </>
            )}
          </p>
        </>
      )}

      {selected ? (
        <Detail incident={incident} flow={data} stage={selected} />
      ) : (
        data.stages.length > 0 && <p className="hint">Select a step for what’s in it, what’s still to check and the calls made on it.</p>
      )}

      {data.can_edit && addable.length > 0 && (
        <form className="flow-decide" onSubmit={expect} aria-label="Expect another step">
          <select className="input inline" aria-label="Step" value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">Expect another step…</option>
            {(Object.keys(columnNames) as FlowColumn[]).map((column) =>
              addable.some((s) => s.column === column) ? (
                <optgroup key={column} label={columnNames[column]}>
                  {addable
                    .filter((s) => s.column === column)
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                </optgroup>
              ) : null,
            )}
          </select>
          {adding && (
            <>
              <input className="input" name="note" required minLength={3} maxLength={2000} aria-label="Why" placeholder="Why you expect it" />
              <button className="btn" type="submit">
                Add
              </button>
            </>
          )}
        </form>
      )}

      {data.dismissed.length > 0 && (
        <div className="flow-dismissed">
          <h4>Ruled out</h4>
          <ul className="flow-list">
            {data.dismissed.map((d) => (
              <li key={d.key}>
                <strong>{d.name}</strong>: {d.note}{' '}
                <span className="hint">
                  ({d.by}, {formatDate(d.at)})
                </span>{' '}
                {data.can_edit && (
                  <button
                    className="link-button"
                    type="button"
                    onClick={() => save.run(() => api(`/api/incidents/${incident.id}/flow/stages/${d.key}`, { method: 'DELETE' }))}
                  >
                    Put back
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}
