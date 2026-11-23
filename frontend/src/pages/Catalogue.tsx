import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { LessonArea, LessonKind } from '../api/analysis';
import {
  askedFor,
  catalogueParams,
  emptyCatalogue,
  emptyLessons,
  isTechnique,
  lessonParams,
  pageWords,
  pages,
  stateNames,
  timeLeft,
} from '../api/catalogue';
import type { CatalogueEntry, CatalogueFilters, LessonFilters, Page, RegisterLesson, WaitingRequest } from '../api/catalogue';
import type { ArchiveState } from '../api/incidents';
import type { Me } from '../api/types';
import type { RecordKind } from '../brand';
import { PillSelect } from '../components/PillSelect';
import { SeverityPill, TlpPill } from '../components/Pills';
import { Notice, messageOf } from '../components/ui';
import { areaNames, kindNames } from './incident/Lessons';
import { Rich } from './incident/shared';
import './catalogue.css';

function useDebounced<T>(value: T, ms = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

const day = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';

function Pager({ page, onPage }: { page: Page<unknown>; onPage: (n: number) => void }) {
  const last = pages(page);
  const paged = page.page > 1 || page.has_more;
  return (
    <nav className="pager" aria-label="Pages">
      <span className="hint" aria-live="polite">
        {pageWords(page)}
      </span>
      {paged && (
        <span className="actions">
          <button className="btn btn-quiet" type="button" disabled={page.page <= 1} onClick={() => onPage(page.page - 1)}>
            Previous
          </button>
          <span className="hint num">{last === null ? `Page ${page.page}` : `Page ${page.page} of ${last}`}</span>
          <button className="btn btn-quiet" type="button" disabled={!page.has_more} onClick={() => onPage(page.page + 1)}>
            Next
          </button>
        </span>
      )}
    </nav>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([k, name]) => (
        <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>
          {name}
        </button>
      ))}
    </div>
  );
}

const kinds: [RecordKind | '', string][] = [
  ['', 'All'],
  ['incident', 'Incidents'],
  ['hunt', 'Hunts'],
];

function Records() {
  const [filters, setFilters] = useState<CatalogueFilters>(emptyCatalogue);
  const [tagText, setTagText] = useState('');
  const set = (change: Partial<CatalogueFilters>) => setFilters((f) => ({ ...f, page: 1, ...change }));
  const settled = useDebounced(filters);
  const params = catalogueParams(settled).toString();
  const found = useQuery({
    queryKey: ['catalogue', 'records', params],
    queryFn: () => api<Page<CatalogueEntry>>(`/api/catalogue?${params}`),
    placeholderData: keepPreviousData,
  });
  const techniqueWrong = filters.technique.trim() !== '' && !isTechnique(filters.technique);
  const filtered = params !== catalogueParams(emptyCatalogue).toString();

  return (
    <>
      <form className="panel catalogue-search" role="search" aria-label="Search the catalogue" onSubmit={(e) => e.preventDefault()}>
        <label className="field catalogue-q">
          <span>Reference, code name or title</span>
          <input
            id="catalogue-q"
            type="search"
            value={filters.q}
            onChange={(e) => set({ q: e.target.value })}
            placeholder="OST-26-014, Folded Lantern, mailbox rule…"
          />
        </label>
        <div className="list-toolbar">
          <Segmented label="Kind" value={filters.kind} options={kinds} onChange={(kind) => set({ kind })} />
          <PillSelect<ArchiveState>
            id="catalogue-state"
            label="State"
            any="Any state"
            value={filters.state}
            onChange={(state) => set({ state })}
            options={(['active', 'archived', 'cold'] as const).map((v) => ({
              value: v,
              name: stateNames[v],
              look: <span className={`chip archive-${v}`}>{stateNames[v]}</span>,
            }))}
          />
        </div>
        <div className="form-row">
          <label className="field">
            <span>Tags</span>
            <input
              id="catalogue-tags"
              value={tagText}
              onChange={(e) => {
                setTagText(e.target.value);
                set({ tags: e.target.value.split(',').map((t) => t.trim()).filter(Boolean) });
              }}
              placeholder="phishing, bec"
            />
          </label>
          <label className="field">
            <span>Technique</span>
            <input
              id="catalogue-technique"
              className="mono"
              value={filters.technique}
              onChange={(e) => set({ technique: e.target.value })}
              placeholder="T1566.001"
              aria-invalid={techniqueWrong}
            />
          </label>
        </div>
        {techniqueWrong && <p className="hint">A whole ATT&CK ID: T1059 also finds its sub-techniques.</p>}
        <div className="form-row">
          <label className="field">
            <span>Opened from</span>
            <input id="catalogue-opened-from" type="date" value={filters.opened_from} onChange={(e) => set({ opened_from: e.target.value })} />
          </label>
          <label className="field">
            <span>Opened to</span>
            <input id="catalogue-opened-to" type="date" value={filters.opened_to} onChange={(e) => set({ opened_to: e.target.value })} />
          </label>
          <label className="field">
            <span>Closed from</span>
            <input id="catalogue-closed-from" type="date" value={filters.closed_from} onChange={(e) => set({ closed_from: e.target.value })} />
          </label>
          <label className="field">
            <span>Closed to</span>
            <input id="catalogue-closed-to" type="date" value={filters.closed_to} onChange={(e) => set({ closed_to: e.target.value })} />
          </label>
        </div>
        {filtered && (
          <div className="actions">
            <button
              className="btn btn-quiet"
              type="button"
              onClick={() => {
                setFilters(emptyCatalogue);
                setTagText('');
              }}
            >
              Clear the search
            </button>
          </div>
        )}
      </form>

      {found.error && <Notice kind="error">{messageOf(found.error)}</Notice>}
      {found.data && (
        <>
          {found.data.items.length === 0 && found.data.page === 1 ? (
            <section className="panel">
              <header>
                <h2>{filtered ? 'Nothing matches' : 'Nothing closed yet'}</h2>
                <p>
                  {filtered
                    ? 'No closed record you can see matches that search.'
                    : 'Incidents and hunts your teams worked appear here once they’re closed.'}
                </p>
              </header>
            </section>
          ) : (
            <ul className="incident-list catalogue-list" aria-label="Closed records">
              {found.data.items.map((i) => (
                <li key={i.id}>
                  <Entry entry={i} />
                </li>
              ))}
            </ul>
          )}
          <Pager page={found.data} onPage={(page) => setFilters((f) => ({ ...f, page }))} />
        </>
      )}
    </>
  );
}

