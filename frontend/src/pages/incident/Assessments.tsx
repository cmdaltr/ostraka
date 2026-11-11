import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { yardstick } from '../../api/analysis';
import type { Assessment, LintFlag, NegativeEvidence, Probability } from '../../api/analysis';
import type { Finding, Incident } from '../../api/incidents';
import { Assembling, useFinishing } from '../../components/sherd/Ostrakon';
import { Notice, formatDate } from '../../components/ui';
import { Rich, fromLocalInput, refTarget, useSave, useSection } from './shared';

const blankAbsence = (): NegativeEvidence => ({
  absent: '',
  platform: '',
  query: '',
  scope: '',
  time_from: null,
  time_to: null,
});

interface Draft {
  judgement: string;
  probability: Probability;
  finding_ids: string[];
  absences: NegativeEvidence[];
  so_what: string;
  what_next: string;
  supersedes_id: string | null;
}

const fieldNames: Record<string, string> = {
  judgement: 'Judgement',
  probability: 'Likelihood',
  basis: 'Basis',
  so_what: 'So what',
  what_next: 'What next',
};

function Flags({ flags }: { flags: LintFlag[] }) {
  if (!flags.length) return null;
  return (
    <ul className="lint" aria-label="Checks">
      {flags.map((f) => (
        <li key={`${f.rule}-${f.field}`} className={`lint-${f.severity}`}>
          <strong>{f.severity === 'error' ? 'Must fix' : 'Check'}</strong> · {fieldNames[f.field] ?? f.field}:{' '}
          {f.message}
          {f.words.length > 0 && <span className="mono"> ({f.words.join(', ')})</span>}
        </li>
      ))}
    </ul>
  );
}

