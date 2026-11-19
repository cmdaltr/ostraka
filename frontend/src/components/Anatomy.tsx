import type { KeyboardEvent, ReactNode } from 'react';
import type { FlowColumn } from '../api/flow';

// The attack's anatomy drawn left to right: who, the ways in, the toolkit, the actions, where it goes.
// The record's attack flow and the playbooks draw it; each says what a part looks like (`look`, classes
// for the part and the line into it) and what it says under its name. The styles are incident.css's flow-*.

export interface DrawnPart {
  key: string;
  name: string;
  column: FlowColumn;
  verb: string; // on the line into it
  look: string;
  lines?: string[]; // under its name; an action has none
  text?: string; // an action's line, if not its name
  label?: string; // set: it can be chosen, and this is what a screen reader hears
  tick?: boolean; // a tick in place of its glyph
}

const W = 1120;
const R = 28;
const GAP = 140; // between stacked nodes
const LINE = 19; // a line of text under a node

const glyphs: Record<string, ReactNode> = {
  actor: (
    <>
      <circle cx="0" cy="-6" r="5" />
      <path d="M-10 11c1-7 5-10 10-10s9 3 10 10" />
    </>
  ),
  exploit: (
    <>
      <ellipse cx="0" cy="2" rx="6" ry="8" />
      <path d="M-6 -2h-5M6 -2h5M-6 5h-5M6 5h5M-3 -7l-3 -4M3 -7l3 -4M0 -6v16" />
    </>
  ),
  phishing: (
    <>
      <rect x="-11" y="-8" width="22" height="16" rx="2" />
      <path d="M-11 -6l11 8 11 -8" />
    </>
  ),
  credentials: (
    <>
      <circle cx="-5" cy="5" r="5" />
      <path d="M-1 1l11 -11M6 -6l3 3M3 -3l3 3" />
    </>
  ),
  toolkit: (
    <>
      <rect x="-11" y="-9" width="22" height="18" rx="2" />
      <path d="M-6 -3l4 3 -4 3M1 4h5" />
    </>
  ),
  action: (
    <>
      <circle r="6" />
      <path d="M0 -12v6M0 6v6M-12 0h6M6 0h6" />
    </>
  ),
  service: <path d="M-8 6h15a5 5 0 0 0 0 -10a7 7 0 0 0 -13 -2a6 6 0 0 0 -2 12z" />,
  infrastructure: (
    <>
      <rect x="-10" y="-10" width="20" height="8" rx="1.5" />
      <rect x="-10" y="2" width="20" height="8" rx="1.5" />
      <path d="M-6 -6h1M-6 6h1" />
    </>
  ),
};

type Node = { key: string; x: number; y: number; part: DrawnPart | null };

function curve(x1: number, y1: number, x2: number, y2: number, bend: number) {
  const dx = (x2 - x1) * bend;
  return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
}

