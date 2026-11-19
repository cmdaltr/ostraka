import { anatomyParts } from '../api/flow';
import type { Playbook } from '../api/work';
import { Anatomy } from './Anatomy';
import type { DrawnPart } from './Anatomy';

// A playbook drawn on the attack's anatomy (backend ostraka/playbook.py). A part with checks is drawn
// solid, one without dashed; what's chosen has the red ochre halo.
//   edit:  every part, so any can be given checks
//   apply: every part, those with checks to take (the ones already taken stay solid, and can't be)
//   view:  only the parts with checks

export type PlaybookMode = 'edit' | 'apply' | 'view';

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function checksIn(playbook: Pick<Playbook, 'checks'>, part: string): number {
  return playbook.checks.filter((c) => c.part === part).length;
}

export function PlaybookAnatomy({
  playbook,
  mode,
  chosen = () => false,
  choose,
  taken = new Set(),
  seen = new Set(),
  label = 'The playbook on the attack’s anatomy',
}: {
  playbook: Pick<Playbook, 'checks' | 'conditions'>;
  mode: PlaybookMode;
  chosen?: (key: string) => boolean;
  choose?: (key: string) => void;
  taken?: Set<string>; // apply: parts this record has already taken
  seen?: Set<string>; // apply: parts the record's own attack flow shows
  label?: string;
}) {
  const parts: DrawnPart[] = anatomyParts
    .filter((p) => mode !== 'view' || checksIn(playbook, p.key) > 0)
    .map((p) => {
      const n = checksIn(playbook, p.key);
      const when = playbook.conditions[p.key];
      const done = taken.has(p.key);
      const lines =
        mode === 'edit'
          ? n
            ? [plural(n, 'check') + (when ? ' · only if…' : '')]
            : [p.brief ?? p.says]
          : [
              ...(seen.has(p.key) ? ['Seen in the record'] : []),
              done ? 'Applied' : n ? plural(n, 'check') + (when ? ' · only if…' : '') : 'No checks',
            ];
      const pickable = mode === 'edit' || (mode === 'apply' && n > 0 && !done);
      const named = `${p.name}, ${done ? 'applied' : n ? plural(n, 'check') : 'not in this playbook'}`;
      return {
        key: p.key,
        name: p.name,
        column: p.column,
        verb: p.verb,
        look: n ? 'layer-seen' : 'layer-expected',
        lines: p.column === 'action' ? undefined : lines,
        text:
          p.column !== 'action'
            ? undefined
            : mode === 'edit'
              ? `${n ? '' : '+ '}${p.name}${n ? ` (${n})` : ''}`
              : `${p.name}${done ? ' · applied' : n ? ` (${n})` : ''}${seen.has(p.key) ? ' · seen' : ''}`,
        label: pickable && choose ? named : undefined,
        tick: mode === 'apply' && done,
      };
    });
  const actions = parts.filter((p) => p.column === 'action');
  return (
    <Anatomy
      parts={parts}
      actor={{ title: 'Whoever it is', lines: mode === 'edit' ? ['Attributed on the record'] : [] }}
      actions={{
        look: actions.some((a) => a.look === 'layer-seen') ? 'layer-seen' : 'layer-expected',
        label: mode === 'edit' && choose ? 'Actions' : undefined,
      }}
      chosen={chosen}
      choose={choose}
      label={label}
    />
  );
}
