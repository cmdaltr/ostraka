import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { Incident, LinkRelation, LinkedRecord, Links as LinksOut } from '../../api/incidents';
import { kindName } from '../../brand';
import { SeverityPill } from '../../components/Pills';
import { Notice } from '../../components/ui';
import { useSave, useSection } from './shared';

const relations: [LinkRelation, string][] = [
  ['related_to', 'is related to'],
  ['child_of', 'is part of (child of)'],
  ['parent_of', 'is the parent of'],
  ['duplicate_of', 'is a duplicate of'],
];

function Linked({ record, onRemove }: { record: LinkedRecord; onRemove?: () => void }) {
  return (
    <li className="linked">
      <Link to={`/incidents/${record.ref}`} className="mono ref-link">
        {record.ref}
      </Link>
      <span className="linked-title">{record.title}</span>
      <SeverityPill level={record.severity} />
      <span className="chip">{record.status === 'closed' ? 'closed' : record.phase}</span>
      {onRemove && (
        <button className="link-button" type="button" onClick={onRemove} aria-label={`Unlink ${record.ref}`}>
          Unlink
        </button>
      )}
    </li>
  );
}

// Where this record sits among others: parent and children, related, duplicates, and hunt promotions
export function Links({ incident }: { incident: Incident }) {
  const links = useSection<LinksOut>(incident, 'record-links', 'links');
  const save = useSave(incident, ['record-links', 'activity']);
  const [relation, setRelation] = useState<LinkRelation>('related_to');
  const base = `/api/incidents/${incident.id}/links`;
  const unlink = incident.can_manage
    ? (r: LinkedRecord) => () => save.run(() => api(`${base}/${r.link_id}`, { method: 'DELETE' }))
    : () => undefined;

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const ref = String(new FormData(form).get('ref') ?? '').trim();
    const done = await save.run(() => api(base, { method: 'POST', json: { ref, relation } }));
    if (done !== undefined) form.reset();
  }

  const l = links.data;
  const groups: [string, LinkedRecord[]][] = l
    ? [
        ['Part of', l.parent ? [l.parent] : []],
        ['Children', l.children],
        ['Promoted from', l.promoted_from ? [l.promoted_from] : []],
        ['Promoted to', l.promoted_to],
        ['Duplicate of', l.duplicate_of ? [l.duplicate_of] : []],
        ['Duplicates of this', l.duplicates],
        ['Related', l.related],
      ]
    : [];
  const shown = groups.filter(([, records]) => records.length);
  const closedChildren = l?.children.filter((c) => c.status === 'closed').length ?? 0;
  const own = kindName(incident.kind);

  return (
    <section className="panel" aria-label="Linked records">
      <header>
        <h2>Linked records</h2>
        <p>Incidents and hunts connected to this {own}.</p>
      </header>
      {l && shown.length === 0 && !l.hidden && <p className="hint">Nothing linked yet.</p>}
      {shown.map(([title, records]) => (
        <div key={title} className="stack">
          <span className="label">
            {title}
            {title === 'Children' && (
              <span className="hint">
                {' '}
                · {closedChildren} of {records.length} closed
              </span>
            )}
          </span>
          <ul className="linked-list">
            {records.map((r) => (
              <Linked
                key={r.link_id}
                record={r}
                // Promotions are history, not a choice to undo
                onRemove={incident.can_manage && !title.startsWith('Promoted') ? unlink(r) : undefined}
              />
            ))}
          </ul>
        </div>
      ))}
      {l && l.hidden > 0 && (
        <p className="hint">
          {l.hidden === 1 ? 'One more linked record' : `${l.hidden} more linked records`} you can’t open.
        </p>
      )}
      {incident.can_manage && (
        <form className="form-row link-form" onSubmit={add} aria-label="Link a record">
          <label className="field">
            <span>This {own}</span>
            <select className="input" value={relation} onChange={(e) => setRelation(e.target.value as LinkRelation)}>
              {relations.map(([value, words]) => (
                <option key={value} value={value}>
                  {words}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Reference</span>
            <input name="ref" required placeholder="OST-2026-014" pattern="#?[A-Za-z]{3}-\d{4}-\d{3,}" />
          </label>
          <button className="btn" type="submit">
            Link
          </button>
        </form>
      )}
      {incident.can_manage && relation === 'duplicate_of' && (
        <p className="hint">Marking this a duplicate closes it; the other record carries on.</p>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}
