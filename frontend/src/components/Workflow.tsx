import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { layout } from '../api/work';
import type { PersonRef } from '../api/incidents';
import type { Outcome, StepState } from '../api/work';
import { teamColour, teamSlots } from './teamColour';
import { route } from './workflowRoute';
import './Workflow.css';

export interface DrawnStep {
  title: string;
  team: PersonRef | null;
  after: number[];
  phase: string | null;
  outcomes: Outcome[];
  // From the playbook, for the step's detail when it's clicked
  description?: string;
  checklist?: string[];
  due_minutes?: number | null;
  // On a record: where the step has got to
  state?: StepState;
  taskRef?: string | null;
  outcome?: string | null;
  runs?: number;
}

interface Edge {
  from: number;
  to: number;
  label?: string;
  back?: boolean;
}

const stateNames: Record<StepState, string> = {
  waiting: 'Waiting',
  open: 'Open',
  done: 'Done',
  skipped: 'Skipped',
};

type IconName = 'team' | 'anyone' | 'decision' | 'phase' | 'again' | StepState;

const drawings: Record<IconName, string> = {
  team: 'M5.5 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM10.5 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM1.5 13c0-2.2 1.8-4 4-4s4 1.8 4 4M8.5 9.3c.6-.2 1.3-.3 2-.3 2.2 0 4 1.8 4 4',
  anyone: 'M8 7a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM3 14c0-2.8 2.2-5 5-5s5 2.2 5 5',
  decision: 'M8 1.5 14.5 8 8 14.5 1.5 8ZM6.4 6.3a1.7 1.7 0 1 1 2.3 1.6c-.5.2-.7.6-.7 1.1M8 11.2v.1',
  phase: 'M3.5 14.5v-12M3.5 2.5h8l-2 3 2 3h-8',
  again: 'M13 8a5 5 0 1 1-1.5-3.6M13 2.5v2.5h-2.5',
  waiting: 'M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM8 4.5V8l2.5 1.5',
  open: 'M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM6.5 5.5l4 2.5-4 2.5Z',
  done: 'M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM5 8.2l2 2 4-4.4',
  skipped: 'M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM4.5 8h7',
};

function Icon({ name }: { name: IconName }) {
  return (
    <svg className="wf-icon" viewBox="0 0 16 16" aria-hidden="true">
      <path d={drawings[name]} />
    </svg>
  );
}

function edges(steps: DrawnStep[]): Edge[] {
  const out: Edge[] = [];
  steps.forEach((s, i) => {
    s.after.forEach((a) => out.push({ from: a, to: i }));
    s.outcomes.forEach((o) =>
      o.opens.forEach((p) => out.push({ from: i, to: p, label: o.label.split(':')[0], back: p < i })),
    );
  });
  return out;
}

/** A playbook as if it were part-way through on a record: the first half of the steps that can run
 * done (a decision taking its first outcome), the next ones open, the rest waiting, and a step only a
 * decision opens skipped when the decision went the other way. For "Show an example". */
export function exampleRun(steps: DrawnStep[]): DrawnStep[] {
  // The earlier decision that opens a step, if one does; a later one that sends the work back only runs it again
  const opener = (i: number) => steps.findIndex((s, j) => j < i && s.outcomes.some((o) => o.opens.includes(i)));
  const state: StepState[] = steps.map(() => 'waiting');
  const outcome: (string | null)[] = steps.map(() => null);
  const ready = (i: number) => steps[i].after.every((a) => state[a] === 'done');
  const target = Math.ceil(steps.length / 2);
  let done = 0;
  for (let pass = 0; pass < steps.length && done < target; pass++) {
    steps.forEach((s, i) => {
      if (done >= target || state[i] !== 'waiting' || !ready(i)) return;
      const by = opener(i);
      if (by >= 0 && state[by] !== 'done') return;
      if (by >= 0 && !steps[by].outcomes.find((o) => o.label === outcome[by])?.opens.includes(i)) {
        state[i] = 'skipped';
        return;
      }
      state[i] = 'done';
      outcome[i] = s.outcomes[0]?.label ?? null;
      done++;
    });
  }
  steps.forEach((_, i) => {
    if (state[i] !== 'waiting') return;
    const by = opener(i);
    const went = by >= 0 && state[by] === 'done' ? steps[by].outcomes.find((o) => o.label === outcome[by]) : undefined;
    if (by >= 0 && went && !went.opens.includes(i)) state[i] = 'skipped';
    else if (ready(i) && (by < 0 || went)) state[i] = 'open';
  });
  return steps.map((s, i) => ({ ...s, state: state[i], outcome: outcome[i] }));
}

const minutesWords = (m: number) =>
  m < 60 ? `${m} min` : m < 1440 ? `${+(m / 60).toFixed(1)} h` : `${+(m / 1440).toFixed(1)} days`;

const actionWords = (o: Outcome) =>
  o.action === 'close'
    ? 'closes the record'
    : o.action === 'promote'
      ? 'promotes to an incident'
      : o.action === 'phase'
        ? `back to ${o.phase}`
        : '';