function Absences({ value, onChange }: { value: NegativeEvidence[]; onChange: (v: NegativeEvidence[]) => void }) {
  const set = (i: number, patch: Partial<NegativeEvidence>) =>
    onChange(value.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  return (
    <fieldset className="absences">
      <legend>Searches that found nothing</legend>
      <p className="hint">
        “No evidence of” must say where someone looked: the platform, the query, and the host or source it covered.
      </p>
      {value.map((a, i) => (
        <div className="absence" key={i}>
          <div className="form-row">
            <label className="field">
              <span>No evidence of</span>
              <input
                aria-label={`No evidence of (${i + 1})`}
                value={a.absent}
                onChange={(e) => set(i, { absent: e.target.value })}
                placeholder="data leaving the network"
                required
              />
            </label>
            <label className="field">
              <span>Platform</span>
              <input value={a.platform} onChange={(e) => set(i, { platform: e.target.value })} placeholder="Defender XDR" required />
            </label>
            <label className="field">
              <span>Scope searched</span>
              <input value={a.scope} onChange={(e) => set(i, { scope: e.target.value })} placeholder="FIN-WS-07" required />
            </label>
          </div>
          <label className="field">
            <span>Query</span>
            <textarea className="input mono" rows={2} value={a.query} onChange={(e) => set(i, { query: e.target.value })} required />
          </label>
          <div className="form-row">
            <label className="field">
              <span>From</span>
              <input type="datetime-local" onChange={(e) => set(i, { time_from: fromLocalInput(e.target.value) })} />
            </label>
            <label className="field">
              <span>Until</span>
              <input type="datetime-local" onChange={(e) => set(i, { time_to: fromLocalInput(e.target.value) })} />
            </label>
            <div className="actions">
              <button className="link-button" type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          </div>
        </div>
      ))}
      <button className="btn btn-quiet" type="button" onClick={() => onChange([...value, blankAbsence()])}>
        Add a search that found nothing
      </button>
    </fieldset>
  );
}

function MakeAssessment({
  incident,
  findings,
  current,
  supersedes,
  onDone,
}: {
  incident: Incident;
  findings: Finding[];
  current: Assessment[];
  supersedes: Assessment | null;
  onDone: () => void;
}) {
  const save = useSave(incident, ['assessments']);
  const [draft, setDraft] = useState<Draft>({
    judgement: supersedes?.judgement ?? '',
    probability: supersedes?.probability ?? 'realistic_possibility',
    finding_ids: supersedes?.basis.map((b) => b.finding_id) ?? [],
    absences: [],
    so_what: supersedes?.so_what ?? '',
    what_next: supersedes?.what_next ?? '',
    supersedes_id: supersedes?.id ?? null,
  });
  const [preview, setPreview] = useState<{ flags: LintFlag[]; sentences: string[] } | null>(null);
  const [linting, setLinting] = useState(false);
  const lintPhase = useFinishing(linting);
  const change = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  // ostraka's own linter, not a model: checked as you write, a moment after you stop typing
  useEffect(() => {
    if (draft.judgement.trim().length < 3) {
      setPreview(null);
      setLinting(false);
      return;
    }
    // Only the latest draft's check counts: an earlier one that answers late is dropped
    let latest = true;
    const timer = setTimeout(() => {
      setLinting(true);
      api<{ flags: LintFlag[]; sentences: string[] }>(`/api/incidents/${incident.id}/assessments/lint`, {
        method: 'POST',
        json: { ...draft, absences: draft.absences.filter((a) => a.absent && a.platform && a.query && a.scope) },
      })
        .then((p) => latest && setPreview(p))
        .catch(() => latest && setPreview(null))
        .finally(() => latest && setLinting(false));
    }, 400);
    return () => {
      latest = false;
      clearTimeout(timer);
    };
  }, [draft, incident.id]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/assessments`, { method: 'POST', json: draft }),
    );
    if (done !== undefined) onDone();
  }

  const usable = findings.filter((f) => f.status !== 'refuted');
  const blocked = preview?.flags.some((f) => f.severity === 'error') ?? true;
  return (
    <form className="panel" onSubmit={submit} aria-label="Make an assessment">
      <header>
        <h2>{supersedes ? `Supersede ${supersedes.ref}` : 'Make an assessment'}</h2>
        <p>
          Write the claim only; Ostraka writes the sentences from these parts. Likelihood comes from the PHIA yardstick,
          and errors must be fixed before it’s saved.
        </p>
      </header>
      <label className="field">
        <span>Judgement</span>
        <textarea
          id="assessment-judgement"
          className="input"
          rows={2}
          value={draft.judgement}
          onChange={(e) => change({ judgement: e.target.value })}
          placeholder="the actor copied the payroll export off the network"
          required
          minLength={3}
          maxLength={2000}
        />
      </label>
      <label className="field">
        <span>Likelihood</span>
        <select
          id="assessment-probability"
          value={draft.probability}
          onChange={(e) => change({ probability: e.target.value as Probability })}
        >
          {yardstick.map(([value, words, range]) => (
            <option key={value} value={value}>
              {words} ({range})
            </option>
          ))}
        </select>
      </label>
      <fieldset className="basis">
        <legend>Based on</legend>
        {usable.length === 0 && <p className="hint">No findings yet. Cite searches that found nothing below instead.</p>}
        {usable.map((f) => (
          <label className="check" key={f.id}>
            <input
              type="checkbox"
              aria-label={`Based on ${f.ref}`}
              checked={draft.finding_ids.includes(f.id)}
              onChange={(e) =>
                change({
                  finding_ids: e.target.checked
                    ? [...draft.finding_ids, f.id]
                    : draft.finding_ids.filter((id) => id !== f.id),
                })
              }
            />
            <span>
              <span className="mono">{f.ref}</span> <span className={`chip status-${f.status}`}>{f.status}</span>{' '}
              <Rich text={f.title} />
            </span>
          </label>
        ))}
      </fieldset>
      <Absences value={draft.absences} onChange={(absences) => change({ absences })} />
      <div className="form-row">
        <label className="field">
          <span>So what</span>
          <textarea className="input" rows={2} value={draft.so_what} onChange={(e) => change({ so_what: e.target.value })} />
        </label>
        <label className="field">
          <span>What next</span>
          <textarea className="input" rows={2} value={draft.what_next} onChange={(e) => change({ what_next: e.target.value })} />
        </label>
      </div>
      {!supersedes && current.length > 0 && (
        <label className="field">
          <span>Replaces</span>
          <select value={draft.supersedes_id ?? ''} onChange={(e) => change({ supersedes_id: e.target.value || null })}>
            <option value="">Nothing: a new judgement</option>
            {current.map((a) => (
              <option key={a.id} value={a.id}>
                {a.ref}: {a.judgement}
              </option>
            ))}
          </select>
        </label>
      )}
      {(preview || lintPhase === 'busy') && (
        <div className="assessment-preview" aria-label="What Ostraka will write">
          <span className="hint">
            What Ostraka will write
            {lintPhase !== 'idle' && (
              <>
                {' '}
                <Assembling inline width="1.4em" done={lintPhase === 'finished'} label="Checking the assessment" />
              </>
            )}
          </span>
          {preview?.sentences.map((s, i) => (
            <p key={i} className={i === 0 ? 'bluf' : undefined}>
              {s}
            </p>
          ))}
          {preview && <Flags flags={preview.flags} />}
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit" disabled={blocked}>
          Save assessment
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AssessmentCard({
  assessment,
  canEdit,
  onSupersede,
}: {
  assessment: Assessment;
  canEdit: boolean;
  onSupersede: () => void;
}) {
  const [bluf, ...rest] = assessment.sentences;
  return (
    <article className="record assessment" id={assessment.ref}>
      <div className="record-head">
        <span className="mono ref">{assessment.ref}</span>
        <span className={`chip probability-${assessment.probability}`}>
          {assessment.probability_words} · {assessment.probability_range}
        </span>
        {assessment.status === 'superseded' && <span className="chip">Superseded</span>}
        {assessment.supersedes && <span className="hint">replaces {assessment.supersedes}</span>}
        <span className="hint">
          {assessment.created_by?.name} · {formatDate(assessment.created_at)}
        </span>
      </div>
      <p className="bluf">
        <Rich text={bluf} />
      </p>
      {rest.map((s, i) => (
        <p key={i}>
          <Rich text={s} />
        </p>
      ))}
      {assessment.basis.length > 0 && (
        <p className="hint">
          Cites{' '}
          {assessment.basis.map((b, i) => (
            <span key={b.finding_id}>
              {i > 0 && ', '}
              <Link className="ref-link mono" to={refTarget(b.ref)}>
                {b.ref}
              </Link>
            </span>
          ))}
        </p>
      )}
      {assessment.absences.length > 0 && (
        <details>
          <summary className="hint">The searches that found nothing</summary>
          {assessment.absences.map((a) => (
            <pre key={a.id} className="query">
              {a.platform} · {a.scope}
              {'\n'}
              {a.query}
            </pre>
          ))}
        </details>
      )}
      <Flags flags={assessment.flags} />
      {canEdit && assessment.status === 'current' && (
        <div className="actions">
          <button className="btn btn-quiet" type="button" onClick={onSupersede}>
            Supersede
          </button>
        </div>
      )}
    </article>
  );
}

export function Assessments({ incident }: { incident: Incident }) {
  const assessments = useSection<Assessment[]>(incident, 'assessments');
  const findings = useSection<Finding[]>(incident, 'findings');
  const [making, setMaking] = useState<Assessment | 'new' | null>(null);
  const all = assessments.data ?? [];
  const current = all.filter((a) => a.status === 'current');
  const superseded = all.filter((a) => a.status === 'superseded');

  return (
    <>
      {making ? (
        <MakeAssessment
          key={making === 'new' ? 'new' : making.id}
          incident={incident}
          findings={findings.data ?? []}
          current={current}
          supersedes={making === 'new' ? null : making}
          onDone={() => setMaking(null)}
        />
      ) : (
        incident.can_edit && (
          <div className="actions">
            <button className="btn btn-primary" type="button" onClick={() => setMaking('new')}>
              Make an assessment
            </button>
          </div>
        )
      )}
      <section className="panel">
        <header className="panel-head">
          <h2>Current assessments</h2>
        </header>
        {current.length === 0 && (
          <p className="hint">
            None yet. Every update needs one: what you judge, how likely, and the evidence behind it.
          </p>
        )}
        {current.map((a) => (
          <AssessmentCard key={a.id} assessment={a} canEdit={incident.can_edit} onSupersede={() => setMaking(a)} />
        ))}
      </section>
      {superseded.length > 0 && (
        <details className="panel">
          <summary>
            <h2 className="inline">How the judgement moved ({superseded.length} superseded)</h2>
          </summary>
          {superseded.map((a) => (
            <AssessmentCard key={a.id} assessment={a} canEdit={false} onSupersede={() => undefined} />
          ))}
        </details>
      )}
    </>
  );
}
