import { describe, expect, it } from 'vitest';
import { sortTasks } from '../pages/incident/Tasks';
import { dueWords, suggested } from './work';
import type { Playbook, Task } from './work';

const now = Date.parse('2026-11-10T12:00:00Z');

describe('due words', () => {
  it('counts down, then up', () => {
    expect(dueWords('2026-11-10T12:20:00Z', now)).toBe('Due in 20 min');
    expect(dueWords('2026-11-10T15:00:00Z', now)).toBe('Due in 3 h');
    expect(dueWords('2026-11-10T07:00:00Z', now)).toBe('5 h overdue');
    expect(dueWords('2026-11-14T12:00:00Z', now)).toBe('Due in 4 days');
    expect(dueWords(null, now)).toBe('No due date');
  });
});

describe('playbook suggestions', () => {
  const book = (id: string, tags: string[], is_active = true) => ({ id, trigger_tags: tags, is_active }) as Playbook;
  it('match any tag, written as the backend normalises it, and skip retired playbooks', () => {
    const books = [book('a', ['phishing']), book('b', ['ransomware']), book('c', ['phishing'], false)];
    expect(suggested(books, ['Phishing', 'bec'])).toEqual(['a']);
    expect(suggested(books, [])).toEqual([]);
  });
});

describe('task order', () => {
  const task = (ref: string, status: Task['status'], due: string | null) => ({ ref, status, due_at: due }) as Task;
  it('puts unfinished work first, soonest due first, undated last', () => {
    const sorted = sortTasks([
      task('TSK-1', 'done', '2026-11-10T09:00:00Z'),
      task('TSK-2', 'open', null),
      task('TSK-3', 'open', '2026-11-10T14:00:00Z'),
      task('TSK-10', 'blocked', '2026-11-10T13:00:00Z'),
    ]);
    expect(sorted.map((t) => t.ref)).toEqual(['TSK-10', 'TSK-3', 'TSK-2', 'TSK-1']);
  });
});
