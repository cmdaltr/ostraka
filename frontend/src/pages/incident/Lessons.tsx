import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../api/client';
import type { Lesson, LessonArea, LessonKind } from '../../api/analysis';
import type { Incident } from '../../api/incidents';
import type { Me } from '../../api/types';
import { Notice, formatDate } from '../../components/ui';
import { FollowUp } from './FollowUp';
import { Rich, useSave, useSection } from './shared';

const kindNames: Record<LessonKind, string> = { went_well: 'Went well', improve: 'To improve' };
const areaNames: Record<LessonArea, string> = {
  people: 'People',
  process: 'Process',
  technology: 'Technology',
  detection: 'Detection',
  communication: 'Communication',
};

function AddLesson({ incident }: { incident: Incident }) {
  const save = useSave(incident, ['lessons']);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/lessons`, {
        method: 'POST',
        json: {
          kind: form.get('kind'),
          area: form.get('area'),
          observation: form.get('observation'),
          recommendation: form.get('recommendation'),
        },
      }),
    );
    if (done !== undefined) formEl.reset();
  }
  return (
    <form className="panel" onSubmit={submit} aria-label="Record a lesson">
      <header>
        <h2>Record a lesson</h2>
        <p>What happened, and what we’d keep or change. An improvement can become a task, so it gets done.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Kind</span>
          <select id="lesson-kind" name="kind" defaultValue="improve">
            {(Object.keys(kindNames) as LessonKind[]).map((k) => (
              <option key={k} value={k}>
                {kindNames[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Area</span>
          <select id="lesson-area" name="area" defaultValue="process">
            {(Object.keys(areaNames) as LessonArea[]).map((a) => (
              <option key={a} value={a}>
                {areaNames[a]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>What happened</span>
        <textarea id="lesson-observation" className="input" name="observation" rows={2} required minLength={3} />
      </label>
      <label className="field">
        <span>What we’d keep or change</span>
        <textarea id="lesson-recommendation" className="input" name="recommendation" rows={2} />
      </label>
      {save.error && <Notice kind="error">{save.error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Record lesson
        </button>
      </div>
    </form>
  );
}

function LessonRecord({ lesson, incident, me }: { lesson: Lesson; incident: Incident; me: Me }) {
  const save = useSave(incident, ['lessons', 'tasks']);
  const [tasking, setTasking] = useState(false);
  return (
    <article className="record" id={lesson.ref}>
      <div className="record-head">
        <span className="mono ref">{lesson.ref}</span>
        <span className={`chip lesson-${lesson.kind}`}>{kindNames[lesson.kind]}</span>
        <span className="chip">{areaNames[lesson.area]}</span>
        <span className="hint">
          {lesson.created_by?.name} · {formatDate(lesson.created_at)}
        </span>
      </div>
      <p>
        <Rich text={lesson.observation} />
      </p>
      {lesson.recommendation && (
        <p>
          <strong>{lesson.kind === 'improve' ? 'Change: ' : 'Keep: '}</strong>
          <Rich text={lesson.recommendation} />
        </p>
      )}
      {lesson.task && (
        <p className="hint">
          Follow-up <span className="mono">{lesson.task.ref}</span> ({lesson.task.status.replace('_', ' ')})
        </p>
      )}
      {incident.can_edit && !lesson.task && lesson.kind === 'improve' && (
        tasking ? (
          <FollowUp
            incident={incident}
            me={me}
            label={`Follow-up task for ${lesson.ref}`}
            onCancel={() => setTasking(false)}
            onSubmit={(owner) =>
              save
                .run(() => api(`/api/incidents/${incident.id}/lessons/${lesson.id}/task`, { method: 'POST', json: owner }))
                .then(() => setTasking(false))
            }
          />
        ) : (
          <div className="actions">
            <button className="btn btn-quiet" type="button" onClick={() => setTasking(true)}>
              Make it a task
            </button>
          </div>
        )
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </article>
  );
}

export function Lessons({ incident, me }: { incident: Incident; me: Me }) {
  const lessons = useSection<Lesson[]>(incident, 'lessons');
  const all = lessons.data ?? [];
  return (
    <>
      {(['improve', 'went_well'] as LessonKind[]).map((kind) => {
        const these = all.filter((l) => l.kind === kind);
        return (
          <section className="panel" key={kind}>
            <header className="panel-head">
              <h2>{kind === 'improve' ? 'What we’d change' : 'What went well'}</h2>
            </header>
            {these.length === 0 && <p className="hint">Nothing recorded yet.</p>}
            {these.map((l) => (
              <LessonRecord key={l.id} lesson={l} incident={incident} me={me} />
            ))}
          </section>
        );
      })}
      {incident.can_edit && <AddLesson incident={incident} />}
    </>
  );
}
