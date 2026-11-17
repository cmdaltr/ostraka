import { describe, expect, it } from 'vitest';
import { initials } from './AppShell';

describe('the profile chip’s initials', () => {
  it('takes the first and last name', () => {
    expect(initials('Sam Analyst')).toBe('SA');
    expect(initials('Dana de la Cruz')).toBe('DC');
  });

  it('makes do with one name, or an address used as a name', () => {
    expect(initials('Cher')).toBe('CH');
    expect(initials('admin@ostraka.app')).toBe('AD');
    expect(initials('ben.smith@example.org')).toBe('BS');
  });
});
