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
  id: string;
  label: string;
  any: string;
  value: T | '';
  options: PillOption<T>[];
  onChange: (value: T | '') => void;
}

/** A filter that drops down a list of pills. A native select can't colour its options on macOS, and
 * these are worth seeing in the colours the records use. A listbox, worked with the arrow keys. */
export function PillSelect<T extends string>({ id, label, any, value, options, onChange }: Props<T>) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const choices: { value: T | ''; look: ReactNode; name: string; group?: string }[] = [
    { value: '', look: <span className="pill-select-any">{any}</span>, name: any },
    ...options,
  ];
  const chosen = choices.find((c) => c.value === value) ?? choices[0];

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
    setAt(Math.max(0, choices.findIndex((c) => c.value === value)));
    setOpen(true);
  };
  const pick = (v: T | '') => {
    onChange(v);
    setOpen(false);
    button.current?.focus();
  };

  function keys(e: KeyboardEvent<HTMLUListElement>) {
    const last = choices.length - 1;
    const moves: Record<string, number> = { ArrowDown: Math.min(last, at + 1), ArrowUp: Math.max(0, at - 1), Home: 0, End: last };
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
        className={`pill-select-button${value ? ' is-set' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${label}: ${chosen.name}`}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            show();
          }
        }}
      >
        {!value && <span className="pill-select-label">{label}</span>}
        {chosen.look}
      </button>
      {open && (
        <ul
          id={listId}
          ref={list}
          className="pill-select-list"
          role="listbox"
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
                aria-selected={c.value === value}
                aria-label={c.name}
                className={`pill-select-option${i === at ? ' is-at' : ''}`}
                onPointerEnter={() => setAt(i)}
                onClick={() => pick(c.value)}
              >
                {c.look}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
