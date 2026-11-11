// The viewer's palette and light/dark choice. Kept in localStorage until accounts arrive (phase 1),
// when it moves to the user's server-side preferences and localStorage becomes a cache.

import { useSyncExternalStore } from 'react';

export const palettes = ['cobalt', 'ostrakon', 'ledger'] as const;
export type Palette = (typeof palettes)[number];
export const modes = ['system', 'light', 'dark'] as const;
export type Mode = (typeof modes)[number];

export interface Theme {
  palette: Palette;
  mode: Mode;
}

export const DEFAULT_THEME: Theme = { palette: 'cobalt', mode: 'system' };
const KEY = 'ostraka.theme';

export function parseTheme(raw: string | null): Theme {
  try {
    const value = raw ? (JSON.parse(raw) as Partial<Theme>) : {};
    return {
      palette: palettes.includes(value.palette as Palette) ? (value.palette as Palette) : DEFAULT_THEME.palette,
      mode: modes.includes(value.mode as Mode) ? (value.mode as Mode) : DEFAULT_THEME.mode,
    };
  } catch {
    return DEFAULT_THEME;
  }
}

function read(): Theme {
  try {
    return parseTheme(localStorage.getItem(KEY));
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  root.dataset.palette = theme.palette;
  if (theme.mode === 'system') delete root.dataset.mode;
  else root.dataset.mode = theme.mode;
}

let current = read();
const listeners = new Set<() => void>();

export function setTheme(next: Partial<Theme>): void {
  current = { ...current, ...next };
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    // private window or blocked storage: the choice lasts for this page only
  }
  applyTheme(current);
  listeners.forEach((l) => l());
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}