export function Anatomy({
  parts,
  actor,
  actions,
  chosen,
  choose,
  label = 'How the attack unfolds',
}: {
  parts: DrawnPart[];
  actor: { title: string; lines: string[] };
  // the Actions node, which holds the actions listed under it: its look, and a label if it can be chosen
  actions: { look: string; label?: string };
  chosen: (key: string) => boolean;
  choose?: (key: string) => void;
  label?: string;
}) {
  const of = (column: FlowColumn) => parts.filter((p) => p.column === column);
  const ways = of('way');
  const tools = of('toolkit');
  const acts = of('action');
  const services = of('service');
  // the columns that have something, spread across the width
  const columns = [
    'actor',
    ...(ways.length ? ['way'] : []),
    ...(tools.length ? ['toolkit'] : []),
    ...(acts.length ? ['action'] : []),
    ...(services.length ? ['service'] : []),
  ];
  const x = (c: string) => 80 + (columns.indexOf(c) * (W - 190)) / Math.max(columns.length - 1, 1);
  const stack = Math.max(ways.length, services.length, 1);
  const mid = 70 + ((stack - 1) * GAP) / 2;
  const ys = (n: number) => Array.from({ length: n }, (_, i) => mid + (i - (n - 1) / 2) * GAP);
  // the actions are listed under their node, so it sits a little higher
  const actionsY = acts.length > 3 ? Math.max(mid - 50, 50) : mid;
  const height = Math.max(70 + (stack - 1) * GAP + 100, actionsY + R + 34 + acts.length * LINE + 16);

  const actorNode: Node = { key: 'actor', x: x('actor'), y: mid, part: null };
  const wayNodes = ways.map((p, i) => ({ key: p.key, x: x('way'), y: ys(ways.length)[i], part: p }));
  const toolNode = tools.length ? { key: 'toolkit', x: x('toolkit'), y: mid, part: tools[0] } : null;
  const actionNode = acts.length ? { key: 'actions', x: x('action'), y: actionsY, part: null } : null;
  const serviceNodes = services.map((p, i) => ({ key: p.key, x: x('service'), y: ys(services.length)[i], part: p }));

  const edges: { from: Node; to: Node; verb: string; look: string; out: boolean }[] = [];
  const link = (from: Node, to: Node, verb: string, out = false) =>
    edges.push({ from, to, verb, look: to.part?.look ?? actions.look, out });
  for (const n of wayNodes) link(actorNode, n, n.part.verb);
  let last: Node[] = wayNodes.length ? wayNodes : [actorNode];
  for (const next of [toolNode, actionNode]) {
    if (!next) continue;
    last.forEach((n, i) => link(n, next, i === Math.floor((last.length - 1) / 2) ? (next === toolNode ? 'Runs' : 'To') : ''));
    last = [next];
  }
  for (const n of serviceNodes) last.forEach((from) => link(from, n, n.part.verb, from === actionNode));

  const keyed = (key: string) => (event: KeyboardEvent<SVGElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      choose?.(key);
    }
  };
  const pressable = (key: string, aria?: string) =>
    aria && choose
      ? { role: 'button', tabIndex: 0, 'aria-label': aria, 'aria-pressed': chosen(key), onClick: () => choose(key), onKeyDown: keyed(key) }
      : {};

  const node = (n: Node, title: string, lines: string[], glyph: ReactNode, look: string, aria?: string, tick = false) => (
    <g key={n.key} className={`flow-node ${look}${chosen(n.key) ? ' chosen' : ''}`} transform={`translate(${n.x} ${n.y})`} {...pressable(n.key, aria)}>
      {chosen(n.key) && <circle className="flow-halo" r={R + 5} />}
      <circle r={R} />
      {tick ? (
        <text className="flow-tick" y="6" textAnchor="middle">
          ✓
        </text>
      ) : (
        <g className="flow-glyph">{glyph}</g>
      )}
      <text className="flow-title" y={R + 20} textAnchor="middle">
        {title}
      </text>
      {lines.map((line, i) => (
        <text key={i} className="flow-sub" y={R + 38 + i * 17} textAnchor="middle">
          {line}
        </text>
      ))}
    </g>
  );
  const drawn = (n: Node, glyph: ReactNode) => node(n, n.part!.name, n.part!.lines ?? [], glyph, n.part!.look, n.part!.label, n.part!.tick);

  return (
    <div className="flow-drawing">
      <svg viewBox={`0 0 ${W} ${height}`} role="group" aria-label={label}>
        {edges.map((e, i) => {
          const x1 = e.from.x + R + 4;
          const x2 = e.to.x - R - 8;
          const mx = e.out ? x2 - 48 : (x1 + x2) / 2;
          const my = e.out ? e.to.y - 12 : (e.from.y + e.to.y) / 2 - 8;
          return (
            <g key={i} className={`flow-edge ${e.look}`}>
              <path d={curve(x1, e.from.y, x2, e.to.y, e.out ? 0.95 : 0.5)} />
              <path className="flow-arrow" d={`M${x2 - 7} ${e.to.y - 5}l7 5 -7 5`} />
              {e.verb && (
                <text x={mx} y={my} textAnchor="middle">
                  {e.verb}
                </text>
              )}
            </g>
          );
        })}
        {node(actorNode, actor.title, actor.lines, glyphs.actor, 'layer-seen actor')}
        {wayNodes.map((n) => drawn(n, glyphs[n.key]))}
        {toolNode && drawn(toolNode, glyphs.toolkit)}
        {actionNode && (
          <g>
            {node(actionNode, 'Actions', [], glyphs.action, actions.look, actions.label)}
            {acts.map((a, i) => (
              <text
                key={a.key}
                className={`flow-action ${a.look}${chosen(a.key) ? ' chosen' : ''}`}
                x={actionNode.x}
                y={actionNode.y + R + 40 + i * LINE}
                textAnchor="middle"
                {...pressable(a.key, a.label)}
              >
                {a.tick ? '✓ ' : ''}
                {a.text ?? a.name}
              </text>
            ))}
          </g>
        )}
        {serviceNodes.map((n) => drawn(n, glyphs[n.key === 'infrastructure' ? 'infrastructure' : 'service']))}
      </svg>
    </div>
  );
}
