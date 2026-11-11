// Stakeholder updates: the draft Ostraka built, comms' note and approval, the rhythm, who receives
// them, and every update sent. Observers see the read-only briefing instead.

import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { LintFlag } from '../../api/analysis';
import type { Incident } from '../../api/incidents';
import type { Person, Team } from '../../api/types';
import { bySection, sectionNames } from '../../api/updates';
import type { Briefing as BriefingData, RhythmMode, StakeholderUpdate, UpdateLine, Updates as UpdatesData } from '../../api/updates';
import { Notice, formatDate } from '../../components/ui';
import { Rich, useSave, useSection } from './shared';
import './updates.css';

const SECTIONS = ['updates', 'briefing'];

const changeNames: Record<string, string> = {
  drafted: 'Drafted',
  rebuilt: 'Rebuilt from the record',
  note: 'Note changed',
  wording: 'Wording chosen',
  rephrased: 'Reworded by AI, passed the check',
  sent: 'Approved and sent',
  discarded: 'Discarded',
};

function Flags({ flags }: { flags: LintFlag[] }) {
  if (!flags.length) return null;
  return (
    <ul className="lint" aria-label="Checks">
      {flags.map((f, i) => (
        <li key={`${f.rule}-${f.field}-${i}`} className={`lint-${f.severity}`}>
          <strong>{f.severity === 'error' ? 'Must fix' : 'Check'}</strong> · {f.message}
          {f.words.length > 0 && <span className="mono"> ({f.words.join(', ')})</span>}
        </li>
      ))}
    </ul>
  );
}

// An update's text under its headings; the assessment's first sentence is the bottom line
function Body({ lines, reworded = false }: { lines: UpdateLine[]; reworded?: boolean }) {
  return (
    <div className="update-body">
      {bySection(lines).map(([section, group], i) => (
        <section key={`${section}-${i}`} className={`update-section update-${section}`}>
          {section !== 'position' && section !== 'next' && <h3>{sectionNames[section]}</h3>}
          {section === 'what' ? (
            <ul>
              {group.map((line, j) => (
                <li key={j} className={line.kind === 'lead' ? 'update-lead' : undefined}>
                  <Rich text={(reworded && line.rephrased) || line.text} />
                </li>
              ))}
            </ul>
          ) : (
            group.map((line, j) => (
              <p key={j} className={line.kind === 'assessment' ? 'bluf' : undefined}>
                <Rich text={(reworded && line.rephrased) || line.text} />
              </p>
            ))
          )}
        </section>
      ))}
    </div>
  );
}

// ---- the briefing, for observers and as a preview ---------------------------------------------------

// `anchors`: give each update its #UPD-n id; off when the Sent list beside it already has them
export function Briefing({ incident, anchors = true }: { incident: Incident; anchors?: boolean }) {
  const briefing = useSection<BriefingData>(incident, 'briefing');
  const b = briefing.data;
  if (briefing.error) return <Notice kind="error">{String(briefing.error)}</Notice>;
  if (!b) return null;
  return (
    <div className="briefing" aria-label="Briefing">
      <section className="panel">
        <header className="panel-head">
          <h2>Where things stand</h2>
        </header>
        <dl className="briefing-figures">
          <div>
            <dt>Phase</dt>
            <dd>{b.phase}</dd>
          </div>
          <div>
            <dt>Confirmed findings</dt>
            <dd className="num">{b.confirmed}</dd>
          </div>
          <div>
            <dt>Leads being checked</dt>
            <dd className="num">{b.leads}</dd>
          </div>
          {b.assets > 0 && (
            <div>
              <dt>Assets contained</dt>
              <dd className="num">
                {b.contained} of {b.assets}
              </dd>
            </div>
          )}
          <div>
            <dt>Tasks done</dt>
            <dd className="num">
              {b.tasks_done} of {b.tasks_done + b.tasks_open}
            </dd>
          </div>
        </dl>
        <p className="hint">
          Commander {b.commander} · updates {b.rhythm}
          {b.next_due_at && <> · next due {formatDate(b.next_due_at)}</>}
        </p>
        {b.assessment.length > 0 ? (
          <div className="assessment">
            {b.assessment.map((s, i) => (
              <p key={i} className={i === 0 ? 'bluf' : undefined}>
                <Rich text={s} />
              </p>
            ))}
          </div>
        ) : (
          <p className="hint">No assessment yet.</p>
        )}
      </section>
      <section className="panel">
        <header className="panel-head">
          <h2>Updates sent</h2>
        </header>
        {b.updates.length === 0 && <p className="hint">No update has been sent yet.</p>}
        {b.updates.map((u) => (
          <article key={u.ref} className="record update" id={anchors ? u.ref : undefined}>
            <div className="record-head">
              <span className="mono ref">{u.ref}</span>
              <strong>Update {u.number}</strong>
              <span className="hint">
                {formatDate(u.approved_at)} · approved by {u.approved_by}
              </span>
              {u.ai_worded && <span className="chip">Worded with AI help</span>}
            </div>
            <Body lines={u.lines} />
          </article>
        ))}
      </section>
    </div>
  );
}

