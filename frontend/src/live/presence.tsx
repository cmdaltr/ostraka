// Presence on each record: who else has a finding, task or observable open, and whether they're
// editing it. Focus is sent by id only ("finding:<id>"); the server drops anything else.

import { createContext, useContext, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { announce } from './live';
import type { Present } from './live';

interface Room {
  incidentId: string;
  meId: string;
  people: Present[];
}

const PresenceContext = createContext<Room | null>(null);

export function PresenceProvider({ value, children }: { value: Room; children: ReactNode }) {
  return <PresenceContext.Provider value={value}>{children}</PresenceContext.Provider>;
}

export function othersOn(people: Present[], meId: string, focus: string): Present[] {
  return people.filter((p) => p.user_id !== meId && p.focus === focus);
}

// What this tab has claimed: an edit holds the focus until it ends, so hovering elsewhere doesn't move it
let editingFocus: string | null = null;
let shownFocus = '';

const HOVER_MS = 500;

// Spread the returned handlers on a record. Pointer or keyboard focus held for a moment is "viewing";
// `editing` (a form open on the record) is announced as soon as it starts
export function useRecordPresence(focus: string, editing: boolean) {
  const room = useContext(PresenceContext);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const incidentId = room?.incidentId;

  useEffect(() => {
    if (!incidentId) return;
    if (editing) {
      editingFocus = focus;
      shownFocus = focus;
      announce(incidentId, focus, true);
    } else if (editingFocus === focus) {
      editingFocus = null;
      shownFocus = '';
      announce(incidentId, '', false);
    }
  }, [editing, focus, incidentId]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      if (incidentId && (editingFocus === focus || (!editingFocus && shownFocus === focus))) {
        editingFocus = null;
        shownFocus = '';
        announce(incidentId, '', false);
      }
    },
    [focus, incidentId],
  );

  const enter = () => {
    if (!incidentId || editingFocus) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (editingFocus || shownFocus === focus) return;
      shownFocus = focus;
      announce(incidentId, focus, false);
    }, HOVER_MS);
  };
  const leave = () => {
    if (timer.current) clearTimeout(timer.current);
    if (!incidentId || editingFocus || shownFocus !== focus) return;
    shownFocus = '';
    announce(incidentId, '', false);
  };
  return { onMouseEnter: enter, onMouseLeave: leave, onFocus: enter, onBlur: leave };
}

// "Dee is editing" beside a record someone else has open
export function Watching({ focus }: { focus: string }) {
  const room = useContext(PresenceContext);
  if (!room) return null;
  const others = othersOn(room.people, room.meId, focus);
  if (!others.length) return null;
  return (
    <span className="watching" role="status" aria-label="Also on this record">
      {others.map((p) => (
        <span key={p.user_id} className={p.editing ? 'chip watching-editing' : 'chip'}>
          <span className={p.editing ? 'dot editing' : 'dot'} aria-hidden />
          {p.name} {p.editing ? 'editing' : 'viewing'}
        </span>
      ))}
    </span>
  );
}
