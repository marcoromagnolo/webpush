# REST API of webpush, JSON. The site is in the url: /api/v1/sites/<code>/...
#
# Public (called by the browsers of the site, CORS only from the origins of the site):
#   GET    /sites/<code>/vapid-public-key   applicationServerKey to subscribe
#   POST   /sites/<code>/subscriptions      subscribe a browser (PushSubscription.toJSON())
#   DELETE /sites/<code>/subscriptions      unsubscribe a browser
#   POST   /sites/<code>/clicks             click on a notification (service worker)
# With a token ("Authorization: Bearer <token>", generated in the console, page API tokens), for the site of the
# token or every site:
#   POST   /sites/<code>/messages           queue a message
#   GET    /sites/<code>/messages/<id>      a message, its status and its deliveries
#   DELETE /sites/<code>/messages/<id>      cancel a queued message
#   GET    /ping                            check the token
#
# The old endpoints of the single site version (/subscription/, /push_message/) are kept for the site DEFAULT_SITE
# (blueprint legacy) until its clients move to /api/v1.
import hashlib
import json
import secrets
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, g

import db
import logg
import notifier
import settings
import util

logger = logg.create_logger('api')

api = Blueprint('api', __name__, url_prefix='/api/v1')
legacy = Blueprint('legacy', __name__)

TOKEN_PREFIX = 'wp_'
MAX_ENDPOINT = 1000


