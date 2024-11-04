'use strict';

self.addEventListener('push', function(event) {
  console.log(`[Service Worker] Push Received with data: "${JSON.stringify(event.data)}"`);
  const data = event.data?.json() ?? {};
  
  const title = data.title;
  const options = {
    body: data.body ?? null,
    icon: data.icon ?? null,
    image: data.image ?? null,
    badge: data.badge ?? null,
    dir: data.dir ?? 'auto',
    timestamp: data.timestamp ?? null,

    actions: data.actions ?? [],
    data: { url: data.data?.url ?? null },

    tag: null,
    requireInteraction: data.requireInteraction ?? false,
    renotify: data.renotify ?? null,
    vibrate: data.vibrate ?? null,
    sound: data.sound ?? null,
    silent: data.silent ?? null
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  console.log('[Service Worker] Notification click Received.');

  event.notification.close();

  const urlToOpen = event.notification.data.url;

  event.waitUntil(
    clients.openWindow(urlToOpen)
  );
});
