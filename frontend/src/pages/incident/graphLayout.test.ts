import { describe, expect, it } from 'vitest';
import type { GraphEdge, GraphNode, NodeType } from '../../api/evidence';
import { MARGIN, NODE_H, NODE_W, edgePath, layout, truncate, visible } from './graphLayout';

const node = (type: NodeType, ref: string): GraphNode => ({ id: `${type}:${ref}`, type, ref, label: ref, entity_id: ref, attrs: {} });
const edge = (source: GraphNode, target: GraphNode, kind: GraphEdge['kind'] = 'about_asset'): GraphEdge => ({
  id: `${source.id}>${target.id}`,
  source: source.id,
  target: target.id,
  kind,
  attrs: {},
});

describe('the graph layout', () => {
  const a1 = node('asset', 'AST-1');
  const a2 = node('asset', 'AST-2');
  const f1 = node('finding', 'FND-1');
  const f2 = node('finding', 'FND-2');
  const t1 = node('technique', 'T1021.002');
  const x1 = node('evidence', 'EXH-1');

  it('puts each kind in its own column, left to right', () => {
    const { placed, columns } = layout([t1, f1, a1, x1], []);
    expect(columns.map((c) => c.column)).toEqual([0, 1, 2, 3]);
    const x = (n: GraphNode) => placed.get(n.id)!.x;
    expect(x(a1)).toBeLessThan(x(x1));
    expect(x(x1)).toBeLessThan(x(f1));
    expect(x(f1)).toBeLessThan(x(t1));
  });

  it('closes up empty columns', () => {
    const { placed, width } = layout([a1, t1], []);
    expect(placed.get(t1.id)!.x - placed.get(a1.id)!.x).toBe(NODE_W + 96);
    expect(width).toBe(MARGIN.left + 2 * NODE_W + 96 + MARGIN.right);
  });

  it('orders a column towards its neighbours, so links cross less', () => {
    // FND-1 is about AST-2 and FND-2 about AST-1: the findings swap to sit opposite their assets
    const { placed } = layout([a1, a2, f1, f2], [edge(f1, a2), edge(f2, a1)]);
    const y = (n: GraphNode) => placed.get(n.id)!.y;
    expect(Math.sign(y(f1) - y(f2))).toBe(Math.sign(y(a2) - y(a1)));
  });

  it('is the same every time', () => {
    const nodes = [a1, a2, f1, f2, t1, x1];
    const edges = [edge(f1, a2), edge(f2, a1), edge(f1, t1, 'uses_technique'), edge(f2, x1, 'cites_evidence')];
    const where = (list: GraphNode[]) =>
      Object.fromEntries([...layout(list, edges).placed.values()].map((p) => [p.node.id, [p.x, p.y]]));
    expect(where([...nodes].reverse())).toEqual(where(nodes));
  });

  it('draws a connection between two assets as an arc to the left of their column', () => {
    const { placed } = layout([a1, a2], [edge(a1, a2, 'connection')]);
    const path = edgePath(placed.get(a1.id)!, placed.get(a2.id)!);
    const x = placed.get(a1.id)!.x;
    expect(path.startsWith(`M${x},${placed.get(a1.id)!.y + NODE_H / 2}`)).toBe(true);
    expect(Number(/C(-?[\d.]+)/.exec(path)![1])).toBeLessThan(x);
  });

  it('drops links to hidden kinds', () => {
    const shown = visible([a1, f1, t1], [edge(f1, a1), edge(f1, t1, 'uses_technique')], new Set<NodeType>(['finding', 'technique']));
    expect(shown.nodes.map((n) => n.ref)).toEqual(['FND-1', 'T1021.002']);
    expect(shown.edges.map((e) => e.kind)).toEqual(['uses_technique']);
  });

  it('shortens long labels', () => {
    expect(truncate('a'.repeat(40), 10)).toBe(`${'a'.repeat(9)}…`);
    expect(truncate('short')).toBe('short');
  });
});
