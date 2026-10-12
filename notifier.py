# Sending of the notifications to the subscribers of a site (Web Push with VAPID, pywebpush).
#
# At every schedule slot of a site (scheduler.py) the first message of its queue is sent to a share of the
# subscribers (partition): with S slots in a day and M messages per day (setting of the site) the subscribers
# are split in ceil(S / M) partitions, used in turn by the slots of the day, so every subscriber receives about
# M messages a day. A partition is MOD(subscriber id, partitions): stable when subscribers are added or removed.
#
# Outcome for each subscriber: 201 delivered; 404 / 410 the subscription doesn't exist any more (removed at once);
# other errors are counted, the subscriber is removed after PUSH_MAX_FAILURES failures in a row.
import json
import math
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import requests
from pywebpush import webpush, WebPushException

import db
import errorlog
import logg
import settings
import vapid

logger = logg.create_logger('notifier')

# options of the Notification API passed to the service worker (showNotification)
OPTION_TYPES = {
    'body': str, 'icon': str, 'image': str, 'badge': str, 'dir': str, 'lang': str, 'tag': str, 'sound': str,
    'timestamp': int, 'requireInteraction': bool, 'renotify': bool, 'silent': bool,
    'vibrate': list, 'actions': list, 'data': dict,
}
TYPE_NAMES = {str: 'a string', int: 'a number', bool: 'true or false', list: 'a list', dict: 'an object'}
DIRECTIONS = ('auto', 'ltr', 'rtl')
MAX_ACTIONS = 2  # shown by Chrome; the others are ignored by the browsers
# the payload is encrypted and sent in one request: the push services accept up to 4096 bytes
MAX_PAYLOAD = 3500


def clean_message(title, options):
    """Title and options of a message checked (API and console): (title, options as JSON). ValueError if not valid."""
    title = (title or '').strip() if isinstance(title, str) else ''
    if not 1 <= len(title) <= 255:
        raise ValueError("The title is required (max 255 characters)")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    clean = {}
    for name, value in options.items():
        if name not in OPTION_TYPES or value is None or value == '' or value == []:
            continue  # unknown options are ignored (as the browsers do)
        expected = OPTION_TYPES[name]
        if (expected is int and isinstance(value, bool)) or not isinstance(value, expected):
            raise ValueError(f"options.{name} must be {TYPE_NAMES[expected]}")
        clean[name] = value.strip() if isinstance(value, str) else value
    if clean.get('dir', 'auto') not in DIRECTIONS:
        raise ValueError(f"options.dir must be one of {', '.join(DIRECTIONS)}")
    actions = clean.get('actions', [])
    if len(actions) > MAX_ACTIONS or not all(isinstance(a, dict) and a.get('action') and a.get('title') for a in actions):
        raise ValueError(f"options.actions: at most {MAX_ACTIONS} objects with action and title")
    if 'vibrate' in clean and not all(isinstance(v, int) and not isinstance(v, bool) for v in clean['vibrate']):
        raise ValueError("options.vibrate must be a list of milliseconds")
    url = clean.get('data', {}).get('url')
    if url is not None and not (isinstance(url, str) and url.startswith(('https://', 'http://'))):
        raise ValueError("options.data.url must be an http(s) url")
    options_json = json.dumps(clean, ensure_ascii=False, separators=(',', ':'))
    if len(title.encode('utf-8')) + len(options_json.encode('utf-8')) > MAX_PAYLOAD:
        raise ValueError(f"The notification is too long: title and options together at most {MAX_PAYLOAD} bytes")
    return title, options_json


def build_payload(site, message, track=True):
    """The JSON sent to the service worker: title, options, defaults of the site and the click tracking."""
    options = json.loads(message['options']) if message['options'] else {}
    payload = {'title': message['title'], **{k: v for k, v in options.items() if k in OPTION_TYPES}}
    if site['default_icon']:
        payload.setdefault('icon', site['default_icon'])
    if site['default_badge']:
        payload.setdefault('badge', site['default_badge'])
    data = dict(payload.get('data') or {})
    if track:
        # the service worker (static/webpush-sw.js) sends the click to click_url
        data['webpush'] = {'message_id': message['id'],
                           'click_url': f"{settings.PUBLIC_URL}/api/v1/sites/{site['code']}/clicks"}
    payload['data'] = data
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def partition_for_slot(day_slots, hour, minute, messages_per_day):
    """(partition index, partitions) of a slot of the day: day_slots are the (hour, minute) of the day in order.
    None if the slot is not among them."""
    if (hour, minute) not in day_slots:
        return None
    partitions = max(1, math.ceil(len(day_slots) / max(1, messages_per_day)))
    return day_slots.index((hour, minute)) % partitions, partitions


_local = threading.local()


