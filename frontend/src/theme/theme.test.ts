import { describe, expect, it } from 'vitest';
import { DEFAULT_THEME, applyTheme, parseTheme } from './theme';

describe('parseTheme', () => {
  it('defaults to Cobalt following the system', () => {
    expect(parseTheme(null)).toEqual(DEFAULT_THEME);
    expect(DEFAULT_THEME).toEqual({ palette: 'cobalt', mode: 'system' });
  });

  it('keeps a valid stored choice', () => {
    expect(parseTheme('{"palette":"ledger","mode":"dark"}')).toEqual({ palette: 'ledger', mode: 'dark' });
  });

  it('replaces unknown or corrupt values field by field', () => {
    expect(parseTheme('{"palette":"neon","mode":"dark"}')).toEqual({ palette: 'cobalt', mode: 'dark' });
    expect(parseTheme('not json')).toEqual(DEFAULT_THEME);
  });
});

describe('applyTheme', () => {
  it('sets the palette and leaves mode to the OS for "system"', () => {
    const root = document.createElement('html');
    applyTheme({ palette: 'ostrakon', mode: 'dark' }, root);
    expect(root.dataset).toMatchObject({ palette: 'ostrakon', mode: 'dark' });
    applyTheme({ palette: 'ostrakon', mode: 'system' }, root);
    expect(root.dataset.mode).toBeUndefined();
  });
});