function Entry({ entry: i }: { entry: CatalogueEntry }) {
  const state = i.archive_state ?? 'active';
  return (
    <Link to={`/incidents/${i.ref}`} className={`incident-card sev-edge-${i.severity} kind-${i.kind}`}>
      <div className="incident-card-head">
        <span className="mono ref">{i.ref}</span>
        {i.kind === 'hunt' && <span className="chip chip-hunt">Hunt</span>}
        <SeverityPill level={i.severity} />
        <TlpPill level={i.tlp} />
        <span className={`chip archive-${state}`}>{stateNames[state]}</span>
        <span className="hint">
          opened {day(i.declared_at)} · closed {day(i.closed_at)}
        </span>
      </div>
      <h2>
        {i.codename} <span className="incident-card-title">{i.title}</span>
      </h2>
      <div className="incident-card-foot hint">
        <span>{i.teams.map((t) => t.name).join(', ')}</span>
        {i.techniques.length > 0 && (
          <span className="tags" aria-label="Techniques">
            {i.techniques.map((t) => (
              <span key={t} className="chip mono">
                {t}
              </span>
            ))}
          </span>
        )}
        {i.tags.length > 0 && (
          <span className="tags" aria-label="Tags">
            {i.tags.map((t) => (
              <span key={t} className="chip">
                {t}
              </span>
            ))}
          </span>
        )}
      </div>
    </Link>
  );
}

