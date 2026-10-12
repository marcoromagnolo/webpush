/*
 * WebPush client: subscription of the visitors of a site to its notifications.
 *
 *   <script src="https://<webpush>/static/webpush-client.js" defer
 *           data-api="https://<webpush>/api/v1" data-site="<site code>" data-service-worker="/webpush-sw.js"></script>
 *   <button type="button" data-webpush-toggle hidden data-label-on="Disable notifications">Enable notifications</button>
 *
 * The buttons [data-webpush-toggle] are shown where push is supported and not blocked, and switch the subscription.
 * From your own code: WebPush.subscribe(), WebPush.unsubscribe(), WebPush.state() -> Promise of
 * 'subscribed' | 'unsubscribed' | 'blocked' | 'unsupported'. WebPush.init({api, site, serviceWorker}) configures it
 * without the data- attributes. Events on document: 'webpush:change' at every change (detail: state),
 * 'webpush:error' when a button fails (detail: message).
 * Debug: WebPush.init({debug: function (step, detail) {...}}) receives every step (service worker, permission, key,
 * subscription, answers of the API); without it the steps go to console.debug.
 */
(function () {
    'use strict';

    var script = document.currentScript;
    var config = {
        api: script && script.dataset.api,
        site: script && script.dataset.site,
        serviceWorker: (script && script.dataset.serviceWorker) || '/webpush-sw.js',
        debug: null
    };
    var publicKey = null;
    var REFRESH_KEY = 'webpush:refreshed';
    var REFRESH_MS = 24 * 3600 * 1000;

    function log(step, detail) {
        if (typeof config.debug === 'function') config.debug(step, detail);
        else console.debug('WebPush: ' + step, detail === undefined ? '' : detail);
    }

    function host(url) {
        try { return new URL(url).host; } catch (e) { return url; }
    }

    function supported() {
        return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
    }

    function base() {
        return config.api.replace(/\/$/, '') + '/sites/' + encodeURIComponent(config.site);
    }

    function keyBytes(base64) {
        var padded = (base64 + '='.repeat((4 - base64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
        return Uint8Array.from(atob(padded), function (c) { return c.charCodeAt(0); });
    }

    function sameKey(buffer, bytes) {
        if (!buffer) return false;
        var a = new Uint8Array(buffer);
        if (a.length !== bytes.length) return false;
        for (var i = 0; i < a.length; i++) if (a[i] !== bytes[i]) return false;
        return true;
    }

    function request(path, method, body) {
        var url = base() + path;
        return fetch(url, {
            method: method,
            headers: body ? {'Content-Type': 'application/json'} : {},
            body: body ? JSON.stringify(body) : undefined
        }).catch(function (e) {
            // no answer: server down, wrong url, or CORS (origin not allowed by the site)
            throw new Error(method + ' ' + url + ' failed (' + e.message + '): WebPush not reachable, or this origin (' +
                location.origin + ') is not allowed by the site (CORS)');
        }).then(function (response) {
            log(method + ' ' + path, response.status + ' ' + response.statusText);
            if (response.ok) return response.status === 204 ? null : response.json();
            return response.text().then(function (text) {
                var message = text;
                try { message = JSON.parse(text).error || text; } catch (e) {}
                throw new Error(method + ' ' + path + ' answered ' + response.status + ': ' + message);
            });
        });
    }

    function getPublicKey() {
        if (publicKey) return Promise.resolve(publicKey);
        return request('/vapid-public-key', 'GET').then(function (body) {
            publicKey = keyBytes(body.public_key);
            log('VAPID public key of the site', body.public_key);
            return publicKey;
        });
    }

    function activated(registration) {
        return new Promise(function (resolve) {
            var worker = registration.installing || registration.waiting || registration.active;
            if (!worker || worker.state === 'activated') return resolve(registration);
            worker.addEventListener('statechange', function () {
                if (worker.state === 'activated') resolve(registration);
            });
        });
    }

    function registration() {
        var url = config.serviceWorker + '?' + new URLSearchParams({api: config.api, site: config.site});
        log('service worker: registering', url);
        return navigator.serviceWorker.register(url).catch(function (e) {
            throw new Error('service worker ' + config.serviceWorker + ' not registered (' + e.message +
                '): it must be reachable on this origin, served as JavaScript');
        }).then(activated).then(function (registration) {
            log('service worker: active', 'scope ' + registration.scope);
            return registration;
        });
    }

    function existingSubscription() {
        return navigator.serviceWorker.getRegistration(config.serviceWorker).then(function (reg) {
            return reg ? reg.pushManager.getSubscription() : null;
        });
    }

    function changed(state) {
        updateButtons(state);
        document.dispatchEvent(new CustomEvent('webpush:change', {detail: state}));
        return state;
    }

    function state() {
        if (!supported() || !config.api || !config.site) return Promise.resolve('unsupported');
        if (Notification.permission === 'denied') return Promise.resolve('blocked');
        if (Notification.permission !== 'granted') return Promise.resolve('unsubscribed');
        return existingSubscription().then(function (subscription) {
            return subscription ? 'subscribed' : 'unsubscribed';
        });
    }

    function subscribe() {
        if (!supported()) return Promise.reject(new Error('WebPush: push notifications not supported'));
        log('permission: asking', 'now ' + Notification.permission);
        return Notification.requestPermission().then(function (permission) {
            log('permission', permission);
            if (permission !== 'granted') {
                changed(permission === 'denied' ? 'blocked' : 'unsubscribed');
                throw new Error('WebPush: permission ' + permission);
            }
            return Promise.all([registration(), getPublicKey()]);
        }).then(function (values) {
            var reg = values[0], key = values[1];
            return reg.pushManager.getSubscription().then(function (subscription) {
                // a subscription made with other keys (e.g. a previous service) is replaced
                if (subscription && sameKey(subscription.options.applicationServerKey, key)) {
                    log('subscription: already there, same key', host(subscription.endpoint));
                    return subscription;
                }
                log('subscription', subscription ? 'there with another key: replaced' : 'none: new one');
                return (subscription ? subscription.unsubscribe() : Promise.resolve()).then(function () {
                    return reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
                }).catch(function (e) {
                    throw new Error('pushManager.subscribe failed (' + e.name + ': ' + e.message + '): the push service ' +
                        'of the browser is not reachable, or the browser blocks push (e.g. Brave: enable Google services for push)');
                }).then(function (created) {
                    log('subscription: created on the push service', host(created.endpoint));
                    return created;
                });
            });
        }).then(function (subscription) {
            return request('/subscriptions', 'POST', {subscription: subscription}).then(function () {
                try { localStorage.setItem(REFRESH_KEY, String(Date.now())); } catch (e) {}
                changed('subscribed');
                return subscription;
            });
        });
    }

    function unsubscribe() {
        return existingSubscription().then(function (subscription) {
            if (!subscription) return null;
            return request('/subscriptions', 'DELETE', {endpoint: subscription.endpoint}).catch(function () {}).then(function () {
                return subscription.unsubscribe();
            });
        }).then(function () {
            return changed('unsubscribed');
        });
    }

    // A subscribed browser sends its subscription again once a day: new keys or expiration are updated,
    // a subscriber removed by mistake is active again
    function refresh() {
        var last = 0;
        try { last = Number(localStorage.getItem(REFRESH_KEY)) || 0; } catch (e) {}
        if (Date.now() - last < REFRESH_MS) return;
        existingSubscription().then(function (subscription) {
            if (!subscription) return;
            return request('/subscriptions', 'POST', {subscription: subscription}).then(function () {
                try { localStorage.setItem(REFRESH_KEY, String(Date.now())); } catch (e) {}
            });
        }).catch(function () {});
    }

    function updateButtons(current) {
        var buttons = document.querySelectorAll('[data-webpush-toggle]');
        Array.prototype.forEach.call(buttons, function (button) {
            if (!button.dataset.labelOff) button.dataset.labelOff = button.textContent;
            button.hidden = current === 'unsupported' || current === 'blocked';
            button.textContent = current === 'subscribed' ? (button.dataset.labelOn || 'Disable notifications') : button.dataset.labelOff;
            button.setAttribute('aria-pressed', current === 'subscribed' ? 'true' : 'false');
        });
    }

    function bindButtons() {
        document.addEventListener('click', function (event) {
            var button = event.target.closest && event.target.closest('[data-webpush-toggle]');
            if (!button) return;
            event.preventDefault();
            button.disabled = true;
            state().then(function (current) {
                return current === 'subscribed' ? unsubscribe() : subscribe();
            }).catch(function (e) {
                console.warn(e.message || e);
                document.dispatchEvent(new CustomEvent('webpush:error', {detail: e.message || String(e)}));
            }).then(function () {
                button.disabled = false;
            });
        });
    }

    function start() {
        state().then(function (current) {
            updateButtons(current);
            if (current === 'subscribed') refresh();
        });
    }

    function init(options) {
        Object.keys(options || {}).forEach(function (name) { config[name] = options[name]; });
        publicKey = null;
        start();
    }

    function subscription() {
        return supported() ? existingSubscription() : Promise.resolve(null);
    }

    window.WebPush = {init: init, subscribe: subscribe, unsubscribe: unsubscribe, state: state, subscription: subscription};
    bindButtons();
    if (config.api && config.site) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
        else start();
    }
})();
