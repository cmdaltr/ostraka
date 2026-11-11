import type { ReactNode } from 'react';
import { Assembling } from './Ostrakon';

/** A button's words while its work runs (a `useFinishing` phase): the sherd assembles beside `busy`, and,
 * when there's `done` to say and the work went through, mends and glows once beside it. Otherwise, and
 * once that's over, the button's own `children`. */
export function Progress({
  phase,
  ok = true,
  label,
  busy,
  done,
  children,
}: {
  phase: 'idle' | 'busy' | 'finished';
  ok?: boolean;
  label: string;
  busy: string;
  done?: string;
  children: ReactNode;
}) {
  // The same element in both branches, so the sherd stays mounted and closes up rather than starting again
  if (phase === 'busy' || (phase === 'finished' && ok && done))
    return (
      <>
        <Assembling inline width="1.4em" done={phase === 'finished'} label={label} />{' '}
        {phase === 'busy' ? busy : done}
      </>
    );
  return <>{children}</>;
}
