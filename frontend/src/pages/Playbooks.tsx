import { useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api } from '../api/client';
import { anatomyParts } from '../api/flow';
import { ANY, partsOf } from '../api/work';
import type { Playbook } from '../api/work';
import type { Me, Team } from '../api/types';
import type { RecordKind } from '../brand';
import { PlaybookAnatomy, checksIn } from '../components/PlaybookAnatomy';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, messageOf } from '../components/ui';
import { teamColour, teamSlots } from '../components/teamColour';
import { splitTags } from './incident/shared';
import './incident/incident.css';
import './playbooks.css';

// A playbook is checks on the parts of an attack (backend ostraka/playbook.py): a check is one line with a
// team, the order within a part is the order of the work, and a part may say "only if…"

interface Check {
  id: number; // this page's own, for React and for moving lines
  part: string;
  title: string;
  team_id: string;
}

const ACTIONS = 'actions'; // the Actions node: choosing it shows the actions to choose from
const partName = (key: string) => (key === ANY ? 'Not tied to a step' : (anatomyParts.find((p) => p.key === key)?.name ?? key));
const partSays = (key: string) =>
  key === ANY ? 'Checks that aren’t about the attack: people to tell, approvals, notices' : (anatomyParts.find((p) => p.key === key)?.says ?? '');
const order = [...anatomyParts.map((p) => p.key), ANY];
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

let made = 0;
const line = (part: string, title: string, team_id = ''): Check => ({ id: ++made, part, title, team_id });

