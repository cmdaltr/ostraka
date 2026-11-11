// Who is signed in. On load, the refresh cookie (if any) restores the session without a password.

import { useSyncExternalStore } from 'react';
import { api, refresh, setAccessToken, whenSignedOut } from '../api/client';
import type { Grant, Me } from '../api/types';
import { setTheme } from '../theme/theme';

export type SessionState =
  | { status: 'loading' }
  | { status: 'signed-out'; reason?: string }
  | { status: 'signed-in'; me: Me };

let state: SessionState = { status: 'loading' };
const listeners = new Set<() => void>();

function set(next: SessionState): void {
  state = next;
  listeners.forEach((l) => l());
}

export function useSession(): SessionState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}

export function currentMe(): Me | null {
  return state.status === 'signed-in' ? state.me : null;
}

function signedIn(me: Me): void {
  setTheme({ palette: me.theme_palette, mode: me.theme_mode });
  set({ status: 'signed-in', me });
}

export async function loadMe(): Promise<void> {
  signedIn(await api<Me>('/api/users/me'));
}

whenSignedOut(() => set({ status: 'signed-out', reason: 'Your session ended. Sign in again.' }));

export async function restore(): Promise<void> {
  if (!(await refresh())) {
    set({ status: 'signed-out' });
    return;
  }
  try {
    await loadMe();
  } catch {
    set({ status: 'signed-out' });
  }
}

export async function startWith(grant: Grant): Promise<void> {
  setAccessToken(grant.access_token);
  await loadMe();
}

export async function signOut(): Promise<void> {
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } finally {
    setAccessToken(null);
    set({ status: 'signed-out' });
  }
}

// What you may change about yourself; "" clears the phone or quiet hours
export type MePatch = Partial<
  Pick<
    Me,
    | 'name'
    | 'job_title'
    | 'phone'
    | 'timezone'
    | 'availability'
    | 'theme_palette'
    | 'theme_mode'
    | 'show_raw_iocs'
    | 'notify_email'
    | 'notify_push'
    | 'muted_kinds'
    | 'quiet_from'
    | 'quiet_until'
    | 'new_device_alerts'
  >
>;

export async function updateMe(changes: MePatch) {
  signedIn(await api<Me>('/api/users/me', { method: 'PATCH', json: changes }));
}
