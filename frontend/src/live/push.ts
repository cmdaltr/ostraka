// Browser push: register the service worker, subscribe with ostraka's VAPID key, tell the server.

import { api } from '../api/client';

export type PushState = 'unsupported' | 'denied' | 'off' | 'on';

export const pushSupported = () =>
  typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

function keyBytes(base64url: string): Uint8Array {
  const raw = atob(base64url.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (base64url.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return 'unsupported';
  if (window.Notification.permission === 'denied') return 'denied';
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager.getSubscription();
  return subscription ? 'on' : 'off';
}

export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return 'unsupported';
  if ((await window.Notification.requestPermission()) !== 'granted') return 'denied';
  const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  await navigator.serviceWorker.ready;
  const { key } = await api<{ key: string }>('/api/notifications/push-key');
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  const json = subscription.toJSON();
  await api('/api/notifications/push-subscriptions', {
    method: 'POST',
    json: { endpoint: json.endpoint, keys: json.keys, user_agent: navigator.userAgent.slice(0, 300) },
  });
  return 'on';
}

export async function disablePush(): Promise<PushState> {
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription) {
    await api('/api/notifications/push-subscriptions', { method: 'DELETE', json: { endpoint: subscription.endpoint } });
    await subscription.unsubscribe();
  }
  return 'off';
}