function Editor({ playbook, teams, onDone }: { playbook: Playbook | null; teams: Team[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [checks, setChecks] = useState<Check[]>(() => playbook?.checks.map((c) => line(c.part, c.title, c.team?.id ?? '')) ?? []);
  const [conditions, setConditions] = useState<Record<string, string>>(() => ({ ...(playbook?.conditions ?? {}) }));
  const [kind, setKind] = useState<RecordKind>(playbook?.applies_to ?? 'incident');
  const [selected, setSelected] = useState<string>(() => (playbook ? (playbook.checks[0]?.part ?? ANY) : 'phishing'));
  const [adding, setAdding] = useState('');
  const [error, setError] = useState<string | null>(null);

  const draft = { checks: checks.map((c) => ({ part: c.part, title: c.title, team: null })), conditions };
  const inPart = checks.filter((c) => c.part === selected);
  const slots = teamSlots(checks.flatMap((c) => (c.team_id ? [c.team_id] : [])));
  const attackParts = new Set(checks.filter((c) => c.part !== ANY).map((c) => c.part));

  const edit = (id: number, patch: Partial<Check>) => setChecks(checks.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  const move = (id: number, by: number) => {
    const ids = inPart.map((c) => c.id);
    const i = ids.indexOf(id);
    const other = ids[i + by];
    if (other === undefined) return;
    const a = checks.findIndex((c) => c.id === id);
    const b = checks.findIndex((c) => c.id === other);
    const next = [...checks];
    [next[a], next[b]] = [next[b], next[a]];
    setChecks(next);
  };
  const takeOut = () => {
    setChecks(checks.filter((c) => c.part !== selected));
    const { [selected]: _, ...rest } = conditions;
    setConditions(rest);
  };
  const add = () => {
    if (adding.trim().length < 3) return;
    setChecks([...checks, line(selected, adding.trim())]);
    setAdding('');
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const json = {
      name: form.get('name'),
      description: form.get('description'),
      trigger_tags: splitTags(String(form.get('trigger_tags') ?? '')),
      applies_to: form.get('applies_to'),
      is_active: form.get('is_active') === 'on',
      conditions,
      checks: [...checks]
        .sort((a, b) => order.indexOf(a.part) - order.indexOf(b.part))
        .map((c) => ({ part: c.part, title: c.title.trim(), team_id: c.team_id || null })),
    };
    if (!json.checks.length) {
      setError('Give it at least one check.');
      return;
    }
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

  const actionPicker = (
    <div className="pb-pickers" role="group" aria-label="Actions">
      {anatomyParts
        .filter((p) => p.column === 'action')
        .map((p) => {
          const n = checks.filter((c) => c.part === p.key).length;
          return (
            <button key={p.key} type="button" className={n ? 'has' : ''} aria-pressed={selected === p.key} onClick={() => setSelected(p.key)}>
              {p.name}
              {n ? ` · ${n}` : ''}
            </button>
          );
        })}
    </div>
  );
  const isAction = anatomyParts.find((p) => p.key === selected)?.column === 'action';
  const when = conditions[selected];

  return (
    <form className="panel" onSubmit={submit} aria-label={playbook ? `Edit ${playbook.name}` : 'New playbook'}>
      <header>
        <h2>{playbook ? `Edit ${playbook.name}` : 'New playbook'}</h2>
        <p>Changes apply the next time it’s used; tasks it has already made stay as they are.</p>
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

      <div className="field">
        <span>How it plays out</span>
        <p className="hint">
          How the attack goes, left to right: how they get in, what they run, what they do and where it goes. Select a part to
          give it checks; only the parts with checks are in the playbook, and the rest stay dashed.
        </p>
        <PlaybookAnatomy
          playbook={draft}
          mode="edit"
          chosen={(key) => key === selected || (key === ACTIONS && isAction)}
          choose={(key) => setSelected(key === ACTIONS ? (isAction ? selected : 'steals_logins') : key)}
        />
        <p className="pb-totals hint">
          {plural(checks.filter((c) => c.part !== ANY).length, 'check')} in {plural(attackParts.size, 'part')} of the attack, and{' '}
          <button className="link-button" type="button" aria-pressed={selected === ANY} onClick={() => setSelected(ANY)}>
            {plural(checks.filter((c) => c.part === ANY).length, 'check')} not tied to a step
          </button>
        </p>
      </div>

      <section className="pb-part" aria-label={partName(selected)}>
        {isAction && actionPicker}
        <div className="pb-part-head">
          <div>
            <h3>{partName(selected)}</h3>
            <p className="hint">{partSays(selected)}</p>
          </div>
          <span className="actions">
            {selected !== ANY && when === undefined && (
              <button className="link-button" type="button" onClick={() => setConditions({ ...conditions, [selected]: '' })}>
                Only if…
              </button>
            )}
            {inPart.length > 0 && (
              <button className="link-button" type="button" onClick={takeOut}>
                Take out of the playbook
              </button>
            )}
          </span>
        </div>
        {selected !== ANY && when !== undefined && (
          <div className="pb-condition">
            <label className="field">
              <span>Only if</span>
              <input
                className="input"
                value={when}
                maxLength={200}
                placeholder="someone entered credentials"
                autoFocus={!when}
                onChange={(e) => setConditions({ ...conditions, [selected]: e.target.value })}
              />
            </label>
            <button
              className="link-button"
              type="button"
              onClick={() => {
                const { [selected]: _, ...rest } = conditions;
                setConditions(rest);
              }}
            >
              Always
            </button>
            <p className="hint">Applying the playbook leaves this part out unless whoever applies it picks it.</p>
          </div>
        )}
        <ol className="pb-checks" aria-label={`Checks for ${partName(selected)}`}>
          {inPart.map((c, i) => (
            <li key={c.id} style={{ '--team': teamColour(c.team_id ? slots.get(c.team_id) : undefined) } as CSSProperties}>
              <input
                className="input"
                aria-label={`Check ${i + 1}`}
                value={c.title}
                required
                minLength={3}
                maxLength={200}
                onChange={(e) => edit(c.id, { title: e.target.value })}
              />
              <select className="input" aria-label={`Team for check ${i + 1}`} value={c.team_id} onChange={(e) => edit(c.id, { team_id: e.target.value })}>
                <option value="">Whoever applies it</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              <span className="pb-moves">
                <button className="icon-button" type="button" aria-label={`Move check ${i + 1} up`} disabled={i === 0} onClick={() => move(c.id, -1)}>
                  ↑
                </button>
                <button
                  className="icon-button"
                  type="button"
                  aria-label={`Move check ${i + 1} down`}
                  disabled={i === inPart.length - 1}
                  onClick={() => move(c.id, 1)}
                >
                  ↓
                </button>
                <button className="icon-button" type="button" aria-label={`Remove check ${i + 1}`} onClick={() => setChecks(checks.filter((x) => x.id !== c.id))}>
                  ×
                </button>
              </span>
            </li>
          ))}
        </ol>
        {/* A form of its own would nest inside the editor's, so Enter is caught here instead */}
        <input
          className="input"
          aria-label={`Add a check to ${partName(selected)}`}
          value={adding}
          placeholder="Add a check, then Enter"
          maxLength={200}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
        />
      </section>

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

// Its size at a glance: checks, parts of the attack, teams
function PlaybookSummary({ playbook }: { playbook: Playbook }) {
  const teams = new Set(playbook.checks.flatMap((c) => (c.team ? [c.team.id] : [])));
  const parts = [...partsOf(playbook)].filter((p) => p !== ANY);
  const loose = checksIn(playbook, ANY);
  const conditional = parts.filter((p) => playbook.conditions[p]).length;
  return (
    <p className="playbook-summary hint">
      <span>{plural(playbook.checks.length, 'check')}</span>
      <span>{plural(parts.length, 'part')} of the attack</span>
      {conditional > 0 && <span>{conditional} only if…</span>}
      {loose > 0 && <span>{loose} not tied to a step</span>}
      <span>{plural(teams.size, 'team')}</span>
    </p>
  );
}

function Card({ playbook, onEdit, writes }: { playbook: Playbook; onEdit: () => void; writes: boolean }) {
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attack = [...partsOf(playbook)].some((p) => p !== ANY);
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
      <PlaybookSummary playbook={playbook} />
      {attack && <PlaybookAnatomy playbook={playbook} mode="view" label={`${playbook.name} on the attack’s anatomy`} />}
      {writes && (
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
      )}
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
            Checks to work through, drawn on the attack’s anatomy: how they get in, what they run, what they do and where it
            goes. Applying one takes the parts that fit the record, and each check becomes a task on that part of its attack
            flow. A playbook’s tags suggest it when someone declares an incident or starts a hunt.
          </p>
        </div>
        {writes && !editing && (
          <div className="actions">
            <button className="btn btn-primary" type="button" onClick={() => setEditing('new')}>
              New playbook
            </button>
          </div>
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
            <Card key={p.id} playbook={p} writes={writes} onEdit={() => setEditing(p)} />
          ))}
        </ul>
      </Loading>
    </>
  );
}
