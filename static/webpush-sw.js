/*
 * WebPush service worker: published at the root of the site (/webpush-sw.js) and registered by webpush-client.js
 * as /webpush-sw.js?api=<api url>&site=<site code>.
 * - push: shows the notification with all the options sent by WebPush (title, body, icon, image, badge, actions...)
 * - notificationclick: opens the link of the notification (or of the button) and counts the click
 * - pushsubscriptionchange: the browser renewed the subscription, the new one is sent to WebPush
 * Every event is also posted to the open pages ({type: 'webpush:sw', event, detail}): the test page of the console
 * shows them (debug: the push arrived, the notification was shown or why not).
 */
'use strict';

var params = new URL(self.location.href).searchParams;
var API = params.get('api');
var SITE = params.get('site');

function report(event, detail) {
    return self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(function (windows) {
        windows.forEach(function (client) {
            client.postMessage({type: 'webpush:sw', event: event, detail: detail});
        });
    }).catch(function () {});
}

self.addEventListener('install', function () {
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function (event) {
    var data = {};
    var problem = null;
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data = {title: event.data.text()};
            problem = 'payload is not JSON';
        }
    } else {
        problem = 'push without payload';
    }
    var title = data.title || '';
    var options = Object.assign({}, data);
    delete options.title;
    options.data = options.data || {};
    event.waitUntil(report('push received', {title: title, problem: problem}).then(function () {
        return self.registration.showNotification(title, options);
    }).then(function () {
        return report('notification shown', {title: title, permission: self.Notification && Notification.permission});
    }, function (e) {
        return report('notification NOT shown', {title: title, error: e.name + ': ' + e.message});
    }));
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    report('notification clicked', {action: event.action || null});
    var data = event.notification.data || {};
    var actions = data.actions || {};
    var url = (event.action && actions[event.action]) || data.url;
    var tracking = data.webpush;
    var tasks = [];

    if (tracking && tracking.click_url) {
        tasks.push(fetch(tracking.click_url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message_id: tracking.message_id}),
            keepalive: true
        }).catch(function () {}));
    }
    if (url) {
        tasks.push(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(function (windows) {
            for (var i = 0; i < windows.length; i++) {
                if (windows[i].url === url && 'focus' in windows[i]) return windows[i].focus();
            }
            return self.clients.openWindow(url);
        }));
    }
    event.waitUntil(Promise.all(tasks));
});

function keyBytes(base64) {
    var padded = (base64 + '='.repeat((4 - base64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(padded), function (c) { return c.charCodeAt(0); });
}

self.addEventListener('pushsubscriptionchange', function (event) {
    if (!API || !SITE) return;
    var base = API + '/sites/' + encodeURIComponent(SITE);
    var key = event.oldSubscription && event.oldSubscription.options && event.oldSubscription.options.applicationServerKey;
    event.waitUntil(
        (key ? Promise.resolve(key) : fetch(base + '/vapid-public-key').then(function (response) {
            return response.json();
        }).then(function (body) {
            return keyBytes(body.public_key);
        })).then(function (applicationServerKey) {
            return event.newSubscription || self.registration.pushManager.subscribe({
                userVisibleOnly: true, applicationServerKey: applicationServerKey
            });
        }).then(function (subscription) {
            return fetch(base + '/subscriptions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({subscription: subscription})
            });
        })
    );
});
