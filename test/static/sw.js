'use strict';

self.addEventListener('push', function(event) {
  const data = event.data ? event.data.json() : {};
  console.log(`[Service Worker] Push Received with data: "${data}"`);
  
  const title = data.title;
  const options = {
    body: data.body,
    icon: 'images/icon.png',
    badge: 'images/badge.png',
    data: {
      url: data.data.url
    }
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
