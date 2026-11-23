import { describe, expect, it } from 'vitest';
import { archiveWords, askedFor, catalogueParams, emptyCatalogue, emptyLessons, isTechnique, lessonParams, pageWords, pages, timeLeft } from './catalogue';
import type { Archive } from './incidents';

const archive: Archive = {
  state: 'active',
  auto: true,
  archive_after_days: 30,
  cold_after_days: 90,
  archived_at: null,
  cold_at: null,
  restored_at: null,
  next_state: 'archived',
  next_at: '2026-12-23T12:00:00Z',
  can_set: true,
  can_restore: false,
  archive_to: ['archived', 'cold'],
  request: null,
};

describe('the catalogue search', () => {
  it('sends only what is set', () => {
    expect(catalogueParams(emptyCatalogue).toString()).toBe('per_page=25');
    const params = catalogueParams({
      ...emptyCatalogue,
      q: '  folded lantern ',
      kind: 'hunt',
      state: ['archived', 'cold'],
      tags: ['Phishing', ' '],
      technique: 't1566.001',
      closed_from: '2026-11-01',
      page: 3,
    });
    expect(params.get('q')).toBe('folded lantern');
    expect(params.getAll('state')).toEqual(['archived', 'cold']);
    expect(params.getAll('tag')).toEqual(['phishing']);
    expect(params.get('technique')).toBe('T1566.001');
    expect(params.get('closed_from')).toBe('2026-11-01');
    expect(params.get('page')).toBe('3');
  });

  it('waits for a whole technique ID', () => {
    expect(isTechnique('T1059')).toBe(true);
    expect(isTechnique('t1059.001')).toBe(true);
    expect(isTechnique('T105')).toBe(false);
    expect(catalogueParams({ ...emptyCatalogue, technique: 'T105' }).has('technique')).toBe(false);
  });

  it('filters the lessons register', () => {
    const params = lessonParams({ ...emptyLessons, area: ['detection', 'people'], follow_up: 'open' });
    expect(params.getAll('area')).toEqual(['detection', 'people']);
    expect(params.get('follow_up')).toBe('open');
  });

  it('says where a page is', () => {
    const page = (n: number, total: number | null, page = 1, has_more = false) => ({ items: Array(n).fill(0), total, page, per_page: 25, has_more });
    expect(pageWords(page(0, 0))).toBe('');
    expect(pageWords(page(3, 3))).toBe('3 found');
    expect(pageWords(page(25, 112, 2, true))).toBe('26–50 of 112');
    expect(pageWords(page(12, 112, 5))).toBe('101–112 of 112');
    // an observer's masked search isn't counted to the end: it says there's more instead
    expect(pageWords(page(25, null, 3, true))).toBe('51–75 and more');
    expect(pageWords(page(4, null, 4))).toBe('76–79');
    expect(pages(page(25, null, 3, true))).toBeNull();
    expect(pages(page(25, 112, 2, true))).toBe(5);
  });
});

describe('a record’s keeping', () => {
  it('says what happens next', () => {
    expect(archiveWords(archive, 'open')).toBe('Once closed, it’s archived 30 days after closing and put in cold store after 90.');
    expect(archiveWords(archive, 'closed')).toBe('It’s archived on 23 Dec 2026, then in cold store after 90 days.');
    expect(archiveWords({ ...archive, state: 'archived', next_state: 'cold', next_at: '2027-02-21T12:00:00Z' }, 'closed')).toBe(
      'Archived; it goes into cold store on 21 Feb 2027.',
    );
    expect(archiveWords({ ...archive, auto: false }, 'closed')).toMatch(/^Kept out/);
    expect(archiveWords({ ...archive, state: 'cold', cold_at: '2027-02-21T12:00:00Z' }, 'closed')).toBe('In cold store since 21 Feb 2027.');
  });

  it('says what a request asks for', () => {
    expect(askedFor({ action: 'restore', to_state: 'active' })).toBe('restore it');
    expect(askedFor({ action: 'archive', to_state: 'archived' })).toBe('archive it');
    expect(askedFor({ action: 'archive', to_state: 'cold' })).toBe('put it in cold store');
  });

  it('counts a request down', () => {
    const now = Date.parse('2026-11-23T10:00:00Z');
    expect(timeLeft('2026-11-24T04:30:00Z', now)).toBe('18 h left');
    expect(timeLeft('2026-11-23T10:40:00Z', now)).toBe('40 min left');
    expect(timeLeft('2026-11-23T09:00:00Z', now)).toBe('lapsed');
  });
});
