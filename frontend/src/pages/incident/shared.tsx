// Pieces every incident section uses: loading a section, saving with conflict handling, rendering text

import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, api } from '../../api/client';
import { keys } from '../../api/incidents';
import type { Incident } from '../../api/incidents';
import { messageOf } from '../../components/ui';

export function splitTags(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export function useSection<T>(incident: Incident, section: string, path = section) {
  return useQuery({
    queryKey: keys.section(incident.id, section),
    queryFn: () => api<T>(`/api/incidents/${incident.id}/${path}`),
  });
}

export const conflictMessage =
  'Someone else changed this while you were working on it. Ostraka has loaded their version: check it, then make your change again.';

// Runs a change and refreshes what it touched. A 409 means a stale version, so the fresh one is fetched.
export function useSave(incident: Incident, sections: string[]) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async <T,>(change: () => Promise<T>): Promise<T | undefined> => {
      const refresh = () =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: keys.incident(incident.id), exact: true }),
          ...sections.map((s) => queryClient.invalidateQueries({ queryKey: keys.section(incident.id, s) })),
        ]);
      try {
        const result = await change();
        setError(null);
        await refresh();
        return result;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          // A stale version, or a rule such as "hand over command first"; either way, show what's current
          setError(e.message.startsWith('Someone else changed') ? conflictMessage : e.message);
          await refresh();
        } else setError(messageOf(e));
        return undefined;
      }
    },
    [incident.id, queryClient, sections],
  );
  return { run, error, clear: () => setError(null) };
}

const sectionOf: Record<string, string> = {
  FND: 'findings',
  AST: 'assets',
  OBS: 'observables',
  TSK: 'tasks',
  ASM: 'assessments',
  HYP: 'ach',
  EVD: 'ach',
  LSN: 'lessons',
  UPD: 'updates',
};

// Where a reference within this incident lives: its section, scrolled to the record
export function refTarget(ref: string) {
  const upper = ref.toUpperCase();
  return { search: `?s=${sectionOf[upper.slice(0, 3)] ?? 'overview'}`, hash: `#${upper}` };
}

const TOKEN = /(@\[([^\]]{1,120})\]\((user|team):[0-9a-f-]{36}\))|#(((?:OST|HNT)-\d{4}-\d{3,})|((FND|AST|OBS|TSK)-\d+))\b|(\[(?:[a-z0-9 ]+)\])/gi;

// Free text with mentions, #references and masked indicators drawn as chips
export function Rich({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    if (m[1]) parts.push(<span key={at} className="mention">@{m[2]}</span>);
    else if (m[5]) parts.push(<Link key={at} className="ref-link mono" to={`/incidents/${m[5].toUpperCase()}`}>#{m[5]}</Link>);
    else if (m[6]) parts.push(<Link key={at} className="ref-link mono" to={refTarget(m[6])}>#{m[6]}</Link>);
    else parts.push(<span key={at} className="masked">{m[8]}</span>);
    last = at + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <span className="rich">{parts}</span>;
}

export function Tags({ tags }: { tags: string[] }) {
  if (!tags.length) return null;
  return (
    <span className="tags">
      {tags.map((t) => (
        <span key={t} className="chip">
          {t}
        </span>
      ))}
    </span>
  );
}

// datetime-local wants local time without a zone; the API wants an ISO instant
export function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function fromLocalInput(value: FormDataEntryValue | null): string | null {
  return value ? new Date(String(value)).toISOString() : null;
}
