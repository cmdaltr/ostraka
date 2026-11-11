import { describe, expect, it } from 'vitest';
import { bySection } from './updates';
import type { UpdateLine } from './updates';

const line = (section: UpdateLine['section'], text: string): UpdateLine => ({ section, kind: '', ref: '', text });

describe('bySection', () => {
  it('keeps the update in order, one group per heading', () => {
    const groups = bySection([
      line('assessment', 'We assess it is likely (55–75%) that ...'),
      line('assessment', 'This is based on FND-2.'),
      line('what', 'Confirmed: FND-2 Payroll export staged.'),
      line('what', 'Lead, not yet confirmed: FND-3 Rclone seen.'),
      line('position', 'Where things stand: ...'),
      line('next', 'Next update: within 4 h.'),
    ]);
    expect(groups.map(([s, g]) => [s, g.length])).toEqual([
      ['assessment', 2],
      ['what', 2],
      ['position', 1],
      ['next', 1],
    ]);
  });

  it('is empty for no lines', () => {
    expect(bySection([])).toEqual([]);
  });
});
