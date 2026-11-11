// The one way the frontend talks to the API.
// The access token lives only in memory; the refresh token is an httpOnly cookie the page can't read.
// A 401 triggers one silent refresh, shared by every request waiting on it, then a retry.

import type { Grant } from './types';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onSignedOut: () => void = () => undefined;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function whenSignedOut(callback: () => void): void {
  onSignedOut = callback;
}

async function errorFrom(response: Response): Promise<ApiError> {
  let message = `Something went wrong (${response.status}).`;
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') message = body.detail;
    else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = String(body.detail[0].msg);
  } catch {
    // not JSON; keep the generic message
  }
  return new ApiError(response.status, message);
}

export async function refresh(): Promise<boolean> {
  refreshing ??= fetch('/api/auth/refresh', { method: 'POST', headers: { 'X-Ostraka': '1' } })
    .then(async (r) => {
      if (!r.ok) return false;
      setAccessToken(((await r.json()) as Grant).access_token);
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

async function send(path: string, init: RequestInit & { json?: unknown }, retry: boolean): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set('X-Ostraka', '1');
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(init.json);
  }
  const response = await fetch(path, { ...init, headers, body });

  if (response.status === 401 && retry && accessToken && !path.startsWith('/api/auth/')) {
    if (await refresh()) return send(path, init, false);
    setAccessToken(null);
    onSignedOut();
  }
  if (!response.ok) throw await errorFrom(response);
  return response;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const response = await send(path, init, true);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// A file from the API (a report), saved under the name the server gives it
export async function download(path: string): Promise<string> {
  const response = await send(path, {}, true);
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? 'download';
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

// An image from the API as an object URL, since an <img> can't send the access token. Revoke it when done.
export async function objectUrl(path: string): Promise<string> {
  const response = await send(path, {}, true);
  return URL.createObjectURL(await response.blob());
}
