import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import './PillSelect.css';

export interface PillOption<T extends string> {
  value: T;
  // What the option looks like: the same pill the record shows, so the choice reads in its colours
  look: ReactNode;
  // Its name for screen readers and tests, when the look isn't plain words
  name: string;
  group?: string;
}

interface Props<T extends string> {
  id?: string;
  label: string;
  // The words for nothing chosen, offered as the first option; leave it out when one must be chosen
  any?: string;
  value: T[];
  options: PillOption<T>[];
  onChange: (value: T[]) => void;
  // One choice only: picking it closes the list, and the button shows the pill alone
  single?: boolean;
  disabled?: boolean;
}

// How many chosen pills the closed button shows before it counts the rest
const SHOWN = 2;

/** A filter that drops down a list of pills, any number of them chosen; none chosen is "any". A native
 * select can't colour its options on macOS, and these are worth seeing in the colours the records use.
 * A multi-select listbox, worked with the arrow keys: Enter or Space ticks, and it stays open. */
export function PillSelect<T extends string>({
  id,
  label,
  any,
  value,
  options,
  onChange,
  single = false,
  disabled = false,
}: Props<T>) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const choices: { value: T | ''; look: ReactNode; name: string; group?: string }[] = [
    ...(any === undefined
      ? []
      : [{ value: '' as const, look: <span className="pill-select-any">{any}</span>, name: any }]),
    ...options,
  ];
  const chosen = options.filter((o) => value.includes(o.value));
  const isOn = (v: T | '') => (v === '' ? value.length === 0 : value.includes(v));

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', away);
    list.current?.focus();
    return () => document.removeEventListener('pointerdown', away);
  }, [open]);

  const show = () => {
    setAt(
      single
        ? Math.max(
            0,
            choices.findIndex((c) => isOn(c.value)),
          )
        : 0,
    );
    setOpen(true);
  };
  // "Any" clears the choice; anything else is ticked or unticked, kept in the options' order
  const pick = (v: T | '') => {
    if (single) {
      setOpen(false);
      button.current?.focus();
      if (v !== '' && !value.includes(v)) onChange([v]);
      return;
    }
    onChange(
      v === '' ? [] : options.map((o) => o.value).filter((o) => (o === v ? !value.includes(v) : value.includes(o))),
    );
  };

  function keys(e: KeyboardEvent<HTMLUListElement>) {
    const last = choices.length - 1;
    const moves: Record<string, number> = {
      ArrowDown: Math.min(last, at + 1),
      ArrowUp: Math.max(0, at - 1),
      Home: 0,
      End: last,
    };
    if (e.key in moves) {
      e.preventDefault();
      setAt(moves[e.key]);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      pick(choices[at].value);
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      if (e.key === 'Escape') e.preventDefault();
      setOpen(false);
      button.current?.focus();
    }
  }

  return (
    <div className="pill-select" ref={box}>
      <button
        id={id}
        ref={button}
        type="button"
        className={`pill-select-button${value.length ? ' is-set' : ''}${single ? ' is-single' : ''}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${label}: ${chosen.length ? chosen.map((c) => c.name).join(', ') : (any ?? 'none')}`}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            show();
          }
        }}
      >
        {!single && <span className="pill-select-label">{label}</span>}
        {chosen.length === 0 && <span className="pill-select-any">{any}</span>}
        {chosen.slice(0, SHOWN).map((c) => (
          <span key={c.value} className="pill-select-chosen">
            {c.look}
          </span>
        ))}
        {chosen.length > SHOWN && <span className="pill-select-more">+{chosen.length - SHOWN}</span>}
      </button>
      {open && (
        <ul
          id={listId}
          ref={list}
          className="pill-select-list"
          role="listbox"
          aria-multiselectable={!single}
          aria-label={label}
          tabIndex={-1}
          aria-activedescendant={`${listId}-${at}`}
          onKeyDown={keys}
        >
          {choices.map((c, i) => (
            <li key={c.value || 'any'} className="pill-select-row">
              {c.group && c.group !== choices[i - 1]?.group && <span className="pill-select-group">{c.group}</span>}
              <span
                id={`${listId}-${i}`}
                role="option"
                aria-selected={isOn(c.value)}
                aria-label={c.name}
                className={`pill-select-option${i === at ? ' is-at' : ''}`}
                onPointerEnter={() => setAt(i)}
                onClick={() => pick(c.value)}
              >
                {!single && (
                  <span className="pill-select-tick" aria-hidden="true">
                    <svg viewBox="0 0 12 12">
                      <path d="M2.5 6.2 5 8.6l4.5-5" />
                    </svg>
                  </span>
                )}
                {c.look}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
