// A layered layout for the incident graph: one column per kind of node, ordered within each column
// to keep crossings down. Deterministic, so the same record always draws the same picture, and pure,
// so it's unit-tested. No graph library: an incident's graph is tens of nodes, not thousands.

import type { GraphEdge, GraphNode, NodeType } from '../../api/evidence';

// Left to right: where it happened, what was seen, what it means, how it was done
export const columnOf: Record<NodeType, number> = {
  asset: 0,
  observable: 1,
  evidence: 1,
  finding: 2,
  technique: 3,
};

export const NODE_W = 196;
export const NODE_H = 34;
export const COL_GAP = 96;
export const ROW_GAP = 14;
export const MARGIN = { top: 36, left: 72, right: 24, bottom: 24 };

export interface Placed {
  node: GraphNode;
  x: number;
  y: number;
  column: number;
}

export interface Layout {
  placed: Map<string, Placed>;
  width: number;
  height: number;
  columns: { column: number; x: number }[]; // the columns that have nodes, and where
}

const byRef = (a: GraphNode, b: GraphNode) =>
  a.type.localeCompare(b.type) || a.ref.localeCompare(b.ref, 'en', { numeric: true });

export function layout(nodes: GraphNode[], edges: GraphEdge[], sweeps = 4): Layout {
  const columns: GraphNode[][] = [[], [], [], []];
  for (const node of [...nodes].sort(byRef)) columns[columnOf[node.type]].push(node);

  const columnById = new Map(nodes.map((n) => [n.id, columnOf[n.type]]));
  const neighbours = new Map<string, string[]>();
  for (const e of edges) {
    if (!neighbours.has(e.source)) neighbours.set(e.source, []);
    if (!neighbours.has(e.target)) neighbours.set(e.target, []);
    neighbours.get(e.source)!.push(e.target);
    neighbours.get(e.target)!.push(e.source);
  }

  // Barycentre sweeps: each node moves towards the average row of its neighbours in other columns
  const rowOf = new Map<string, number>();
  const index = () => columns.forEach((col) => col.forEach((n, i) => rowOf.set(n.id, i)));
  index();
  for (let sweep = 0; sweep < sweeps; sweep++) {
    const order = sweep % 2 === 0 ? [0, 1, 2, 3] : [3, 2, 1, 0];
    for (const c of order) {
      const weight = new Map<string, number>();
      for (const n of columns[c]) {
        const rows = (neighbours.get(n.id) ?? [])
          .filter((m) => rowOf.has(m) && columnById.get(m) !== c)
          .map((m) => rowOf.get(m)!);
        weight.set(n.id, rows.length ? rows.reduce((a, b) => a + b, 0) / rows.length : rowOf.get(n.id)!);
      }
      // Stable: ties keep their current order
      columns[c] = columns[c]
        .map((n, i) => ({ n, i }))
        .sort((a, b) => weight.get(a.n.id)! - weight.get(b.n.id)! || a.i - b.i)
        .map(({ n }) => n);
      index();
    }
  }

  const used = columns.map((col, c) => (col.length ? c : -1)).filter((c) => c >= 0);
  const placed = new Map<string, Placed>();
  const tallest = Math.max(1, ...columns.map((col) => col.length));
  const columnHeight = (count: number) => count * NODE_H + Math.max(0, count - 1) * ROW_GAP;
  used.forEach((c, slot) => {
    const x = MARGIN.left + slot * (NODE_W + COL_GAP);
    // Shorter columns are centred against the tallest
    const offset = (columnHeight(tallest) - columnHeight(columns[c].length)) / 2;
    columns[c].forEach((node, i) => {
      placed.set(node.id, { node, x, y: MARGIN.top + offset + i * (NODE_H + ROW_GAP), column: c });
    });
  });
  return {
    placed,
    width: MARGIN.left + used.length * NODE_W + Math.max(0, used.length - 1) * COL_GAP + MARGIN.right,
    height: MARGIN.top + columnHeight(tallest) + MARGIN.bottom,
    columns: used.map((column, slot) => ({ column, x: MARGIN.left + slot * (NODE_W + COL_GAP) })),
  };
}

// An edge's path. Between columns, a curve from the right edge of the leftmost node to the left edge of
// the other; within a column (asset connections), an arc out to the left of the column.
export function edgePath(a: Placed, b: Placed): string {
  const ay = a.y + NODE_H / 2;
  const by = b.y + NODE_H / 2;
  if (a.x === b.x) {
    const bulge = Math.min(MARGIN.left - 8, 24 + Math.abs(by - ay) / 4);
    return `M${a.x},${ay} C${a.x - bulge},${ay} ${b.x - bulge},${by} ${b.x},${by}`;
  }
  const [l, r, ly, ry] = a.x < b.x ? [a, b, ay, by] : [b, a, by, ay];
  const x1 = l.x + NODE_W;
  const x2 = r.x;
  const mid = (x1 + x2) / 2;
  return `M${x1},${ly} C${mid},${ly} ${mid},${ry} ${x2},${ry}`;
}

// Nodes and edges once some kinds are hidden: an edge needs both its ends
export function visible(nodes: GraphNode[], edges: GraphEdge[], shown: Set<NodeType>) {
  const kept = nodes.filter((n) => shown.has(n.type));
  const ids = new Set(kept.map((n) => n.id));
  return { nodes: kept, edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
}

export function truncate(text: string, max = 26): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
