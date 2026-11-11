// ostraka's service worker: shows push notifications and opens ostraka when one is clicked.
// Payloads arrive already masked for anything that leaves ostraka (see backend ostraka/notify).

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'ostraka', {
      body: data.body || '',
      tag: data.tag,
      icon: '/favicon.png',
      data: { link: typeof data.link === 'string' && data.link.startsWith('/') ? data.link : '/' },
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.link || '/', self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => w.url.startsWith(self.location.origin));
      if (open) return open.navigate(url).then((w) => (w || open).focus());
      return self.clients.openWindow(url);
    }),
  );
});