def session():
    """A requests session per thread: the connections to the push services are reused."""
    if not hasattr(_local, 'session'):
        _local.session = requests.Session()
    return _local.session


def origin(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def response_text(response, limit=300):
    """Body of the answer of a push service, short (it says why: e.g. VAPID key mismatch, expired token)."""
    try:
        return ' '.join(response.text.split())[:limit]
    except Exception:
        return ''


def claims_of(site, subscriber):
    # new claims at every sending: pywebpush writes aud and exp in the dict
    return {'sub': site['vapid_sub'], 'aud': origin(subscriber['endpoint'])}


def push(site, signer, subscriber, payload, claims):
    return webpush(subscription_info={'endpoint': subscriber['endpoint'],
                                      'keys': {'p256dh': subscriber['keys_p256dh'], 'auth': subscriber['keys_auth']}},
                   data=payload, vapid_private_key=signer, vapid_claims=claims, ttl=site['ttl_seconds'],
                   timeout=settings.PUSH['timeout'], requests_session=session())


def send_one(site, signer, subscriber, payload):
    """Send to one subscriber: (outcome, summary, detail). outcome: 'success' | 'gone' | 'expired' | 'error';
    summary groups the errors of a sending (log), detail is saved on the subscriber (last_error)."""
    host = urlparse(subscriber['endpoint']).netloc
    if subscriber['expiration_time'] and subscriber['expiration_time'] < time.time() * 1000:
        return 'expired', None, 'expired: past the expirationTime of the browser'
    try:
        push(site, signer, subscriber, payload, claims_of(site, subscriber))
        return 'success', None, None
    except WebPushException as e:
        response = e.response
        if response is None:
            return 'error', f"{e} ({host})", str(e)
        answer = response_text(response)
        detail = f"{response.status_code} {response.reason} from {host}" + (f": {answer}" if answer else '')
        if response.status_code in (404, 410):
            return 'gone', None, detail
        return 'error', f"{response.status_code} {response.reason} ({host})", detail
    except requests.RequestException as e:
        return 'error', f"{type(e).__name__} ({host})", f"{type(e).__name__} calling {host}: {e}"
    except Exception as e:
        # a subscriber with broken keys must not stop the sending
        return 'error', f"{type(e).__name__}: {e}", f"{type(e).__name__}: {e}"


HINTS = {
    201: "Accepted by the push service: if no notification appears, the browser or the system hides it "
         "(permission of the site, Do not disturb, notifications of the browser in the system settings).",
    400: "Request refused: invalid headers or payload (see the answer).",
    401: "VAPID signature refused: check the keys of the site and the contact (mailto: or https).",
    403: "VAPID key different from the one of the subscription: the browser subscribed with other keys "
         "(new keys generated?). Disable and enable the notifications again.",
    404: "The subscription doesn't exist any more (revoked or expired): subscribe again.",
    410: "The subscription doesn't exist any more (revoked or expired): subscribe again.",
    413: "Payload too large: shorter title and options.",
    429: "Too many requests to the push service: retry later.",
}


def debug_send(site, subscriber):
    """Send a test notification to one subscriber and describe everything (test page of the console):
    request, answer of the push service, hint. The subscriber is not changed."""
    payload = json.dumps({'title': f"WebPush test - {site['name']}",
                          'body': f"Sent at {datetime.now().strftime('%H:%M:%S')}: push service, browser and "
                                  f"service worker work.",
                          'icon': site['default_icon'] or None, 'tag': 'webpush-test', 'renotify': True,
                          'data': {}}, separators=(',', ':'))
    claims = claims_of(site, subscriber)
    result = {'endpoint_host': urlparse(subscriber['endpoint']).netloc, 'ttl': site['ttl_seconds'],
              'payload_bytes': len(payload.encode('utf-8')), 'claims': claims}
    started = time.monotonic()
    try:
        response = push(site, vapid.signer(site['vapid_private_key']), subscriber, payload, claims)
    except WebPushException as e:
        response = e.response
        if response is None:
            result.update(ok=False, error=str(e), hint="The request was not sent: see the error.")
            return result
    except Exception as e:
        result.update(ok=False, error=f"{type(e).__name__}: {e}",
                      hint="The push service was not reached (network, DNS, timeout) or the keys of the subscription are broken.")
        return result
    status = response.status_code
    result.update(ok=status in (200, 201, 202), status=status, reason=response.reason, answer=response_text(response, 1000),
                  elapsed_ms=round((time.monotonic() - started) * 1000),
                  headers={k: v for k, v in response.headers.items() if k.lower() in ('location', 'ttl', 'retry-after')},
                  hint=HINTS.get(201 if status in (200, 202) else status,
                                 "Unexpected answer: see the status and the answer of the push service."))
    return result


def deliver(site, message, subscribers, kind, slot=None, partition_index=0, partitions=1):
    """Send a message to the subscribers, save the outcome (subscribers and delivery): the counts."""
    signer = vapid.signer(site['vapid_private_key'])
    payload = build_payload(site, message, track=kind != 'test')
    delivery_id = db.start_delivery(site['id'], message['id'], kind, slot, partition_index, partitions, len(subscribers))
    outcomes = {'success': {}, 'gone': {}, 'expired': {}, 'error': {}}
    errors = Counter()
    if subscribers:
        with ThreadPoolExecutor(max_workers=min(settings.PUSH['workers'], len(subscribers))) as pool:
            results = pool.map(lambda s: send_one(site, signer, s, payload), subscribers)
            for subscriber, (outcome, summary, detail) in zip(subscribers, results):
                outcomes[outcome][subscriber['id']] = detail
                if summary:
                    errors[summary] += 1
    db.save_sending_results(list(outcomes['success']), outcomes['error'],
                            {'gone': outcomes['gone'], 'expired': outcomes['expired']}, settings.PUSH['max_failures'])
    counts = {'total': len(subscribers), 'success': len(outcomes['success']),
              'gone': len(outcomes['gone']) + len(outcomes['expired']), 'errors': len(outcomes['error'])}
    db.finish_delivery(delivery_id, counts['success'], counts['gone'], counts['errors'])
    logger.info(f"Message {message['id']} of {site['code']} ({kind}{' ' + slot if slot else ''},"
                f" partition {partition_index + 1}/{partitions}): {counts}")
    if errors:
        summary = '; '.join(f"{error}: {n}" for error, n in errors.most_common(5))
        # all failed: an error of the page Errors, otherwise only in the log
        (logger.error if not counts['success'] else logger.warning)(
            f"Message {message['id']} of {site['code']}: {counts['errors']} of {counts['total']} sendings failed: {summary}")
    return counts


def can_send(site):
    if site is None or not site['enabled']:
        return False
    if not site['vapid_private_key']:
        logger.error(f"Site {site['code']} has no VAPID keys: nothing sent (console > Sites > Keys)",
                     extra={'site_id': site['id']})
        return False
    return True


def send_slot(site_id, day, hour, minute):
    """A schedule slot of a site: the next message of the queue to the partition of the slot."""
    site = db.get_site(site_id)
    if not can_send(site):
        return
    slot = f"{hour:02}:{minute:02}"
    day_slots = [(s['hour'], s['minute']) for s in db.get_slots(site_id) if s['day'] == day]
    partition = partition_for_slot(day_slots, hour, minute, site['messages_per_day'])
    if partition is None:
        logger.info(f"Slot {slot} of {site['code']} no longer scheduled")
        return
    if db.count_active_subscribers(site_id) == 0:
        logger.info(f"Slot {slot} of {site['code']}: no subscriber, the queue is kept")
        return
    message = db.claim_next_message(site_id)
    if message is None:
        logger.info(f"Slot {slot} of {site['code']}: the queue is empty")
        return
    with errorlog.context(site_id, message['id']):
        try:
            partition_index, partitions = partition
            subscribers = db.get_subscribers_partition(site_id, partitions, partition_index)
            counts = deliver(site, message, subscribers, 'slot', slot, partition_index, partitions)
            db.finish_message(message['id'], 'failed' if counts['total'] and not counts['success'] else 'sent')
        except Exception as e:
            db.finish_message(message['id'], 'failed')
            logger.error(f"Sending of message {message['id']} of {site['code']} failed: {e}", exc_info=True)


def send_now(message_id):
    """Send a queued message at once to all the active subscribers of its site (console). False if not queued."""
    message = db.get_message(message_id)
    site = db.get_site(message['site_id']) if message else None
    if not can_send(site) or not db.claim_message(message_id):
        return False
    with errorlog.context(site['id'], message_id):
        try:
            subscribers = db.get_subscribers_partition(site['id'], 1, 0)
            counts = deliver(site, message, subscribers, 'now')
            db.finish_message(message_id, 'failed' if counts['total'] and not counts['success'] else 'sent')
        except Exception as e:
            db.finish_message(message_id, 'failed')
            logger.error(f"Sending of message {message_id} of {site['code']} failed: {e}", exc_info=True)
    return True


def send_test(message_id):
    """Send a message to the test browsers of its site, its status doesn't change: the counts, None if impossible."""
    message = db.get_message(message_id)
    site = db.get_site(message['site_id']) if message else None
    if site is None or not site['vapid_private_key']:
        return None
    with errorlog.context(site['id'], message_id):
        return deliver(site, message, db.get_test_subscribers(site['id']), 'test')


def start(target, *args):
    """Run a sending in the background (console): the page answers at once."""
    threading.Thread(target=target, args=args, daemon=True, name=f"{target.__name__}-{args}").start()

