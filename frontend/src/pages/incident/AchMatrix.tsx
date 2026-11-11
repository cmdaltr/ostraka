import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Ach, AchCell, AchEvidence, EvidenceKind, Hypothesis, Rating, Weight } from '../../api/analysis';
import { keys } from '../../api/incidents';
import type { Finding, Incident, IncidentObservable } from '../../api/incidents';
import type { Me } from '../../api/types';
import { Notice } from '../../components/ui';
import { FollowUp } from './FollowUp';
import { Rich, useSave, useSection } from './shared';

const ratingNames: Record<Rating, string> = { c: 'C', i: 'I', na: 'N/A' };
const weightNames: Record<Weight, string> = { h: 'High', m: 'Medium', l: 'Low' };
const kindNames: Record<EvidenceKind, string> = {
  finding: 'Finding',
  observable: 'Observable',
  assumption: 'Assumption',
  absence: 'Absent',
};

// Every change returns the whole matrix, so the cache takes it as it comes back
function useMatrix(incident: Incident) {
  const queryClient = useQueryClient();
  const save = useSave(incident, []);
  const put = (path: string, method: string, json?: unknown) =>
    save.run(async () => {
      const ach = await api<Ach>(`/api/incidents/${incident.id}/ach${path}`, { method, json });
      queryClient.setQueryData(keys.section(incident.id, 'ach'), ach);
      return ach;
    });
  return { put, error: save.error };
}

