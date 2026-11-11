// The incident graph: assets, observables, exhibits, findings and techniques, and how the record joins
// them. Plain SVG over a layered layout (graphLayout.ts); colours are tokens, set by class.

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Graph as GraphData, GraphNode, NodeType } from '../../api/evidence';
import { edgeWords, nodeTypes } from '../../api/evidence';
import type { Incident } from '../../api/incidents';
import { label as words } from '../../api/incidents';
import { NODE_H, NODE_W, edgePath, layout, truncate, visible } from './graphLayout';
import { refTarget, useSection } from './shared';
import './graph.css';

const typeWord: Record<NodeType, string> = {
  asset: 'Asset',
  observable: 'Observable',
  evidence: 'Exhibit',
  finding: 'Finding',
  technique: 'Technique',
};

const columnNames = ['Assets', 'Observables and exhibits', 'Findings', 'Techniques'];

function nodeClass(node: GraphNode, state: 'none' | 'selected' | 'near' | 'far'): string {
  const status = node.type === 'finding' ? ` status-${String(node.attrs.status)}` : '';
  const flag = node.attrs.patient_zero || node.attrs.hash_mismatch ? ' flagged' : '';
  return `gnode gnode-${node.type}${status}${flag} ${state}`;
}

function Detail({ node, graph }: { node: GraphNode; graph: GraphData }) {
  const links = graph.edges.filter((e) => e.source === node.id || e.target === node.id);
  const name = (id: string) => graph.nodes.find((n) => n.id === id);
  const technique = node.type === 'technique' ? graph.techniques.find((t) => t.technique_id === node.ref) : undefined;
  return (
    <aside className="panel graph-detail" aria-label={`${node.ref} in the graph`}>
      <header>
        <span className="hint">{typeWord[node.type]}</span>
        <h3>
          <span className="mono">{node.ref}</span> {node.type !== 'technique' && node.label}
          {technique?.name && ` ${technique.name}`}
        </h3>
      </header>
      {node.type === 'technique' ? (
        <p>
          {technique && (
            <>
              {technique.tactics.map(words).join(', ') || 'No tactic known'} · {technique.confirmed} confirmed,{' '}
              {technique.leads} {technique.leads === 1 ? 'lead' : 'leads'}
              {technique.connections ? `, ${technique.connections} connections` : ''}.{' '}
            </>
          )}
          <a href={`https://attack.mitre.org/techniques/${node.ref.replace('.', '/')}/`} target="_blank" rel="noreferrer">
            ATT&amp;CK page
          </a>
        </p>
      ) : (
        <p>
          <Link to={refTarget(node.ref)}>Open {node.ref}</Link>
          {node.type === 'finding' && <span className="chip">{String(node.attrs.status)}</span>}
          {node.type === 'asset' && node.attrs.patient_zero ? <span className="chip chip-accent">Patient zero</span> : null}
          {node.type === 'evidence' && node.attrs.holder ? <span className="hint"> held by {String(node.attrs.holder)}</span> : null}
        </p>
      )}
      {links.length > 0 && (
        <ul className="graph-links">
          {links.map((e) => {
            const how = e.kind === 'connection' ? words(String(e.attrs.link_kind)) : edgeWords[e.kind];
            return (
              <li key={e.id}>
                <span className="mono">{name(e.source)?.ref}</span> {how} <span className="mono">{name(e.target)?.ref}</span>
                {e.kind === 'sighted_on' && <span className="hint"> · {String(e.attrs.sightings)} sightings</span>}
                {e.kind === 'connection' && e.attrs.technique ? <span className="hint"> · {String(e.attrs.technique)}</span> : null}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}

export function Graph({ incident }: { incident: Incident }) {
  const graph = useSection<GraphData>(incident, 'graph');
  const [shown, setShown] = useState<Set<NodeType>>(() => new Set(nodeTypes.map(([t]) => t)));
  const [hideRefuted, setHideRefuted] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const view = useMemo(() => {
    if (!graph.data) return null;
    const nodes = graph.data.nodes.filter((n) => !(hideRefuted && n.type === 'finding' && n.attrs.status === 'refuted'));
    const kept = visible(nodes, graph.data.edges, shown);
    return { ...kept, layout: layout(kept.nodes, kept.edges) };
  }, [graph.data, shown, hideRefuted]);

  if (graph.error) return <p className="hint">The graph couldn’t be loaded.</p>;
  if (!graph.data || !view) return null;
  if (!graph.data.nodes.length)
    return <p className="hint">Nothing to draw yet. Assets, observables, findings and exhibits appear here as they’re recorded.</p>;

  const near = new Set<string>();
  if (selected)
    view.edges.forEach((e) => {
      if (e.source === selected) near.add(e.target);
      if (e.target === selected) near.add(e.source);
    });
  const state = (id: string) => (!selected ? 'none' : id === selected ? 'selected' : near.has(id) ? 'near' : 'far');
  const node = selected ? graph.data.nodes.find((n) => n.id === selected) : undefined;
  const toggle = (type: NodeType) =>
    setShown((was) => {
      const next = new Set(was);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  const { placed, width, height } = view.layout;

  return (
    <section className="graph-section">
      <div className="graph-filters" role="group" aria-label="Show in the graph">
        {nodeTypes.map(([type, name]) => (
          <label key={type} className={`check graph-key graph-key-${type}`}>
            <input type="checkbox" checked={shown.has(type)} onChange={() => toggle(type)} />
            <span className="swatch" aria-hidden />
            <span>
              {name} <span className="num hint">{graph.data!.nodes.filter((n) => n.type === type).length}</span>
            </span>
          </label>
        ))}
        <label className="check">
          <input type="checkbox" checked={hideRefuted} onChange={(e) => setHideRefuted(e.target.checked)} />
          <span>Hide refuted findings</span>
        </label>
        {graph.data.masked && <span className="chip">Indicators masked</span>}
      </div>
      <div className="graph-body">
        <div className="graph-canvas">
          <svg
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="group"
            aria-label={`Graph of ${incident.ref}: ${view.nodes.length} nodes, ${view.edges.length} links`}
            onClick={() => setSelected(null)}
          >
            <defs>
              <marker id="graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" className="garrow" />
              </marker>
            </defs>
            {view.layout.columns.map(({ column, x }) => (
              <text key={column} x={x} y={18} className="gcolumn">
                {columnNames[column]}
              </text>
            ))}
            {view.edges.map((e) => {
              const a = placed.get(e.source);
              const b = placed.get(e.target);
              if (!a || !b) return null;
              const lit = selected && (e.source === selected || e.target === selected);
              const cls = `gedge gedge-${e.kind}${e.attrs.status === 'refuted' ? ' refuted' : ''}${lit ? ' lit' : selected ? ' dim' : ''}`;
              return (
                <path key={e.id} d={edgePath(a, b)} className={cls} markerEnd={e.kind === 'connection' ? 'url(#graph-arrow)' : undefined}>
                  <title>
                    {a.node.ref} {e.kind === 'connection' ? words(String(e.attrs.link_kind)) : edgeWords[e.kind]} {b.node.ref}
                  </title>
                </path>
              );
            })}
            {[...placed.values()].map(({ node: n, x, y }) => (
              <g
                key={n.id}
                transform={`translate(${x},${y})`}
                className={nodeClass(n, state(n.id))}
                role="button"
                tabIndex={0}
                aria-pressed={selected === n.id}
                aria-label={`${typeWord[n.type]} ${n.ref}${n.type === 'technique' ? '' : `: ${n.label}`}`}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelected(selected === n.id ? null : n.id);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setSelected(selected === n.id ? null : n.id);
                  }
                }}
              >
                <title>{`${n.ref} ${n.label}`}</title>
                <rect width={NODE_W} height={NODE_H} rx={6} />
                <rect width={5} height={NODE_H} rx={2} className="gband" />
                <text x={12} y={14} className="gref">
                  {n.ref}
                </text>
                <text x={12} y={27} className="glabel">
                  {truncate(n.type === 'technique' ? String(n.attrs.name || n.label) : n.label)}
                </text>
              </g>
            ))}
          </svg>
        </div>
        {node && <Detail node={node} graph={graph.data} />}
      </div>
      {graph.data.techniques.length > 0 && (
        <details className="panel graph-techniques">
          <summary>
            Techniques <span className="num hint">{graph.data.techniques.length}</span>
          </summary>
          <table className="table">
            <thead>
              <tr>
                <th>Technique</th>
                <th>Tactics</th>
                <th className="num">Confirmed</th>
                <th className="num">Leads</th>
                <th className="num">Connections</th>
                <th>Findings</th>
              </tr>
            </thead>
            <tbody>
              {graph.data.techniques.map((t) => (
                <tr key={t.technique_id}>
                  <td>
                    <span className="mono">{t.technique_id}</span> {t.name}
                  </td>
                  <td>{t.tactics.map(words).join(', ')}</td>
                  <td className="num">{t.confirmed}</td>
                  <td className="num">{t.leads}</td>
                  <td className="num">{t.connections}</td>
                  <td className="mono">{t.findings.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