// A playbook as swimlanes: a lane per team, steps left to right in the order they can open, solid
// arrows for "after", dashed ones for a decision's outcomes (curving back when it runs a step again)
export function Workflow({ steps, incidentRef }: { steps: DrawnStep[]; incidentRef?: string }) {
  const places = layout(steps);
  const lanes: { name: string; id: string | null }[] = [];
  steps.forEach((s) => {
    const name = s.team?.name ?? '';
    if (!lanes.some((l) => l.name === name)) lanes.push({ name, id: s.team?.id ?? null });
  });
  const slots = teamSlots(lanes.flatMap((l) => (l.id ? [l.id] : [])));
  const colourOf = (id: string | null | undefined) => teamColour(id ? slots.get(id) : undefined);
  const paint = (id: string | null | undefined) => ({ '--team': colourOf(id) }) as CSSProperties;
  const columns = Math.max(0, ...places.map((p) => p.column)) + 1;
  const box = useRef<HTMLDivElement>(null);
  const cards = useRef<(HTMLLIElement | null)[]>([]);
  const [lines, setLines] = useState<
    {
      d: string;
      head: string;
      label?: string;
      x: number;
      y: number;
      dashed: boolean;
      from: number;
    }[]
  >([]);
  const [chosen, setChosen] = useState<number | null>(null);
  const detailId = useId();
  const picked = chosen !== null && chosen < steps.length ? chosen : null;

  useLayoutEffect(() => {
    const draw = () => {
      const frame = box.current?.getBoundingClientRect();
      if (!frame) return;
      const at = (i: number) => {
        const r = cards.current[i]?.getBoundingClientRect();
        return (
          r && {
            left: r.left - frame.left,
            right: r.right - frame.left,
            top: r.top - frame.top,
            bottom: r.bottom - frame.top,
          }
        );
      };
      setLines(
        edges(steps).flatMap((e) => {
          const a = at(e.from);
          const b = at(e.to);
          if (!a || !b) return [];
          return [
            {
              ...route(a, b, e.back),
              label: e.label,
              dashed: !!e.label,
              from: e.from,
            },
          ];
        }),
      );
    };
    draw();
    // Redrawn whenever the drawing or any card changes size, as a card does when its state is shown
    const watch = new ResizeObserver(draw);
    if (box.current) watch.observe(box.current);
    cards.current.forEach((card) => card && watch.observe(card));
    return () => watch.disconnect();
  }, [steps]);

  return (
    <div className="workflow-wrap">
      <div
        className="workflow"
        ref={box}
        style={{
          gridTemplateColumns: `10rem repeat(${columns}, minmax(12rem, 1fr))`,
        }}
      >
        <svg className="workflow-lines" aria-hidden="true">
          {lines.map((l, i) => {
            // "After" lines leave in the colour of the team that hands the work on; a decision's are dashed
            const colour = l.dashed ? undefined : colourOf(steps[l.from]?.team?.id);
            return (
              <g key={i} className={l.dashed ? 'dashed' : undefined}>
                <path className="line" d={l.d} style={colour ? { stroke: colour } : undefined} />
                <path className="head" d={l.head} style={colour ? { fill: colour } : undefined} />
                {l.label && (
                  <text x={l.x} y={l.y} textAnchor="middle">
                    {l.label}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {lanes.map((lane, row) => (
          <div
            key={`band-${lane.name}`}
            className="workflow-band"
            style={{ gridRow: row + 1, ...paint(lane.id) }}
            aria-hidden="true"
          />
        ))}
        {lanes.map((lane, row) => (
          <div key={lane.name || 'runner'} className="workflow-lane" style={{ gridRow: row + 1, ...paint(lane.id) }}>
            <span className="workflow-lane-badge">
              <Icon name={lane.id ? 'team' : 'anyone'} />
            </span>
            <span>{lane.name || 'Whoever runs it'}</span>
          </div>
        ))}
        {Array.from({ length: lanes.length }, (_, row) =>
          Array.from({ length: columns }, (_, column) => {
            const here = steps
              .map((s, i) => ({ s, i }))
              .filter(({ i }) => places[i].lane === row && places[i].column === column);
            if (!here.length) return null;
            return (
              <ul
                key={`${row}-${column}`}
                className="workflow-cell"
                style={{ gridRow: row + 1, gridColumn: column + 2 }}
              >
                {here.map(({ s, i }) => (
                  <li
                    key={i}
                    ref={(el) => {
                      cards.current[i] = el;
                    }}
                    className={`workflow-step${s.outcomes.length ? ' is-decision' : ''}${s.state ? ` state-${s.state}` : ''}${picked === i ? ' is-picked' : ''}`}
                    style={paint(s.team?.id)}
                    aria-label={`Step ${i + 1}: ${s.title}${s.state ? `, ${stateNames[s.state].toLowerCase()}` : ''}`}
                  >
                    <span className="workflow-step-head">
                      <span className="workflow-num num">{i + 1}</span>
                      {s.state && (
                        <span className="workflow-state">
                          <Icon name={s.state} />
                          {stateNames[s.state]}
                        </span>
                      )}
                      {s.outcomes.length > 0 && (
                        <span className="workflow-tag workflow-tag-decision">
                          <Icon name="decision" />
                          Decision
                        </span>
                      )}
                      {s.phase && (
                        <span className="workflow-tag">
                          <Icon name="phase" />
                          at {s.phase}
                        </span>
                      )}
                      {(s.runs ?? 0) > 1 && (
                        <span className="workflow-tag">
                          <Icon name="again" />
                          run {s.runs}
                        </span>
                      )}
                    </span>
                    {/* the whole card opens the step's detail */}
                    <button
                      type="button"
                      className="workflow-step-open"
                      aria-expanded={picked === i}
                      aria-controls={picked === i ? detailId : undefined}
                      onClick={() => setChosen(picked === i ? null : i)}
                    >
                      {s.title}
                    </button>
                    {s.taskRef && incidentRef && (
                      <Link className="ref-link mono" to={`/incidents/${incidentRef}?s=tasks#${s.taskRef}`}>
                        {s.taskRef}
                      </Link>
                    )}
                    {s.outcomes.length > 0 && (
                      <ul className="workflow-outcomes">
                        {s.outcomes.map((o) => (
                          <li key={o.label} className={s.outcome === o.label ? 'chosen' : undefined}>
                            {o.label}
                            {actionWords(o) && <span className="hint"> · {actionWords(o)}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            );
          }),
        )}
      </div>
      {picked !== null && (
        <StepDetail
          id={detailId}
          n={picked}
          steps={steps}
          colour={colourOf(steps[picked].team?.id)}
          incidentRef={incidentRef}
          onClose={() => setChosen(null)}
        />
      )}
    </div>
  );
}

// Everything about one step: who, when it opens, how long it has, what it waits for and leads to, its
// checklist, and where a decision sends the work
function StepDetail({
  id,
  n,
  steps,
  colour,
  incidentRef,
  onClose,
}: {
  id: string;
  n: number;
  steps: DrawnStep[];
  colour: string;
  incidentRef?: string;
  onClose: () => void;
}) {
  const s = steps[n];
  const name = (i: number) => `${i + 1}. ${steps[i]?.title ?? ''}`;
  const leadsTo = steps.flatMap((o, i) => (o.after.includes(n) ? [i] : []));
  const openedBy = steps.flatMap((o, i) =>
    o.outcomes.filter((x) => x.opens.includes(n)).map((x) => `${name(i)}: ${x.label}`),
  );
  return (
    <section
      id={id}
      className="workflow-detail"
      style={{ '--team': colour } as CSSProperties}
      aria-label={`Step ${n + 1}: ${s.title}`}
    >
      <header className="workflow-detail-head">
        <span className="workflow-num num">{n + 1}</span>
        <h3>{s.title}</h3>
        {s.state && (
          <span className="workflow-state">
            <Icon name={s.state} />
            {stateNames[s.state]}
          </span>
        )}
        {s.taskRef && incidentRef && (
          <Link className="ref-link mono" to={`/incidents/${incidentRef}?s=tasks#${s.taskRef}`}>
            {s.taskRef}
          </Link>
        )}
        <button className="link-button" type="button" onClick={onClose}>
          Close
        </button>
      </header>
      {s.description && <p className="prose">{s.description}</p>}
      <dl className="workflow-facts">
        <div>
          <dt>Who</dt>
          <dd>{s.team ? `${s.team.name}’s queue` : 'Whoever applies the playbook'}</dd>
        </div>
        <div>
          <dt>Opens</dt>
          <dd>
            {openedBy.length
              ? `When a decision goes this way: ${openedBy.join('; ')}`
              : s.after.length
                ? `After ${s.after.map(name).join(', ')}`
                : 'Straight away'}
            {s.phase && `, once the record reaches ${s.phase}`}
          </dd>
        </div>
        {s.due_minutes != null && (
          <div>
            <dt>Due</dt>
            <dd>{minutesWords(s.due_minutes)} after it opens</dd>
          </div>
        )}
        {leadsTo.length > 0 && (
          <div>
            <dt>Then</dt>
            <dd>{leadsTo.map(name).join(', ')}</dd>
          </div>
        )}
      </dl>
      {s.checklist && s.checklist.filter(Boolean).length > 0 && (
        <div className="stack">
          <span className="label">Checklist</span>
          <ul className="step-list">
            {s.checklist.filter(Boolean).map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {s.outcomes.length > 0 && (
        <div className="stack">
          <span className="label">A decision: it’s finished by choosing one of these</span>
          <ul className="step-list">
            {s.outcomes.map((o) => (
              <li key={o.label} className={s.outcome === o.label ? 'chosen' : undefined}>
                <strong>{o.label}</strong>
                {actionWords(o) && ` · ${actionWords(o)}`}
                {o.opens.length > 0 && ` · opens ${o.opens.map(name).join(', ')}`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