function HypothesisHead({
  hypothesis,
  incident,
  me,
  put,
}: {
  hypothesis: Hypothesis;
  incident: Incident;
  me: Me;
  put: ReturnType<typeof useMatrix>['put'];
}) {
  const [rejecting, setRejecting] = useState(false);
  const [tasking, setTasking] = useState(false);
  const rejected = hypothesis.status === 'rejected';

  async function reject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = new FormData(event.currentTarget).get('reason');
    if (await put(`/hypotheses/${hypothesis.id}/reject`, 'POST', { version: hypothesis.version, reason }))
      setRejecting(false);
  }

  return (
    <th scope="col" className={rejected ? 'hypothesis rejected' : 'hypothesis'} id={hypothesis.ref}>
      <span className="mono ref">{hypothesis.ref}</span>
      {hypothesis.leading && <span className="chip chip-accent">Leading</span>}
      {rejected && <span className="chip">Rejected</span>}
      <div className="statement">
        <Rich text={hypothesis.statement} />
      </div>
      <div className="score num" title="Weighted inconsistency: nearer zero is better">
        {hypothesis.score.toFixed(2)}
      </div>
      {rejected && <p className="hint">Why: {hypothesis.rejection_reason}</p>}
      {hypothesis.milestone && (
        <p className="hint">
          Would change our view: {hypothesis.milestone}
          {hypothesis.milestone_task && <span className="mono"> ({hypothesis.milestone_task.ref})</span>}
        </p>
      )}
      {incident.can_edit && (
        <div className="actions">
          {!rejected && !rejecting && (
            <button className="link-button" type="button" onClick={() => setRejecting(true)}>
              Reject
            </button>
          )}
          {rejected && (
            <button
              className="link-button"
              type="button"
              onClick={() => put(`/hypotheses/${hypothesis.id}/reopen`, 'POST', { version: hypothesis.version })}
            >
              Reopen
            </button>
          )}
          {hypothesis.milestone && !hypothesis.milestone_task && !tasking && (
            <button className="link-button" type="button" onClick={() => setTasking(true)}>
              Make the milestone a task
            </button>
          )}
        </div>
      )}
      {rejecting && (
        <form className="reject" onSubmit={reject} aria-label={`Reject ${hypothesis.ref}`}>
          <textarea className="input" name="reason" rows={2} required minLength={3} placeholder="Why it’s rejected" />
          <div className="actions">
            <button className="btn" type="submit">
              Reject
            </button>
            <button className="btn btn-quiet" type="button" onClick={() => setRejecting(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {tasking && (
        <FollowUp
          incident={incident}
          me={me}
          label={`Task for ${hypothesis.ref}'s milestone`}
          onCancel={() => setTasking(false)}
          onSubmit={(owner) =>
            put(`/hypotheses/${hypothesis.id}/milestone-task`, 'POST', owner).then(() => setTasking(false))
          }
        />
      )}
    </th>
  );
}

function Cell({
  cell,
  hypothesis,
  evidence,
  canRate,
  put,
}: {
  cell: AchCell | undefined;
  hypothesis: Hypothesis;
  evidence: AchEvidence;
  canRate: boolean;
  put: ReturnType<typeof useMatrix>['put'];
}) {
  const others = cell?.ratings.map((r) => `${r.rater.name}: ${ratingNames[r.value]}`).join(', ');
  const consensus = cell?.consensus;
  return (
    <td className={`ach-cell${consensus ? ` rating-${consensus}` : ''}${cell?.disputed ? ' disputed' : ''}`} title={others}>
      {canRate && hypothesis.status === 'open' ? (
        <select
          className="input"
          aria-label={`Your rating of ${evidence.ref} against ${hypothesis.ref}`}
          value={cell?.mine ?? ''}
          onChange={(e) =>
            put('/ratings', 'PUT', {
              hypothesis_id: hypothesis.id,
              evidence_id: evidence.id,
              value: e.target.value || null,
            })
          }
        >
          <option value="">–</option>
          <option value="c">C</option>
          <option value="i">I</option>
          <option value="na">N/A</option>
        </select>
      ) : (
        <span>{consensus ? ratingNames[consensus] : '–'}</span>
      )}
      {cell && cell.ratings.length > 1 && (
        <span className="hint consensus">
          {cell.disputed ? 'disputed' : `all ${consensus ? ratingNames[consensus] : ''}`}
        </span>
      )}
    </td>
  );
}

function EvidenceHead({
  evidence,
  canEdit,
  put,
}: {
  evidence: AchEvidence;
  canEdit: boolean;
  put: ReturnType<typeof useMatrix>['put'];
}) {
  const weigh = (field: 'credibility' | 'relevance', value: string) =>
    put(`/evidence/${evidence.id}`, 'PATCH', { version: evidence.version, [field]: value });
  return (
    <th scope="row" className="evidence" id={evidence.ref}>
      <span className="mono ref">{evidence.ref}</span> <span className="chip">{kindNames[evidence.kind]}</span>
      <div>
        <Rich text={evidence.text} />
      </div>
      <div className="weights">
        {(['credibility', 'relevance'] as const).map((field) =>
          canEdit ? (
            <label key={field} className="hint">
              {field === 'credibility' ? 'Credibility' : 'Relevance'}{' '}
              <select
                className="input inline"
                aria-label={`${field} of ${evidence.ref}`}
                value={evidence[field]}
                onChange={(e) => weigh(field, e.target.value)}
              >
                {(Object.keys(weightNames) as Weight[]).map((w) => (
                  <option key={w} value={w}>
                    {weightNames[w]}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <span key={field} className="hint">
              {field === 'credibility' ? 'Credibility' : 'Relevance'} {weightNames[evidence[field]]}{' '}
            </span>
          ),
        )}
      </div>
      {evidence.non_diagnostic && <p className="hint flag">Rated the same against every hypothesis: it can’t tell them apart.</p>}
      {evidence.pivotal && (
        <p className="hint flag">
          {evidence.lead_without
            ? `If this were wrong, ${evidence.lead_without} would lead.`
            : 'If this were wrong, no hypothesis would lead outright.'}
        </p>
      )}
      {canEdit && (
        <button className="link-button" type="button" onClick={() => put(`/evidence/${evidence.id}`, 'DELETE')}>
          Remove
        </button>
      )}
    </th>
  );
}

function AddHypothesis({ put }: { put: ReturnType<typeof useMatrix>['put'] }) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    if (await put('/hypotheses', 'POST', { statement: form.get('statement'), milestone: form.get('milestone') }))
      formEl.reset();
  }
  return (
    <form className="panel" onSubmit={submit} aria-label="Add a hypothesis">
      <header>
        <h2>Add a hypothesis</h2>
        <p>Include the ones you think are wrong: ACH works by eliminating them.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Hypothesis</span>
          <input id="hypothesis-statement" name="statement" required minLength={3} maxLength={2000} />
        </label>
        <label className="field">
          <span>What would change our view (optional)</span>
          <input name="milestone" maxLength={2000} placeholder="HR confirms who requested the export" />
        </label>
      </div>
      <div className="actions">
        <button className="btn" type="submit">
          Add hypothesis
        </button>
      </div>
    </form>
  );
}

function AddEvidence({ incident, put }: { incident: Incident; put: ReturnType<typeof useMatrix>['put'] }) {
  const [kind, setKind] = useState<EvidenceKind>('finding');
  const findings = useSection<Finding[]>(incident, 'findings');
  const observables = useSection<IncidentObservable[]>(incident, 'observables');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const done = await put('/evidence', 'POST', {
      kind,
      finding_id: kind === 'finding' ? form.get('finding_id') : null,
      observable_id: kind === 'observable' ? form.get('observable_id') : null,
      text: form.get('text') ?? '',
      credibility: form.get('credibility'),
      relevance: form.get('relevance'),
    });
    if (done) formEl.reset();
  }
  return (
    <form className="panel" onSubmit={submit} aria-label="Add evidence">
      <header>
        <h2>Add evidence</h2>
        <p>Findings, observables, assumptions, and things you’d expect to see under a hypothesis but don’t.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Kind</span>
          <select id="evidence-kind" value={kind} onChange={(e) => setKind(e.target.value as EvidenceKind)}>
            {(Object.keys(kindNames) as EvidenceKind[]).map((k) => (
              <option key={k} value={k}>
                {kindNames[k]}
              </option>
            ))}
          </select>
        </label>
        {kind === 'finding' && (
          <label className="field">
            <span>Finding</span>
            <select name="finding_id" required>
              {(findings.data ?? []).map((f) => (
                <option key={f.id} value={f.id}>
                  {f.ref} ({f.status}): {f.title}
                </option>
              ))}
            </select>
          </label>
        )}
        {kind === 'observable' && (
          <label className="field">
            <span>Observable</span>
            <select name="observable_id" required>
              {(observables.data ?? []).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.ref}: {o.value ?? o.kind}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Credibility</span>
          <select name="credibility" defaultValue="m">
            {(Object.keys(weightNames) as Weight[]).map((w) => (
              <option key={w} value={w}>
                {weightNames[w]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Relevance</span>
          <select name="relevance" defaultValue="m">
            {(Object.keys(weightNames) as Weight[]).map((w) => (
              <option key={w} value={w}>
                {weightNames[w]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>{kind === 'finding' || kind === 'observable' ? 'In other words (optional)' : 'Evidence'}</span>
        <input
          id="evidence-text"
          name="text"
          maxLength={2000}
          required={kind === 'assumption' || kind === 'absence'}
          placeholder={kind === 'absence' ? 'No change ticket for the export' : undefined}
        />
      </label>
      <div className="actions">
        <button className="btn" type="submit">
          Add evidence
        </button>
      </div>
    </form>
  );
}

export function AchMatrix({ incident, me }: { incident: Incident; me: Me }) {
  const matrix = useSection<Ach>(incident, 'ach');
  const { put, error } = useMatrix(incident);
  const ach = matrix.data;
  if (!ach) return matrix.error ? <Notice kind="error">{String(matrix.error)}</Notice> : null;
  const cell = (h: string, e: string) => ach.cells.find((c) => c.hypothesis_id === h && c.evidence_id === e);
  const canRate =
    incident.can_edit && (ach.raters.some((r) => r.id === me.id) || ach.raters.length < ach.max_raters);

  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Analysis of Competing Hypotheses</h2>
          <span className="hint">
            {ach.leading ? `${ach.leading} leads` : ach.hypotheses.length ? 'No hypothesis leads outright' : ''}
            {ach.raters.length > 0 && ` · rated by ${ach.raters.map((r) => r.name).join(', ')}`}
          </span>
        </header>
        <p className="hint">
          Rate each piece of evidence against each hypothesis: C consistent, I inconsistent, N/A not applicable. The
          hypothesis with the least evidence against it leads, not the one with the most for it.
        </p>
        {ach.hypotheses.length > 0 && ach.evidence.length > 0 ? (
          <div className="table-wrap">
            <table className="table ach" aria-label="ACH matrix">
              <thead>
                <tr>
                  <th scope="col">Evidence</th>
                  {ach.hypotheses.map((h) => (
                    <HypothesisHead key={h.id} hypothesis={h} incident={incident} me={me} put={put} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {ach.evidence.map((e) => (
                  <tr key={e.id}>
                    <EvidenceHead evidence={e} canEdit={incident.can_edit} put={put} />
                    {ach.hypotheses.map((h) => (
                      <Cell key={h.id} cell={cell(h.id, e.id)} hypothesis={h} evidence={e} canRate={canRate} put={put} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="hint">Add at least one hypothesis and one piece of evidence to start the matrix.</p>
        )}
        {incident.can_edit && !canRate && (
          <Notice>{ach.max_raters} analysts are already rating this matrix, the most it takes.</Notice>
        )}
        {error && <Notice kind="error">{error}</Notice>}
        <p className="hint algorithm">{ach.algorithm}</p>
      </section>
      {incident.can_edit && (
        <>
          <AddHypothesis put={put} />
          <AddEvidence incident={incident} put={put} />
        </>
      )}
    </>
  );
}
