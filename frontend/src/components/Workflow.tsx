import { useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { layout } from '../api/work';
import type { PersonRef } from '../api/incidents';
import type { Outcome, StepState } from '../api/work';
import { teamColour, teamSlots } from './teamColour';
import './Workflow.css';

export interface DrawnStep {
  title: string;
  team: PersonRef | null;
  after: number[];
  phase: string | null;
  outcomes: Outcome[];
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

const stateNames: Record<StepState, string> = { waiting: 'Waiting', open: 'Open', done: 'Done', skipped: 'Skipped' };

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
    s.outcomes.forEach((o) => o.opens.forEach((p) => out.push({ from: i, to: p, label: o.label.split(':')[0], back: p < i })));
  });
  return out;
}

const actionWords = (o: Outcome) =>
  o.action === 'close' ? 'closes the record' : o.action === 'promote' ? 'promotes to an incident' : o.action === 'phase' ? `back to ${o.phase}` : '';

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
  const [lines, setLines] = useState<{ d: string; label?: string; x: number; y: number; dashed: boolean; from: number }[]>([]);

  useLayoutEffect(() => {
    const draw = () => {
      const frame = box.current?.getBoundingClientRect();
      if (!frame) return;
      const at = (i: number) => cards.current[i]?.getBoundingClientRect();
      setLines(
        edges(steps).flatMap((e) => {
          const a = at(e.from);
          const b = at(e.to);
          if (!a || !b) return [];
          if (e.back) {
            // Over the top, from the decision back to the step that runs again
            const x1 = a.left + a.width / 2 - frame.left;
            const y1 = a.top - frame.top;
            const x2 = b.left + b.width / 2 - frame.left;
            const y2 = b.top - frame.top;
            const lift = Math.min(y1, y2) - 18;
            return [{ d: `M${x1},${y1} C${x1},${lift} ${x2},${lift} ${x2},${y2}`, label: e.label, x: (x1 + x2) / 2, y: lift + 4, dashed: true, from: e.from }];
          }
          const x1 = a.right - frame.left;
          const y1 = a.top + a.height / 2 - frame.top;
          const x2 = b.left - frame.left;
          const y2 = b.top + b.height / 2 - frame.top;
          const mid = (x1 + x2) / 2;
          return [{ d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`, label: e.label, x: mid, y: (y1 + y2) / 2 - 4, dashed: !!e.label, from: e.from }];
        }),
      );
    };
    draw();
    const watch = new ResizeObserver(draw);
    if (box.current) watch.observe(box.current);
    return () => watch.disconnect();
  }, [steps]);

  return (
    <div className="workflow" ref={box} style={{ gridTemplateColumns: `10rem repeat(${columns}, minmax(12rem, 1fr))` }}>
      <svg className="workflow-lines" aria-hidden="true">
        <defs>
          <marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" />
          </marker>
        </defs>
        {lines.map((l, i) => (
          <g key={i}>
            <path
              d={l.d}
              className={l.dashed ? 'dashed' : undefined}
              // "After" lines leave in the colour of the team that hands the work on
              style={l.dashed ? undefined : { stroke: colourOf(steps[l.from]?.team?.id) }}
              markerEnd="url(#wf-arrow)"
            />
            {l.label && (
              <text x={l.x} y={l.y} textAnchor="middle">
                {l.label}
              </text>
            )}
          </g>
        ))}
      </svg>
      {lanes.map((lane, row) => (
        <div key={`band-${lane.name}`} className="workflow-band" style={{ gridRow: row + 1, ...paint(lane.id) }} aria-hidden="true" />
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
          const here = steps.map((s, i) => ({ s, i })).filter(({ i }) => places[i].lane === row && places[i].column === column);
          if (!here.length) return null;
          return (
            <ul key={`${row}-${column}`} className="workflow-cell" style={{ gridRow: row + 1, gridColumn: column + 2 }}>
              {here.map(({ s, i }) => (
                <li
                  key={i}
                  ref={(el) => {
                    cards.current[i] = el;
                  }}
                  className={`workflow-step${s.outcomes.length ? ' is-decision' : ''}${s.state ? ` state-${s.state}` : ''}`}
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
                  <strong>{s.title}</strong>
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
  );
}