// ---- the draft --------------------------------------------------------------------------------------

function Draft({ incident, draft, data }: { incident: Incident; draft: StakeholderUpdate; data: UpdatesData }) {
  const save = useSave(incident, SECTIONS);
  const [note, setNote] = useState(draft.note);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setNote(draft.note), [draft.note, draft.version]);
  const base = `/api/incidents/${incident.id}/updates/${draft.id}`;
  const errors = draft.flags.some((f) => f.severity === 'error');
  const hasRewording = draft.lines.some((l) => l.rephrased);

  const act = (path: string, json: object) => save.run(() => api(`${base}${path}`, { method: 'POST', json }));

  async function saveNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await save.run(() => api(base, { method: 'PATCH', json: { version: draft.version, note } }));
  }

  async function rephrase() {
    setBusy(true);
    const result = await save.run(() =>
      api<{ accepted: boolean; problems: string[] }>(`${base}/rephrase`, { method: 'POST', json: { version: draft.version } }),
    );
    setBusy(false);
    if (result) setProblems(result.accepted ? [] : result.problems);
  }

  return (
    <section className="panel" aria-label={`Draft ${draft.ref}`}>
      <header className="panel-head">
        <h2>
          Draft: update {draft.number} <span className="mono ref">{draft.ref}</span>
        </h2>
        <span className="hint">
          {draft.drafted_by ? `Drafted by ${draft.drafted_by.name}` : 'Drafted by Ostraka on the rhythm'} · covers{' '}
          {formatDate(draft.since)} to {formatDate(draft.until)} · version {draft.version}
        </span>
      </header>
      <p className="hint">
        Ostraka wrote this from the record: the latest assessment first, then what changed, strongest first. Leads are
        labelled as leads. Aim for 4-3-3: four points on what, three on so what, three on what next.
      </p>
      <div className="update-preview" aria-label="What stakeholders will read">
        <Body lines={draft.lines} reworded={draft.use_rephrased} />
      </div>
      <Flags flags={draft.flags} />

      {data.can_run && (
        <form onSubmit={saveNote}>
          <label className="field">
            <span>Your note (optional, linted like the rest)</span>
            <textarea
              className="input"
              rows={2}
              maxLength={3000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              aria-label="Note from comms"
            />
          </label>
          <div className="actions">
            <button className="btn" type="submit" disabled={note === draft.note}>
              Save note
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => act('/rebuild', { version: draft.version })}>
              Rebuild from the record
            </button>
            {data.ai ? (
              <button className="btn btn-quiet" type="button" disabled={busy} onClick={rephrase}>
                {busy ? 'Rewording…' : `Reword with AI (${data.ai})`}
              </button>
            ) : (
              <span className="hint" title={data.ai_why_not}>
                AI rewording unavailable
              </span>
            )}
          </div>
        </form>
      )}
      {problems && problems.length > 0 && (
        <Notice kind="error">
          Ostraka rejected the rewording, so the draft stands as written: {problems.join(' ')}
        </Notice>
      )}
      {hasRewording && data.can_run && (
        <fieldset className="wording">
          <legend>Wording to send</legend>
          <label className="check">
            <input
              type="radio"
              name="wording"
              checked={!draft.use_rephrased}
              onChange={() => save.run(() => api(base, { method: 'PATCH', json: { version: draft.version, use_rephrased: false } }))}
            />
            <span>Ostraka’s own</span>
          </label>
          <label className="check">
            <input
              type="radio"
              name="wording"
              checked={draft.use_rephrased}
              onChange={() => save.run(() => api(base, { method: 'PATCH', json: { version: draft.version, use_rephrased: true } }))}
            />
            <span>The AI rewording ({draft.rephrased_by}), which kept every claim, citation and probability term</span>
          </label>
        </fieldset>
      )}
      {data.restricted && (
        <Notice>
          {incident.ref} is TLP:{incident.tlp.toUpperCase()}: email and Teams carry only the reference. People in Ostraka
          read the update in full.
        </Notice>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button
          className="btn btn-primary"
          type="button"
          disabled={!draft.can_approve}
          title={draft.why_not || undefined}
          onClick={() => act('/approve', { version: draft.version })}
        >
          Approve and send to {data.stakeholders.filter((s) => s.can_receive).length} stakeholder(s)
        </button>
        {data.can_run && (
          <button className="btn btn-quiet" type="button" onClick={() => act('/discard', { version: draft.version })}>
            Discard
          </button>
        )}
        {!draft.can_approve && draft.why_not && <span className="hint">{errors ? 'Fix the errors first.' : draft.why_not}</span>}
      </div>
    </section>
  );
}

// ---- rhythm and stakeholders ------------------------------------------------------------------------

function Rhythm({ incident, data }: { incident: Incident; data: UpdatesData }) {
  const save = useSave(incident, SECTIONS);
  const plan = data.plan;
  const [mode, setMode] = useState<RhythmMode>(plan.mode);
  const [hours, setHours] = useState(plan.every_minutes ? plan.every_minutes / 60 : 4);
  useEffect(() => setMode(plan.mode), [plan.mode, plan.version]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await save.run(() =>
      api(`/api/incidents/${incident.id}/updates/plan`, {
        method: 'PUT',
        json: { mode, every_minutes: mode === 'every' ? Math.round(hours * 60) : null, version: plan.version },
      }),
    );
  }

  return (
    <section className="panel" aria-label="Update rhythm">
      <header className="panel-head">
        <h2>Rhythm</h2>
        <span className="hint">
          Updates go {plan.words}
          {plan.next_due_at && <> · next due {formatDate(plan.next_due_at)}</>}
        </span>
      </header>
      <p className="hint">
        By severity:{' '}
        {plan.by_severity.map((r, i) => (
          <span key={r.severity}>
            {i > 0 && ', '}
            {r.severity} {r.words}
          </span>
        ))}
        . Ostraka drafts the update when it’s due and tells comms.
      </p>
      {data.can_run && (
        <form className="form-row" onSubmit={submit}>
          <label className="field">
            <span>For this incident</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as RhythmMode)} aria-label="Rhythm">
              <option value="severity">The {incident.severity} default</option>
              <option value="every">Every…</option>
              <option value="on_change">On change only</option>
            </select>
          </label>
          {mode === 'every' && (
            <label className="field">
              <span>Hours</span>
              <input
                type="number"
                min={0.5}
                max={168}
                step={0.5}
                value={hours}
                onChange={(e) => setHours(Number(e.target.value))}
                aria-label="Hours between updates"
              />
            </label>
          )}
          <div className="actions">
            <button className="btn" type="submit">
              Set rhythm
            </button>
          </div>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

function Stakeholders({ incident, data }: { incident: Incident; data: UpdatesData }) {
  const save = useSave(incident, SECTIONS);
  const [kind, setKind] = useState<'person' | 'email' | 'teams'>('person');
  const people = useQuery({ queryKey: ['directory'], queryFn: () => api<Person[]>('/api/users'), enabled: data.can_run });
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams'), enabled: data.can_run });
  const base = `/api/incidents/${incident.id}/updates/stakeholders`;

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const json =
      kind === 'person'
        ? { user_id: form.get('user_id') }
        : kind === 'teams'
          ? { team_id: form.get('team_id') }
          : { email: form.get('email'), name: form.get('name') };
    const target = event.currentTarget;
    if ((await save.run(() => api(base, { method: 'POST', json }))) !== undefined) target.reset();
  }

  return (
    <section className="panel" aria-label="Stakeholders">
      <header className="panel-head">
        <h2>Stakeholders</h2>
      </header>
      {data.stakeholders.length === 0 && <p className="hint">Nobody yet. Approved updates go to everyone listed here.</p>}
      <ul className="stakeholders">
        {data.stakeholders.map((s) => (
          <li key={s.id}>
            <span className="chip">{s.kind === 'person' ? 'In Ostraka' : s.kind === 'teams' ? 'Teams' : 'Email'}</span>{' '}
            {s.name}
            {s.kind === 'email' && s.email !== s.name && <span className="hint"> {s.email}</span>}
            {!s.can_receive && <span className="hint"> · {s.why_not}</span>}
            {data.can_run && (
              <button
                className="link-button"
                type="button"
                aria-label={`Remove ${s.name}`}
                onClick={() => save.run(() => api(`${base}/${s.id}`, { method: 'DELETE' }))}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {data.can_run && (
        <form className="form-row" onSubmit={add} aria-label="Add a stakeholder">
          <label className="field">
            <span>Add</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} aria-label="Stakeholder kind">
              <option value="person">Someone in Ostraka</option>
              <option value="email" disabled={!data.email_enabled}>
                An email address{data.email_enabled ? '' : ' (email isn’t set up)'}
              </option>
              <option value="teams">A team’s Teams channel</option>
            </select>
          </label>
          {kind === 'person' && (
            <label className="field">
              <span>Person</span>
              <select name="user_id" required aria-label="Person">
                <option value="">Choose…</option>
                {(people.data ?? [])
                  .filter((p) => p.is_active)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {kind === 'teams' && (
            <label className="field">
              <span>Team</span>
              <select name="team_id" required aria-label="Team">
                <option value="">Choose…</option>
                {(teams.data ?? [])
                  .filter((t) => t.has_channel)
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {kind === 'email' && (
            <>
              <label className="field">
                <span>Email</span>
                <input name="email" type="email" required aria-label="Email address" />
              </label>
              <label className="field">
                <span>Shown as</span>
                <input name="name" maxLength={120} placeholder="The board" />
              </label>
            </>
          )}
          <div className="actions">
            <button className="btn" type="submit">
              Add stakeholder
            </button>
          </div>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

function Sent({ update }: { update: StakeholderUpdate }) {
  return (
    <article className="record update" id={update.ref}>
      <div className="record-head">
        <span className="mono ref">{update.ref}</span>
        <strong>Update {update.number}</strong>
        <span className="hint">
          {formatDate(update.approved_at)} · approved by {update.approved_by?.name}
        </span>
        {update.use_rephrased && <span className="chip">Worded with AI help ({update.rephrased_by})</span>}
      </div>
      <Body lines={update.lines} reworded={update.use_rephrased} />
      <details>
        <summary className="hint">
          Sent to {update.recipients.filter((r) => r.sent).length} of {update.recipients.length} · {update.revisions.length}{' '}
          versions
        </summary>
        <ul className="hint">
          {update.recipients.map((r, i) => (
            <li key={i}>
              {r.to} ({r.channel}){r.sent ? '' : ': not sent'}
            </li>
          ))}
        </ul>
        <ol className="revisions">
          {update.revisions.map((r) => (
            <li key={r.version}>
              v{r.version} · {changeNames[r.change] ?? r.change} · {r.by?.name ?? 'Ostraka'} · {formatDate(r.at)}
            </li>
          ))}
        </ol>
      </details>
    </article>
  );
}

export function Updates({ incident }: { incident: Incident }) {
  if (!incident.can_edit) return <Briefing incident={incident} />;
  return <Workings incident={incident} />;
}

function Workings({ incident }: { incident: Incident }) {
  const updates = useSection<UpdatesData>(incident, 'updates');
  const save = useSave(incident, SECTIONS);
  const data = updates.data;
  if (updates.error) return <Notice kind="error">{String(updates.error)}</Notice>;
  if (!data) return null;
  return (
    <>
      {data.draft ? (
        <Draft key={data.draft.id} incident={incident} draft={data.draft} data={data} />
      ) : (
        <section className="panel">
          <header className="panel-head">
            <h2>Next update</h2>
          </header>
          <p className="hint">
            No draft open. Ostraka drafts one when it’s due ({data.plan.words}); you can draft it now.
          </p>
          {data.can_run && (
            <div className="actions">
              <button
                className="btn btn-primary"
                type="button"
                onClick={() => save.run(() => api(`/api/incidents/${incident.id}/updates`, { method: 'POST' }))}
              >
                Draft an update now
              </button>
            </div>
          )}
          {!data.can_run && <p className="hint">{data.why_not}</p>}
          {save.error && <Notice kind="error">{save.error}</Notice>}
        </section>
      )}
      <Rhythm incident={incident} data={data} />
      <Stakeholders incident={incident} data={data} />
      <section className="panel">
        <header className="panel-head">
          <h2>Sent ({data.sent.length})</h2>
        </header>
        {data.sent.length === 0 && <p className="hint">Nothing sent yet.</p>}
        {[...data.sent].reverse().map((u) => (
          <Sent key={u.id} update={u} />
        ))}
      </section>
      <details className="panel">
        <summary>
          <h2 className="inline">The briefing, as observers see it</h2>
        </summary>
        <Briefing incident={incident} anchors={false} />
      </details>
    </>
  );
}