def generate_token():
    """A new token: shown once to the user, only its hash is stored."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token), token[:len(TOKEN_PREFIX) + 6]


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def error(status, message):
    response = jsonify({'error': message})
    response.status_code = status
    if status == 401:
        response.headers['WWW-Authenticate'] = 'Bearer'
    return response


def iso(value):
    # naive datetimes are local time (TZ): ISO 8601 with the offset
    return value.astimezone().isoformat() if value else None


def site_origins(site):
    """Origins allowed by CORS: the one of the url of the site, the allowed_origins and the one of the console
    (its test page of the site, Sites > Test page)."""
    origins = {notifier.origin(site['url']), notifier.origin(settings.PUBLIC_URL)}
    origins.update(line.strip().rstrip('/') for line in (site['allowed_origins'] or '').splitlines() if line.strip())
    return origins


def token_required(view):
    """A valid token for the site of the request (g.site) or for every site: g.api_token."""
    @wraps(view)
    def decorated(*args, **kwargs):
        scheme, _, token = request.headers.get('Authorization', '').partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            return error(401, 'Missing bearer token')
        api_token = db.use_api_token(hash_token(token.strip()), request.remote_addr)
        if api_token is None:
            # like the failed logins: repeated attempts block the ip
            util.block_ip(request.remote_addr)
            logger.warning(f"Invalid API token from ip: {request.remote_addr}")
            return error(401, 'Invalid, expired or revoked token')
        site = g.get('site')
        if site is not None and api_token['site_id'] not in (None, site['id']):
            return error(403, f"The token is not valid for the site {site['code']}")
        g.api_token = api_token
        return view(*args, **kwargs)
    return decorated


def origin_required(view):
    """Write requests of the browsers: only from an origin of the site."""
    @wraps(view)
    def decorated(*args, **kwargs):
        if request.headers.get('Origin') not in site_origins(g.site):
            logger.warning(f"Origin not allowed for {g.site['code']}: {request.headers.get('Origin')}")
            return error(403, 'Origin not allowed')
        return view(*args, **kwargs)
    return decorated


@api.url_value_preprocessor
def load_site(endpoint, values):
    """The site of the url in g.site: an unknown or disabled site answers 404."""
    if values and 'code' in values:
        g.site = db.get_site(code=values.pop('code'))


@api.before_request
def check_site():
    if 'site' in g and (g.site is None or not g.site['enabled']):
        return error(404, 'Unknown site')
    return None


@legacy.before_request
def load_default_site():
    g.site = db.get_site(code=settings.DEFAULT_SITE) if settings.DEFAULT_SITE else None
    if g.site is None or not g.site['enabled']:
        return error(404, 'Not found')
    return None


PUBLIC_ENDPOINTS = {'api.vapid_public_key', 'api.subscriptions', 'api.clicks', 'legacy.subscription'}


@api.after_request
@legacy.after_request
def cors(response):
    """CORS of the public endpoints: the origin of the request if it is an origin of the site."""
    site = g.get('site')
    request_origin = request.headers.get('Origin')
    if site and request.endpoint in PUBLIC_ENDPOINTS and request_origin in site_origins(site):
        response.headers['Access-Control-Allow-Origin'] = request_origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Max-Age'] = '86400'
        response.vary.add('Origin')
    return response


@api.errorhandler(400)
@legacy.errorhandler(400)
def bad_request(e):
    return error(400, 'Invalid JSON body')


def json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


# Public

@api.route('/sites/<code>/vapid-public-key', methods=['GET'])
def vapid_public_key():
    if not g.site['vapid_public_key']:
        return error(503, 'The site has no VAPID keys yet')
    return jsonify({'public_key': g.site['vapid_public_key']})


def parse_subscription(body):
    """PushSubscription.toJSON() of the browser: (endpoint, p256dh, auth, expiration time); ValueError if not valid."""
    subscription = body.get('subscription') or body.get('subscription_token') or body
    if not isinstance(subscription, dict):
        raise ValueError('subscription must be an object')
    endpoint = subscription.get('endpoint')
    keys = subscription.get('keys') if isinstance(subscription.get('keys'), dict) else {}
    expiration = subscription.get('expirationTime')
    if not isinstance(endpoint, str) or not endpoint.startswith('https://') or len(endpoint) > MAX_ENDPOINT:
        raise ValueError('endpoint must be an https url')
    if not isinstance(keys.get('p256dh'), str) or not isinstance(keys.get('auth'), str) \
            or not 0 < len(keys['p256dh']) <= 255 or not 0 < len(keys['auth']) <= 100:
        raise ValueError('keys.p256dh and keys.auth are required')
    if expiration is not None and (isinstance(expiration, bool) or not isinstance(expiration, (int, float))):
        raise ValueError('expirationTime must be null or epoch milliseconds')
    return endpoint, keys['p256dh'], keys['auth'], int(expiration) if expiration is not None else None


def subscribe():
    body = json_body()
    if body is None:
        return error(400, 'Invalid JSON body')
    try:
        endpoint, p256dh, auth, expiration = parse_subscription(body)
    except ValueError as e:
        return error(400, str(e))
    new = db.upsert_subscriber(g.site['id'], endpoint, p256dh, auth, expiration,
                               (request.headers.get('User-Agent') or '')[:255] or None)
    logger.info(f"Subscriber {'added' if new else 'updated'} for {g.site['code']}")
    return jsonify({'subscribed': True}), 201


@api.route('/sites/<code>/subscriptions', methods=['POST', 'DELETE'])
@origin_required
def subscriptions():
    if request.method == 'POST':
        return subscribe()
    body = json_body() or {}
    endpoint = body.get('endpoint') or (body.get('subscription') or {}).get('endpoint')
    if not isinstance(endpoint, str):
        return error(400, 'endpoint is required')
    return jsonify({'unsubscribed': db.unsubscribe(g.site['id'], endpoint)})


@api.route('/sites/<code>/clicks', methods=['POST'])
@origin_required
def clicks():
    message_id = (json_body() or {}).get('message_id')
    if not isinstance(message_id, int) or isinstance(message_id, bool):
        return error(400, 'message_id is required')
    db.add_click(g.site['id'], message_id)
    return '', 204


# With a token

def parse_send_after(value):
    if value in (None, ''):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('send_after must be an ISO 8601 date and time')
    # with an offset: to the local time of the application
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def queue_message(body):
    """Queue a message from a JSON body: title, options, url (shortcut of options.data.url), send_after."""
    if body is None:
        return error(400, 'Invalid JSON body')
    options = body.get('options') if body.get('options') is not None else {}
    if body.get('url') and isinstance(options, dict):
        options = dict(options, data=dict(options.get('data') or {}, url=body['url']))
    try:
        title, options_json = notifier.clean_message(body.get('title'), options)
        send_after = parse_send_after(body.get('send_after'))
    except ValueError as e:
        return error(400, str(e))
    message_id = db.add_message(g.site['id'], title, options_json, send_after, f"token {g.api_token['name']}"[:100])
    logger.info(f"Message {message_id} queued for {g.site['code']} by token {g.api_token['name']}")
    return jsonify({'id': message_id, 'status': 'queued'}), 202


def message_to_json(message):
    return {
        'id': message['id'],
        'site': message['site_code'],
        'title': message['title'],
        'options': json.loads(message['options']) if message['options'] else {},
        'status': message['status'],
        'send_after': iso(message['send_after']),
        'created_at': iso(message['created_at']),
        'sent_at': iso(message['sent_at']),
        'deliveries': [{'kind': d['kind'], 'slot': d['slot'],
                        'total': d['total'], 'success': d['success'], 'gone': d['gone'], 'errors': d['errors'],
                        'start_time': iso(d['start_time']), 'end_time': iso(d['end_time'])}
                       for d in db.get_deliveries(message_id=message['id'], limit=100)],
        'clicks': message['clicks'],
    }


@api.route('/sites/<code>/messages', methods=['POST'])
@token_required
def messages():
    return queue_message(json_body())


def site_message(message_id):
    message = db.get_message(message_id)
    return message if message and message['site_id'] == g.site['id'] else None


@api.route('/sites/<code>/messages/<int:message_id>', methods=['GET', 'DELETE'])
@token_required
def message(message_id):
    found = site_message(message_id)
    if found is None:
        return error(404, 'Unknown message')
    if request.method == 'DELETE':
        if not db.cancel_message(message_id):
            return error(409, f"The message is {found['status']}: only a queued message can be cancelled")
        logger.info(f"Message {message_id} cancelled by token {g.api_token['name']}")
        found = db.get_message(message_id)
    return jsonify(message_to_json(found))


@api.route('/ping', methods=['GET'])
@token_required
def ping():
    return jsonify({'ok': True, 'token': g.api_token['name']})


# Old endpoints of the single site version (site DEFAULT_SITE)

@legacy.route('/subscription/', methods=['GET', 'POST'])
def subscription():
    if request.method == 'GET':
        return vapid_public_key()
    if request.headers.get('Origin') not in site_origins(g.site):
        return error(403, 'Origin not allowed')
    return subscribe()


@legacy.route('/push_message/', methods=['POST'])
@token_required
def push_message():
    return queue_message(json_body())
