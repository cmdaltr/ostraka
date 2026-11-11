// The live connection: one socket per tab while signed in, opened with a one-time ticket, rejoining
// after a drop. Activity events mark the matching cached sections stale; presence says who else is
// here; a notification signal refreshes the bell and your work.

import { useEffect, useState } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { keys, sectionsFor } from '../api/incidents';

export interface Present {
  user_id: string;
  name: string;
  focus: string;
  editing: boolean;
}

type Listener = (message: { type: string; [key: string]: unknown }) => void;

let socket: WebSocket | null = null;
let connecting: Promise<void> | null = null;
let retry = 1000;
let wanted = false;
const joined = new Set<string>();
const listeners = new Set<Listener>();

async function connect(): Promise<void> {
  if (!wanted) return;
  if (socket && socket.readyState <= WebSocket.OPEN) return;
  connecting ??= (async () => {
    try {
      const { ticket } = await api<{ ticket: string }>('/api/live/ticket', { method: 'POST' });
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${scheme}://${location.host}/ws?ticket=${encodeURIComponent(ticket)}`);
      socket = ws;
      ws.onopen = () => {
        retry = 1000;
        joined.forEach((incident) => ws.send(JSON.stringify({ type: 'join', incident })));
      };
      ws.onmessage = (e) => listeners.forEach((l) => l(JSON.parse(e.data)));
      ws.onclose = () => {
        socket = null;
        if (wanted) setTimeout(connect, (retry = Math.min(retry * 2, 30_000)));
      };
    } catch {
      if (wanted) setTimeout(connect, (retry = Math.min(retry * 2, 30_000)));
    } finally {
      connecting = null;
    }
  })();
  return connecting;
}

function send(message: object): void {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

// Open the socket for the signed-in person; the returned function closes it again
export function startLive(queryClient: QueryClient): () => void {
  wanted = true;
  const listener: Listener = (message) => {
    if (message.type !== 'notification') return;
    queryClient.invalidateQueries({ queryKey: ['notifications'] });
    queryClient.invalidateQueries({ queryKey: ['my-tasks'] });
    queryClient.invalidateQueries({ queryKey: ['my-clocks'] });
  };
  listeners.add(listener);
  connect();
  return () => {
    wanted = false;
    listeners.delete(listener);
    socket?.close();
    socket = null;
  };
}

export function announce(incident: string, focus: string, editing: boolean): void {
  send({ type: 'presence', incident, focus, editing });
}

// Watch an incident while the page is open: keep its cached data fresh and report who's here
export function useLiveIncident(incidentId: string | undefined, queryClient: QueryClient): Present[] {
  const [people, setPeople] = useState<Present[]>([]);

  useEffect(() => {
    if (!incidentId) return;
    const listener: Listener = (message) => {
      if (message.incident && message.incident !== incidentId) return;
      if (message.type === 'presence') setPeople(message.people as Present[]);
      if (message.type === 'activity') {
        const event = message.event as { incident_id: string; entity_type: string };
        if (event.incident_id !== incidentId) return;
        queryClient.invalidateQueries({ queryKey: keys.incident(incidentId), exact: true });
        queryClient.invalidateQueries({ queryKey: keys.section(incidentId, 'activity') });
        sectionsFor(event.entity_type).forEach((s) =>
          queryClient.invalidateQueries({ queryKey: keys.section(incidentId, s) }),
        );
        if (event.entity_type === 'task') queryClient.invalidateQueries({ queryKey: ['my-tasks'] });
        if (event.entity_type === 'clock') queryClient.invalidateQueries({ queryKey: ['my-clocks'] });
      }
    };
    listeners.add(listener);
    joined.add(incidentId);
    connect().then(() => send({ type: 'join', incident: incidentId }));
    const heartbeat = setInterval(() => send({ type: 'presence', incident: incidentId }), 30_000);
    return () => {
      clearInterval(heartbeat);
      listeners.delete(listener);
      joined.delete(incidentId);
      send({ type: 'leave', incident: incidentId });
      setPeople([]);
    };
  }, [incidentId, queryClient]);

  return people;
}
