import { describe, expect, it } from 'vitest';
import { intelPath, pushedWords } from './exports';

describe('intelligence exports', () => {
  it('asks for leads on the Navigator layer only when wanted', () => {
    expect(intelPath('i-1', 'stix')).toBe('/api/incidents/i-1/export/stix');
    expect(intelPath('i-1', 'navigator')).toBe('/api/incidents/i-1/export/navigator');
    expect(intelPath('i-1', 'navigator', true)).toBe('/api/incidents/i-1/export/navigator?leads=true');
  });

  it('says whether a push added the event or updated it', () => {
    const base = { uuid: 'u', url: 'https://misp.example/events/view/7' };
    expect(pushedWords({ ...base, action: 'added', event_id: '7' })).toBe('Sent to MISP (event 7).');
    expect(pushedWords({ ...base, action: 'updated', event_id: '' })).toBe('Updated in MISP.');
  });
});
