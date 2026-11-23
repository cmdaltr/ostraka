// The catalogue of closed records and the lessons register (backend/ostraka/api/catalogue.py)

import type { RecordKind } from '../brand';
import type { Lesson, LessonArea, LessonKind } from './analysis';
import type { Archive, ArchiveRequest, ArchiveState, IncidentSummary } from './incidents';

export interface CatalogueEntry extends IncidentSummary {
  closed_at: string | null;
  archived_at: string | null;
  cold_at: string | null;
  techniques: string[];
}

export interface Page<T> {
  items: T[];
  // null: an observer's masked search, not counted to the end
  total: number | null;
  has_more: boolean;
  page: number;
  per_page: number;
}

export interface RecordRef {
  id: string;
  ref: string;
  kind: RecordKind;
  codename: string;
  title: string;
  archive_state: ArchiveState;
  closed_at: string | null;
}

export interface RegisterLesson extends Lesson {
  record: RecordRef;
}

export interface WaitingRequest {
  record: RecordRef;
  request: ArchiveRequest;
}

export interface CatalogueFilters {
  q: string;
  kind: RecordKind | '';
  state: ArchiveState[];
  tags: string[];
  technique: string;
  opened_from: string;
  opened_to: string;
  closed_from: string;
  closed_to: string;
  page: number;
}

export interface LessonFilters {
  q: string;
  area: LessonArea[];
  kind: LessonKind | '';
  follow_up: '' | 'none' | 'open' | 'done';
  record_kind: RecordKind | '';
  page: number;
}

export const PER_PAGE = 25;

export const emptyCatalogue: CatalogueFilters = {
  q: '',
  kind: '',
  state: [],
  tags: [],
  technique: '',
  opened_from: '',
  opened_to: '',
  closed_from: '',
  closed_to: '',
  page: 1,
};

export const emptyLessons: LessonFilters = { q: '', area: [], kind: '', follow_up: '', record_kind: '', page: 1 };

const TECHNIQUE = /^T\d{4}(\.\d{3})?$/i;

/** Whether a technique is one the catalogue can search: a whole ATT&CK ID */
export function isTechnique(text: string): boolean {
  return TECHNIQUE.test(text.trim());
}

/** The catalogue's query string: only what's set, the technique sent once it's a whole ID */
export function catalogueParams(f: CatalogueFilters, perPage = PER_PAGE): URLSearchParams {
  const params = new URLSearchParams();
  if (f.q.trim()) params.set('q', f.q.trim());
  if (f.kind) params.set('kind', f.kind);
  for (const s of f.state) params.append('state', s);
  for (const t of f.tags) if (t.trim()) params.append('tag', t.trim().toLowerCase());
  if (isTechnique(f.technique)) params.set('technique', f.technique.trim().toUpperCase());
  for (const key of ['opened_from', 'opened_to', 'closed_from', 'closed_to'] as const) if (f[key]) params.set(key, f[key]);
  if (f.page > 1) params.set('page', String(f.page));
  params.set('per_page', String(perPage));
  return params;
}

export function lessonParams(f: LessonFilters, perPage = PER_PAGE): URLSearchParams {
  const params = new URLSearchParams();
  if (f.q.trim()) params.set('q', f.q.trim());
  for (const a of f.area) params.append('area', a);
  if (f.kind) params.set('kind', f.kind);
  if (f.follow_up) params.set('follow_up', f.follow_up);
  if (f.record_kind) params.set('record_kind', f.record_kind);
  if (f.page > 1) params.set('page', String(f.page));
  params.set('per_page', String(perPage));
  return params;
}

/** "26–50 of 112", "26–50 and more" when it isn't counted, or nothing when there's nothing */
export function pageWords(page: Page<unknown>): string {
  if (!page.items.length) return '';
  const from = (page.page - 1) * page.per_page + 1;
  const to = from + page.items.length - 1;
  if (page.total === null) return page.has_more ? `${from}–${to} and more` : `${from}–${to}`;
  return from === 1 && to === page.total ? `${page.total} found` : `${from}–${to} of ${page.total}`;
}

/** The last page, when it's known */
export function pages(page: Page<unknown>): number | null {
  if (page.total === null) return null;
  return Math.max(1, Math.ceil(page.total / page.per_page));
}

/** What a request asks for, in words: "restore it", "archive it", "put it in cold store" */
export function askedFor(request: Pick<ArchiveRequest, 'action' | 'to_state'>): string {
  if (request.action === 'restore') return 'restore it';
  return request.to_state === 'cold' ? 'put it in cold store' : 'archive it';
}

export const stateNames: Record<ArchiveState, string> = { active: 'Closed', archived: 'Archived', cold: 'Cold store' };

const day = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

/** What happens next to a record's keeping, in a sentence */
export function archiveWords(a: Archive, status: 'open' | 'closed'): string {
  const days = `archived ${a.archive_after_days} days after closing and put in cold store after ${a.cold_after_days}`;
  if (!a.auto) return 'Kept out of the archive: it stays as it is once closed.';
  if (a.state === 'cold') return `In cold store since ${a.cold_at ? day(a.cold_at) : 'its closing'}.`;
  if (status === 'open') return `Once closed, it’s ${days}.`;
  if (a.next_state && a.next_at) {
    const when = day(a.next_at);
    return a.next_state === 'archived' ? `It’s archived on ${when}, then in cold store after ${a.cold_after_days} days.` : `Archived; it goes into cold store on ${when}.`;
  }
  return `It’s ${days}.`;
}

/** How long a restore request has left: "18 h left", "40 min left", or lapsed */
export function timeLeft(expiresAt: string, now = Date.now()): string {
  const minutes = Math.floor((new Date(expiresAt).getTime() - now) / 60_000);
  if (minutes <= 0) return 'lapsed';
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h left` : `${minutes} min left`;
}
