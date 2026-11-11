import { describe, expect, it } from 'vitest';
import { sectionsFor } from '../../api/incidents';
import { trigger } from './Comments';
import { refTarget, splitTags } from './shared';

describe('the comment trigger', () => {
  it('finds an @mention being typed', () => {
    expect(trigger('ask @da', 7)).toEqual({ sign: '@', query: 'da', start: 4 });
  });
  it('finds a #reference at the start of the text', () => {
    expect(trigger('#FND', 4)).toEqual({ sign: '#', query: 'fnd', start: 0 });
  });
  it('ignores an @ inside a word, such as an email address', () => {
    expect(trigger('mail sam@example', 16)).toBeNull();
  });
  it('stops once a space follows', () => {
    expect(trigger('@dana ', 6)).toBeNull();
  });
});

describe('references', () => {
  it('open the section that holds the record', () => {
    expect(refTarget('fnd-12')).toEqual({ search: '?s=findings', hash: '#FND-12' });
    expect(refTarget('AST-3').search).toBe('?s=assets');
    expect(refTarget('OBS-9').search).toBe('?s=observables');
  });
});

describe('live invalidation', () => {
  it('refreshes the sections an event touches', () => {
    expect(sectionsFor('asset_link')).toEqual(['assets', 'links', 'timeline']);
    expect(sectionsFor('comment')).toEqual(['comments']);
    expect(sectionsFor('user')).toEqual([]);
  });
});

it('splits tags on commas and new lines', () => {
  expect(splitTags('ransomware, phishing\n  bec ,')).toEqual(['ransomware', 'phishing', 'bec']);
});