function LessonsRegister() {
  const [filters, setFilters] = useState<LessonFilters>(emptyLessons);
  const set = (change: Partial<LessonFilters>) => setFilters((f) => ({ ...f, page: 1, ...change }));
  const settled = useDebounced(filters);
  const params = lessonParams(settled).toString();
  const found = useQuery({
    queryKey: ['catalogue', 'lessons', params],
    queryFn: () => api<Page<RegisterLesson>>(`/api/catalogue/lessons?${params}`),
    placeholderData: keepPreviousData,
  });
  const filtered = params !== lessonParams(emptyLessons).toString();

  return (
    <>
      <form className="panel catalogue-search" role="search" aria-label="Search the lessons" onSubmit={(e) => e.preventDefault()}>
        <label className="field catalogue-q">
          <span>Containing</span>
          <input
            id="lessons-q"
            type="search"
            value={filters.q}
            onChange={(e) => set({ q: e.target.value })}
            placeholder="forwarding rule, handover, EDR…"
          />
        </label>
        <div className="list-toolbar">
          <Segmented
            label="Lesson"
            value={filters.kind}
            options={[
              ['', 'All'],
              ['improve', kindNames.improve],
              ['went_well', kindNames.went_well],
            ] as [LessonKind | '', string][]}
            onChange={(kind) => set({ kind })}
          />
          <PillSelect<LessonArea>
            id="lessons-area"
            label="Area"
            any="Any area"
            value={filters.area}
            onChange={(area) => set({ area })}
            options={(Object.keys(areaNames) as LessonArea[]).map((v) => ({ value: v, name: areaNames[v], look: <span className="chip">{areaNames[v]}</span> }))}
          />
          <label className="field field-inline">
            <span>Follow-up</span>
            <select id="lessons-follow-up" value={filters.follow_up} onChange={(e) => set({ follow_up: e.target.value as LessonFilters['follow_up'] })}>
              <option value="">Any</option>
              <option value="none">None yet</option>
              <option value="open">Still open</option>
              <option value="done">Done</option>
            </select>
          </label>
          <Segmented label="Records" value={filters.record_kind} options={kinds} onChange={(record_kind) => set({ record_kind })} />
        </div>
      </form>

      {found.error && <Notice kind="error">{messageOf(found.error)}</Notice>}
      {found.data && (
        <>
          {found.data.items.length === 0 && found.data.page === 1 ? (
            <section className="panel">
              <header>
                <h2>{filtered ? 'No lesson matches' : 'No lessons yet'}</h2>
                <p>
                  {filtered
                    ? 'No lesson in a record you can see matches that search.'
                    : 'What went well and what to change, from every record you can see, gathers here.'}
                </p>
              </header>
            </section>
          ) : (
            <section className="panel" aria-label="Lessons">
              {found.data.items.map((lesson) => (
                <RegisterEntry key={lesson.id} lesson={lesson} />
              ))}
            </section>
          )}
          <Pager page={found.data} onPage={(page) => setFilters((f) => ({ ...f, page }))} />
        </>
      )}
    </>
  );
}

function RegisterEntry({ lesson }: { lesson: RegisterLesson }) {
  const r = lesson.record;
  return (
    <article className="record" aria-label={lesson.ref}>
      <div className="record-head">
        <span className="mono ref">{lesson.ref}</span>
        <span className={`chip lesson-${lesson.kind}`}>{kindNames[lesson.kind]}</span>
        <span className="chip">{areaNames[lesson.area]}</span>
        <Link className="ref-link" to={`/incidents/${r.ref}?s=overview#${lesson.ref}`}>
          <span className="mono">{r.ref}</span> {r.codename}
        </Link>
        {r.archive_state !== 'active' && <span className={`chip archive-${r.archive_state}`}>{stateNames[r.archive_state]}</span>}
        <span className="hint">{day(lesson.created_at)}</span>
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
    </article>
  );
}

function Waiting({ me }: { me: Me }) {
  const waiting = useQuery({
    queryKey: ['archive-requests'],
    queryFn: () => api<WaitingRequest[]>('/api/archive-requests'),
    enabled: me.role !== 'observer',
  });
  if (!waiting.data?.length) return null;
  return (
    <section className="panel" aria-labelledby="requests-head">
      <header>
        <h2 id="requests-head">Waiting for approval</h2>
        <p className="hint">Requests to archive or restore a record. Each lapses 24 hours after it was made; open the record to approve or decline it.</p>
      </header>
      {waiting.data.map(({ record, request }) => (
        <article className="record" key={request.id}>
          <div className="record-head">
            <Link className="ref-link" to={`/incidents/${record.ref}`}>
              <span className="mono">{record.ref}</span> {record.codename}
            </Link>
            <span className={`chip archive-${record.archive_state}`}>{stateNames[record.archive_state]}</span>
            <span className="hint">
              {request.can_withdraw ? 'You asked' : `${request.requested_by?.name ?? 'A former member'} asked`} to{' '}
              {askedFor(request)} · {timeLeft(request.expires_at)}
            </span>
          </div>
          <p>{request.reason}</p>
        </article>
      ))}
    </section>
  );
}

export function Catalogue({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const view = params.get('view') === 'lessons' ? 'lessons' : 'records';
  const tabs: [typeof view, ReactNode][] = [
    ['records', 'Records'],
    ['lessons', 'Lessons register'],
  ];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Catalogue</h1>
          <p className="hint">
            {view === 'records'
              ? 'Closed incidents and hunts, archived after 30 days and in cold store after 90 unless a record says otherwise.'
              : 'Every lesson from the records you can see, open or closed.'}
          </p>
        </div>
      </div>
      <nav className="tabs" aria-label="Catalogue">
        {tabs.map(([key, name]) => (
          <button
            key={key}
            type="button"
            aria-current={view === key ? 'page' : undefined}
            onClick={() => setParams(key === 'records' ? {} : { view: key }, { replace: true })}
          >
            {name}
          </button>
        ))}
      </nav>
      <Waiting me={me} />
      {view === 'records' ? <Records /> : <LessonsRegister />}
    </>
  );
}
