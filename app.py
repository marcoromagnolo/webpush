# webpush: Web Push notifications of many websites. One process: the console (this module), the REST API
# (api.py) and the scheduler of the sendings (scheduler.py, notifier.py). See README.md.
import ipaddress
import json
import re
import signal
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

import bcrypt
import mysql.connector
import werkzeug.exceptions
from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup
from flask_jwt_extended import (JWTManager, create_access_token, get_jwt_identity, jwt_required, set_access_cookies,
                                unset_jwt_cookies)

import api
import db
import dbpool
import errorlog
import logg
import notifier
import scheduler
import settings
import timeline
import util
import vapid

logger = logg.create_logger('app')

app = Flask(__name__)
if settings.WEB_SETTINGS['behind_proxy']:
    # one proxy in front: real client ip (ip blocking) and https in the external urls
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Configure JWT (as in scrapynews): token in a cookie, CSRF token in the forms
app.config['JWT_SECRET_KEY'] = settings.JWT_SECRET_KEY
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=settings.JWT_ACCESS_TOKEN_EXPIRES)
app.config['JWT_TOKEN_LOCATION'] = ['cookies']
# cookie names of this application: the cookies don't depend on the port, and other consoles on the same host
# (e.g. scrapynews on 127.0.0.1) use the default names with their own secret ("Signature verification failed")
app.config['JWT_ACCESS_COOKIE_NAME'] = 'webpush_access_token'
app.config['JWT_ACCESS_CSRF_COOKIE_NAME'] = 'webpush_csrf_token'
app.config['JWT_COOKIE_SAMESITE'] = 'Lax'
app.config['JWT_CSRF_CHECK_FORM'] = True
app.config['JWT_CSRF_REQUEST_FIELD_NAME'] = 'csrf_token'
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

PASSWORD_MIN_LENGTH = 8
USERNAME_PATTERN = r"^[A-Za-z0-9._-]{3,50}$"
SITE_CODE_PATTERN = r"^[a-z0-9][a-z0-9-]{1,49}$"
SITE_COOKIE = 'site'

# True once an administrator exists (checked on the DB until then)
users_configured = False

jwt = JWTManager(app)

app.register_blueprint(api.api)
app.register_blueprint(api.legacy)

app.jinja_env.globals.update(DAY_LABELS=scheduler.DAY_LABELS, PHASES=errorlog.PHASES, PHASE_BADGES=errorlog.PHASE_BADGES,
                             PER_PAGE_CHOICES=util.PER_PAGE_CHOICES,
                             REFRESH_CHOICES=(0, 5, 10, 15, 30, 60),
                             REFRESH_SECONDS=settings.WEB_SETTINGS['refresh_seconds'])

MESSAGE_STATUSES = {'queued': 'Queued', 'sending': 'Sending', 'sent': 'Sent', 'failed': 'Failed', 'cancelled': 'Cancelled'}
MESSAGE_BADGES = {'queued': 'bg-primary', 'sending': 'bg-info text-dark', 'sent': 'bg-success', 'failed': 'bg-danger',
                  'cancelled': 'bg-secondary'}
app.jinja_env.globals.update(MESSAGE_STATUSES=MESSAGE_STATUSES, MESSAGE_BADGES=MESSAGE_BADGES)


def info(text):
    """Info icon of a form field (next to its label): the text in a tooltip, on hover and on keyboard focus."""
    return Markup('<span class="info-tip" tabindex="0" role="note" aria-label="{0}" data-bs-toggle="tooltip" '
                  'data-bs-title="{0}">i</span>').format(text)


app.jinja_env.globals['info'] = info


def current_site():
    """The site chosen in the menu (cookie), None for all the sites."""
    if 'current_site' not in g:
        code = request.cookies.get(SITE_COOKIE)
        g.current_site = db.get_site(code=code) if code else None
    return g.current_site


def site_id_filter():
    site = current_site()
    return site['id'] if site else None


@app.context_processor
def common():
    def open_errors_count():
        # badge of the menu item Errors: called by the templates only (header.html)
        try:
            return sum(db.count_open_errors().values())
        except Exception:
            return 0

    def back_url():
        # this page without its notices, where the buttons of the page go back
        args = {k: v for k, v in request.args.items() if k not in ('message', 'error', 'notice')}
        return request.path + ('?' + urlencode(args) if args else '')

    return {'current_year': datetime.now().year, 'open_errors_count': open_errors_count, 'back_url': back_url,
            'csrf_access_token': request.cookies.get(app.config['JWT_ACCESS_CSRF_COOKIE_NAME']),
            'current_site': current_site, 'all_sites': db.get_sites}


@app.before_request
def limit_remote_addr():
    if util.is_blocked_ip(request.remote_addr):
        logger.warning(f"Blocked ip: {request.remote_addr}")
        return 'Forbidden :(', 403
    return None


@app.before_request
def require_setup():
    # First access: no user yet, every page of the console leads to the administrator setup
    global users_configured
    if users_configured or request.endpoint in ('setup', 'static', 'health') or request.blueprint in ('api', 'legacy'):
        return None
    if db.count_users() > 0:
        users_configured = True
        return None
    return redirect(url_for('setup'))


def login_response(user_id):
    response = redirect(url_for('index'))
    set_access_cookies(response, create_access_token(identity=str(user_id)))
    return response


def check_new_password(username, password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f'The password must be at least {PASSWORD_MIN_LENGTH} characters long'
    if password != confirm:
        return 'The passwords do not match'
    if password.lower() == username.lower():
        return 'The password must be different from the username'
    return None


def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def current_user():
    return db.get_user(user_id=int(get_jwt_identity()))


def wants_json():
    return request.blueprint in ('api', 'legacy')


@app.errorhandler(werkzeug.exceptions.NotFound)
def handle_not_found(e):
    if wants_json():
        return api.error(404, 'Not found')
    return render_template('not_found.html', title='Not found'), 404


@app.errorhandler(werkzeug.exceptions.MethodNotAllowed)
def handle_method_not_allowed(e):
    return api.error(405, 'Method not allowed') if wants_json() else ('Method not allowed', 405)


@app.errorhandler(werkzeug.exceptions.BadRequest)
def handle_bad_request(e):
    util.block_ip(request.remote_addr)
    logger.warning(f"Bad request from ip: {request.remote_addr} {e}")
    return api.error(400, 'Bad request') if wants_json() else ('Bad Request', 400)


@app.errorhandler(werkzeug.exceptions.RequestEntityTooLarge)
def handle_too_large(e):
    return api.error(413, 'Request too large') if wants_json() else ('Request too large', 413)


# MariaDB errors meaning "not available": can't connect, connection lost / timed out, too many connections
DB_UNAVAILABLE_ERRORS = {1040, 2002, 2003, 2005, 2006, 2013, 2055}


def database_unavailable():
    if wants_json():
        return api.error(503, 'Database not available')
    return render_template("db_error.html", title="Database not available"), 503


@app.errorhandler(dbpool.PoolTimeout)
def handle_pool_timeout(e):
    logger.error(f"Database not available: {e}", extra={'error_log': False})
    return database_unavailable()


@app.errorhandler(mysql.connector.Error)
def handle_database_error(e):
    if getattr(e, 'errno', None) in DB_UNAVAILABLE_ERRORS:
        logger.error(f"Database not available: {e}", extra={'error_log': False})
        return database_unavailable()
    logger.error(e, exc_info=True)
    return api.error(500, 'Error') if wants_json() else ('Error', 500)


@app.errorhandler(Exception)
def handle_generic_error(e):
    if isinstance(e, werkzeug.exceptions.HTTPException):
        return e
    logger.error(e, exc_info=True)
    return api.error(500, 'Error') if wants_json() else ('Error', 500)


@jwt.expired_token_loader
def expired_token_callback(header, body):
    return redirect(url_for('login'))


@jwt.unauthorized_loader
def unauthorized_callback(message):
    logger.warning(f"Unauthorized request {message}")
    return redirect(url_for('login'))


@jwt.invalid_token_loader
def invalid_token_callback(message):
    # a cookie signed with another secret (JWT_SECRET_KEY changed) or corrupted: removed, back to the login. The ip is
    # not blocked: it is not a guessed password, the failed logins are blocked by the login
    logger.warning(f"Invalid token {message}")
    response = redirect(url_for('login'))
    unset_jwt_cookies(response)
    return response


@app.route('/health', methods=['GET'])
def health():
    """Healthcheck of the container: the application and the database answer."""
    db.count_users()
    return jsonify({'ok': True})


# Login

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            error = 'Username and password are required'
        else:
            user = db.get_user(username)
            if user and bcrypt.checkpw(password.encode('utf-8'), user['hash'].encode('utf-8')):
                return login_response(user['id'])
            util.block_ip(request.remote_addr)
            error = 'Invalid username or password'
    return render_template("login.html", title="Login", error=error)


@app.route('/logout', methods=['POST'])
@jwt_required()
def logout():
    response = redirect(url_for('login'))
    unset_jwt_cookies(response)
    return response


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    # Only while there is no user: create the administrator chosen at the first access
    if db.count_users() > 0:
        return redirect(url_for('login'))
    error = None
    username = ''
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not re.match(USERNAME_PATTERN, username):
            error = 'The username must be 3-50 characters: letters, numbers, dot, dash or underscore'
        else:
            error = check_new_password(username, password, request.form.get('confirm_password', ''))
        if error is None:
            user_id = db.create_first_user(username, hash_password(password))
            if user_id is None:
                # another request created the administrator in the meantime
                return redirect(url_for('login'))
            logger.info(f"Administrator '{username}' created from ip: {request.remote_addr}")
            return login_response(user_id)
    return render_template("setup.html", title="Setup", error=error, username=username, min_length=PASSWORD_MIN_LENGTH)


@app.route('/change-password', methods=['GET', 'POST'])
@jwt_required()
def change_password():
    user = current_user()
    if user is None:
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        if not bcrypt.checkpw(current_password.encode('utf-8'), user['hash'].encode('utf-8')):
            error = 'Current password is wrong'
        elif current_password == new_password:
            error = 'The new password must be different from the current one'
        else:
            error = check_new_password(user['username'], new_password, request.form.get('confirm_password', ''))
        if error is None:
            db.update_user_password(user['id'], hash_password(new_password))
            logger.info(f"Password changed for user '{user['username']}'")
            return login_response(user['id'])
    return render_template("change_password.html", title="Change Password", error=error, username=user['username'],
                           min_length=PASSWORD_MIN_LENGTH)


def safe_next(target, default):
    """A local path to go back to (never another host)."""
    if target and target.startswith('/') and not target.startswith('//') and not urlparse(target).netloc:
        return target
    return default


@app.route('/select-site', methods=['GET'])
@jwt_required()
def select_site():
    """Site of the menu: every page shows its data (empty = all the sites)."""
    code = request.args.get('site', '')
    response = redirect(safe_next(request.args.get('next'), url_for('index')))
    if code and db.get_site(code=code):
        response.set_cookie(SITE_COOKIE, code, max_age=365 * 24 * 3600, samesite='Lax', httponly=True)
    else:
        response.delete_cookie(SITE_COOKIE)
    return response


def list_args(*names):
    """Non empty query parameters to keep in the pagination links."""
    return {name: request.args[name] for name in names if request.args.get(name)}


def paginate(total):
    return util.get_paginate(total, request.args.get('current'), request.args.get('records', type=int))


# Home

@app.route("/", methods=["GET"])
@jwt_required()
def index():
    site = current_site()
    sites = [site] if site else db.get_sites()
    now = datetime.now()
    return render_template("index.html", title="Home", stats=db.get_stats(site_id_filter()), now=now,
                           timeline=timeline.today([s for s in sites if s['enabled']], now),
                           summary=None if site else db.get_sites_summary(),
                           deliveries=db.get_deliveries(site_id=site_id_filter(), limit=10),
                           errors=db.get_error_logs({'status': 'open', 'site_id': site_id_filter()}, 5)[0])


# Messages

def message_filters():
    status = request.args.get('status', 'queued')
    return {'site_id': site_id_filter(), 'status': status if status in MESSAGE_STATUSES else None,
            'q': request.args.get('q', '').strip()[:200]}


@app.route("/messages", methods=["GET"])
@jwt_required()
def messages():
    filters = message_filters()
    _, total = db.get_messages(filters, 1)
    pages = paginate(total)
    rows, _ = db.get_messages(filters, pages['records_per_page'], pages['offset'])
    for row in rows:
        row['options_dict'] = json.loads(row['options']) if row['options'] else {}
    return render_template("messages.html", title="Messages", messages=rows, filters=filters, paginate=pages,
                           counts=db.count_messages_by_status(site_id_filter()), current_route='messages',
                           records_name='messages', args=list_args('status', 'q', 'records'),
                           message=request.args.get('message'), error=request.args.get('error'))


def form_to_message(form):
    """Fields of the message form to (title, options as JSON); ValueError if not valid."""
    options = {}
    extra = form.get('extra', '').strip()
    if extra:
        try:
            options = json.loads(extra)
        except ValueError as e:
            raise ValueError(f"Extra options: not valid JSON ({e})")
        if not isinstance(options, dict):
            raise ValueError("Extra options must be a JSON object")
    for name in ('body', 'icon', 'image', 'badge', 'tag'):
        if form.get(name, '').strip():
            options[name] = form[name].strip()
    for name in ('requireInteraction', 'renotify', 'silent'):
        if form.get(name) == 'on':
            options[name] = True
    data = options.get('data') if isinstance(options.get('data'), dict) else {}
    if form.get('url', '').strip():
        data['url'] = form['url'].strip()
    actions, action_urls = [], {}
    for n in (1, 2):
        title, url = form.get(f'action{n}_title', '').strip(), form.get(f'action{n}_url', '').strip()
        if title:
            actions.append({'action': f'a{n}', 'title': title})
            if url:
                action_urls[f'a{n}'] = url
    if actions:
        options['actions'] = actions
    if action_urls:
        data['actions'] = action_urls
    if data:
        options['data'] = data
    return notifier.clean_message(form.get('title'), options)


def message_to_form(message):
    """The form fields of a message (the options not in the form go to Extra options)."""
    options = json.loads(message['options']) if message.get('options') else {}
    data = dict(options.pop('data', None) or {})
    form = {'title': message.get('title', ''), 'url': data.pop('url', ''),
            'send_after': message['send_after'].strftime('%Y-%m-%dT%H:%M') if message.get('send_after') else ''}
    for name in ('body', 'icon', 'image', 'badge', 'tag'):
        form[name] = options.pop(name, '')
    for name in ('requireInteraction', 'renotify', 'silent'):
        form[name] = bool(options.pop(name, False))
    action_urls = data.pop('actions', {}) if isinstance(data.get('actions'), dict) else {}
    for n, action in enumerate(options.pop('actions', [])[:2], 1):
        form[f'action{n}_title'] = action.get('title', '')
        form[f'action{n}_url'] = action_urls.get(action.get('action'), '')
    if data:
        options['data'] = data
    form['extra'] = json.dumps(options, indent=2, ensure_ascii=False) if options else ''
    return form


def parse_local_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Send after: not a valid date and time")


def message_form_page(message, form, error=None, site=None):
    sites = db.get_sites()
    preview_site = site or current_site() or (sites[0] if sites else None)
    return render_template("message_form.html", title="Messages", message=message, form=form, error=error,
                           sites=sites, site=site, preview_site=preview_site,
                           deliveries=db.get_deliveries(message_id=message['id'], limit=100) if message else [],
                           test_subscribers=db.get_test_subscribers(message['site_id']) if message else [],
                           notice=request.args.get('message'), action_error=request.args.get('error'),
                           running=scheduler.is_running(message['site_id']) if message else False), (400 if error else 200)


@app.route("/messages/new", methods=["GET", "POST"])
@jwt_required()
def message_new():
    site = current_site()
    if request.method == 'GET':
        # Duplicate: the form of a copy of a message
        source = db.get_message(request.args.get('copy', type=int)) if request.args.get('copy') else None
        form = message_to_form(source) if source else {}
        if source:
            form['send_after'] = ''
        return message_form_page(None, form, site=db.get_site(source['site_id']) if source else site)
    form = request.form.to_dict()
    site = db.get_site(request.form.get('site_id', type=int)) if request.form.get('site_id') else site
    try:
        if site is None:
            raise ValueError("Choose the site")
        title, options = form_to_message(request.form)
        send_after = parse_local_datetime(form.get('send_after'))
    except ValueError as e:
        return message_form_page(None, form, str(e), site)
    message_id = db.add_message(site['id'], title, options, send_after, current_user()['username'])
    logger.info(f"Message {message_id} queued for {site['code']} from the console")
    return redirect(url_for('message_edit', message_id=message_id, message='Message queued'))


@app.route("/messages/<int:message_id>", methods=["GET", "POST"])
@jwt_required()
def message_edit(message_id):
    message = db.get_message(message_id)
    if message is None:
        return render_template('not_found.html', title='Not found'), 404
    site = db.get_site(message['site_id'])
    if request.method == 'GET':
        return message_form_page(message, message_to_form(message), site=site)
    form = request.form.to_dict()
    try:
        title, options = form_to_message(request.form)
        send_after = parse_local_datetime(form.get('send_after'))
    except ValueError as e:
        return message_form_page(message, form, str(e), site)
    if not db.update_message(message_id, title, options, send_after):
        return message_form_page(message, form, "The message is no longer in the queue: it can't be changed", site)
    return redirect(url_for('message_edit', message_id=message_id, message='Message saved'))


@app.route("/messages/<int:message_id>/action", methods=["POST"])
@jwt_required()
def message_action(message_id):
    """Buttons of the messages: cancel, up, down, top, send-now, send-test. Back to the page of the button."""
    action = request.form.get('action')
    back = safe_next(request.form.get('next'), url_for('messages'))
    message = db.get_message(message_id)
    if message is None:
        return redirect(back)
    notice, error = None, None
    if action == 'cancel':
        notice = 'Message cancelled' if db.cancel_message(message_id) else None
        error = None if notice else 'The message is no longer in the queue'
    elif action in ('up', 'down', 'top'):
        db.move_message(message_id, {'up': -1, 'down': 1, 'top': -2}[action])
    elif action == 'send-now':
        site = db.get_site(message['site_id'])
        if message['status'] != 'queued':
            error = 'The message is no longer in the queue'
        elif not site['vapid_private_key']:
            error = 'The site has no VAPID keys: Sites > Keys'
        else:
            logger.info(f"Message {message_id} sent now by {current_user()['username']}")
            notifier.start(notifier.send_now, message_id)
            notice = 'Sending to all the subscribers started: reload the page to see the result'
    elif action == 'send-test':
        if not db.get_test_subscribers(message['site_id']):
            error = 'No test browser for this site: Sites > the site > Test browsers'
        else:
            counts = notifier.send_test(message_id)
            if counts is None:
                error = 'The site has no VAPID keys: Sites > Keys'
            else:
                notice = f"Test sent: {counts['success']} of {counts['total']} test browsers"
                error = None if counts['success'] == counts['total'] else f"Test: {counts['errors'] + counts['gone']} test browsers failed (see Errors)"
    if notice or error:
        back += ('&' if '?' in back else '?') + urlencode({'error': error} if error else {'message': notice})
    return redirect(back)


# Subscribers

@app.route("/subscribers", methods=["GET"])
@jwt_required()
def subscribers():
    status = request.args.get('status', 'active')
    filters = {'site_id': site_id_filter(), 'status': status if status in db.SUBSCRIBER_STATUSES else 'active',
               'service': request.args.get('service', '')[:100]}
    _, total = db.get_subscribers(filters, 1)
    pages = paginate(total)
    rows, _ = db.get_subscribers(filters, pages['records_per_page'], pages['offset'])
    for row in rows:
        row['service'] = db.push_service_of(row['endpoint'])[1]
    growth = db.get_subscriber_growth(site_id_filter(), 30)
    return render_template("subscribers.html", title="Subscribers", subscribers=rows, filters=filters, paginate=pages,
                           statuses=db.SUBSCRIBER_STATUSES, services=db.get_push_services(site_id_filter()),
                           growth=growth, growth_max=max([day['active'] for day in growth] + [1]),
                           current_route='subscribers', records_name='subscribers',
                           args=list_args('status', 'service', 'records'), stats=db.get_stats(site_id_filter()))


@app.route("/subscribers/<int:subscriber_id>/remove", methods=["POST"])
@jwt_required()
def subscriber_remove(subscriber_id):
    db.remove_subscriber(subscriber_id)
    logger.info(f"Subscriber {subscriber_id} removed by hand")
    return redirect(safe_next(request.form.get('next'), url_for('subscribers')))


# Schedules

TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")


def parse_times(text):
    """'8:00 9:30, 12' -> [(8, 0), (9, 30), (12, 0)]; ValueError on a wrong time."""
    times = set()
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        if token.isdigit() and int(token) < 24:
            token += ':00'
        match = TIME_RE.match(token)
        if not match:
            raise ValueError(f"'{token}' is not a time (HH:MM)")
        times.add((int(match.group(1)), int(match.group(2))))
    return sorted(times)


def schedule_summary(site, slots):
    per_day = {day: [s for s in slots if s['day'] == day] for day in scheduler.DAY_LABELS}
    counts = [len(v) for v in per_day.values() if v]
    return {
        'per_day': per_day,
        'slots_week': len(slots),
        'max_per_day': max(counts) if counts else 0,
        'partitions': {day: max(1, -(-len(v) // max(1, site['messages_per_day']))) for day, v in per_day.items()},
    }


@app.route("/schedules", methods=["GET", "POST"])
@jwt_required()
def schedules():
    site = current_site()
    if site is None:
        return render_template("schedules.html", title="Schedules", site=None, sites=db.get_sites_summary())
    error, form = None, None
    if request.method == 'POST':
        form = request.form.to_dict()
        try:
            messages_per_day = request.form.get('messages_per_day', type=int)
            if not messages_per_day or not 1 <= messages_per_day <= 24:
                raise ValueError("Messages per subscriber a day: from 1 to 24")
            slots = []
            for day in scheduler.DAY_LABELS:
                try:
                    slots += [(day, h, m) for h, m in parse_times(request.form.get(f'day_{day}', ''))]
                except ValueError as e:
                    raise ValueError(f"{scheduler.DAY_LABELS[day]}: {e}")
        except ValueError as e:
            error = str(e)
        else:
            db.set_messages_per_day(site['id'], messages_per_day)
            db.replace_slots(site['id'], slots)
            scheduler.reload()
            logger.info(f"Schedule of {site['code']} saved: {len(slots)} slots, {messages_per_day} messages a day")
            return redirect(url_for('schedules', message='Schedule saved'))
    slots = db.get_slots(site['id'])
    return render_template("schedules.html", title="Schedules", site=site, form=form, error=error,
                           summary=schedule_summary(site, slots), message=request.args.get('message'),
                           queued=db.count_messages_by_status(site['id'])['queued']), (400 if error else 200)


# Sites

DEFAULT_SLOTS = [(day, hour, 0) for day in range(1, 8) for hour in range(8, 23)]


def form_to_site(form, site=None):
    """Values of the site form; ValueError if not valid."""
    values = {
        'code': site['code'] if site else form.get('code', '').strip().lower(),
        'name': form.get('name', '').strip(),
        'url': form.get('url', '').strip().rstrip('/'),
        'allowed_origins': '\n'.join(line.strip().rstrip('/') for line in form.get('allowed_origins', '').splitlines()
                                     if line.strip()) or None,
        'enabled': form.get('enabled') == 'on',
        'vapid_sub': form.get('vapid_sub', '').strip(),
        'messages_per_day': form.get('messages_per_day', type=int),
        'ttl_seconds': (form.get('ttl_hours', type=int) or 0) * 3600,
        'default_icon': form.get('default_icon', '').strip() or None,
        'default_badge': form.get('default_badge', '').strip() or None,
    }
    if not re.match(SITE_CODE_PATTERN, values['code']):
        raise ValueError("Code: 2-50 characters, lowercase letters, numbers and dash (it is in the API urls)")
    if not site and db.get_site(code=values['code']):
        raise ValueError(f"There is already a site with code {values['code']}")
    if not 1 <= len(values['name']) <= 100:
        raise ValueError("The name is required (max 100 characters)")
    for name, label in (('url', 'Url'), ('default_icon', 'Default icon'), ('default_badge', 'Default badge')):
        if values[name] and (not values[name].startswith(('https://', 'http://')) or len(values[name]) > 1000):
            raise ValueError(f"{label}: an http(s) url")
    if not values['url']:
        raise ValueError("The url of the site is required")
    for origin in (values['allowed_origins'] or '').splitlines():
        parsed = urlparse(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path:
            raise ValueError(f"Allowed origins: '{origin}' is not an origin (scheme://host[:port])")
    if not re.match(r"^(mailto:[^@\s]+@[^@\s]+|https://\S+)$", values['vapid_sub']):
        raise ValueError("Contact: mailto:address or https url (the push services use it to contact you)")
    if not values['messages_per_day'] or not 1 <= values['messages_per_day'] <= 24:
        raise ValueError("Messages per subscriber a day: from 1 to 24")
    if not 3600 <= values['ttl_seconds'] <= 28 * 24 * 3600:
        raise ValueError("Time to live: from 1 to 672 hours (28 days)")
    return values


@app.route("/sites", methods=["GET"])
@jwt_required()
def sites():
    return render_template("sites.html", title="Sites", sites=db.get_sites_summary(), message=request.args.get('message'))


@app.route("/sites/new", methods=["GET", "POST"])
@jwt_required()
def site_new():
    form = {'enabled': True, 'messages_per_day': 3, 'ttl_hours': 24, 'generate_keys': True, 'default_schedule': True}
    error = None
    if request.method == 'POST':
        form = request.form.to_dict()
        try:
            values = form_to_site(request.form)
        except ValueError as e:
            error = str(e)
        else:
            site_id = db.create_site(values)
            if request.form.get('generate_keys') == 'on':
                db.set_site_keys(site_id, *vapid.generate())
            if request.form.get('default_schedule') == 'on':
                db.replace_slots(site_id, DEFAULT_SLOTS)
            scheduler.reload()
            logger.info(f"Site {values['code']} created by {current_user()['username']}")
            response = redirect(url_for('site_edit', site_id=site_id, message='Site created: add the code of the tab Integration to the site'))
            response.set_cookie(SITE_COOKIE, values['code'], max_age=365 * 24 * 3600, samesite='Lax', httponly=True)
            return response
    return render_template("site_form.html", title="Sites", site=None, form=form, error=error), (400 if error else 200)


def site_page(site, form=None, error=None, key_error=None):
    form = form or dict(site, ttl_hours=site['ttl_seconds'] // 3600, allowed_origins=site['allowed_origins'] or '',
                        default_icon=site['default_icon'] or '', default_badge=site['default_badge'] or '')
    return render_template("site_form.html", title="Sites", site=site, form=form, error=error, key_error=key_error,
                           public_url=settings.PUBLIC_URL, origins=sorted(api.site_origins(site)),
                           test_subscribers=db.get_test_subscribers(site['id']),
                           message=request.args.get('message')), (400 if error or key_error else 200)


@app.route("/sites/<int:site_id>", methods=["GET", "POST"])
@jwt_required()
def site_edit(site_id):
    site = db.get_site(site_id)
    if site is None:
        return render_template('not_found.html', title='Not found'), 404
    if request.method == 'GET':
        return site_page(site)
    try:
        values = form_to_site(request.form, site)
    except ValueError as e:
        return site_page(site, request.form.to_dict(), str(e))
    db.update_site(site_id, values)
    scheduler.reload()
    logger.info(f"Site {site['code']} saved by {current_user()['username']}")
    return redirect(url_for('site_edit', site_id=site_id, message='Site saved'))


@app.route("/sites/<int:site_id>/keys", methods=["POST"])
@jwt_required()
def site_keys(site_id):
    site = db.get_site(site_id)
    if site is None:
        return render_template('not_found.html', title='Not found'), 404
    if request.form.get('action') == 'generate':
        private_key, public_key = vapid.generate()
        message = 'New VAPID keys generated: the browsers subscribed with the old keys must subscribe again'
    else:
        try:
            private_key, public_key = vapid.check(request.form.get('private_key'), request.form.get('public_key'))
        except vapid.InvalidKeys as e:
            return site_page(site, key_error=str(e))
        message = 'VAPID keys imported'
    db.set_site_keys(site_id, private_key, public_key)
    logger.info(f"VAPID keys of {site['code']} changed by {current_user()['username']} ({request.form.get('action')})")
    return redirect(url_for('site_edit', site_id=site_id, message=message) + '#keys')


@app.route("/sites/<int:site_id>/test-subscription", methods=["POST"])
@jwt_required()
def site_test_subscription(site_id):
    """This browser as test browser of the site (button of the tab Integration, JSON with the X-CSRF-TOKEN header)."""
    site = db.get_site(site_id)
    if site is None:
        return api.error(404, 'Unknown site')
    try:
        endpoint, p256dh, auth, expiration = api.parse_subscription(request.get_json(silent=True) or {})
    except ValueError as e:
        return api.error(400, str(e))
    db.upsert_subscriber(site_id, endpoint, p256dh, auth, expiration,
                         (request.headers.get('User-Agent') or '')[:255] or None, is_test=True)
    logger.info(f"Test browser added to {site['code']} by {current_user()['username']}")
    return jsonify({'subscribed': True}), 201


@app.route("/sites/<int:site_id>/test-subscribers/<int:subscriber_id>/remove", methods=["POST"])
@jwt_required()
def site_test_subscriber_remove(site_id, subscriber_id):
    db.remove_subscriber(subscriber_id)
    return redirect(url_for('site_edit', site_id=site_id) + '#integration')


@app.route("/sites/<int:site_id>/test-page/", methods=["GET"])
@jwt_required()
def site_test_page(site_id):
    """A page of the console that plays the site: the integration code (webpush-client.js, webpush-sw.js, public API)
    as on the real site, to subscribe this browser and receive the messages of the site."""
    site = db.get_site(site_id)
    if site is None:
        return render_template('not_found.html', title='Not found'), 404
    public_origin = notifier.origin(settings.PUBLIC_URL)
    return render_template("site_test_page.html", title="Sites", site=site, api_url=url_for('api.ping', _external=True).rsplit('/ping', 1)[0],
                           public_url=settings.PUBLIC_URL, wrong_origin=request.host_url.rstrip('/') != public_origin)


def iso_or_none(value):
    return value.strftime('%d/%m/%Y %H:%M:%S') if value else None


@app.route("/sites/<int:site_id>/test-page/inspect", methods=["POST"])
@jwt_required()
def site_test_page_inspect(site_id):
    """Debug of the test page: what the server knows of the subscription of this browser (JSON {endpoint})."""
    site = db.get_site(site_id)
    endpoint = (request.get_json(silent=True) or {}).get('endpoint')
    if site is None or not isinstance(endpoint, str):
        return api.error(400, 'endpoint is required')
    subscriber = db.get_subscriber_by_endpoint(site_id, endpoint)
    if subscriber is None:
        return jsonify({'found': False})
    return jsonify({
        'found': True, 'id': subscriber['id'], 'active': subscriber['removed_at'] is None,
        'is_test': bool(subscriber['is_test']), 'created_at': iso_or_none(subscriber['created_at']),
        'updated_at': iso_or_none(subscriber['updated_at']), 'last_success_at': iso_or_none(subscriber['last_success_at']),
        'failures': subscriber['failures'], 'last_error': subscriber['last_error'],
        'removed_at': iso_or_none(subscriber['removed_at']), 'removed_reason': subscriber['removed_reason'],
        'keys_match_server': (request.get_json(silent=True) or {}).get('public_key') == site['vapid_public_key'],
    })


@app.route("/sites/<int:site_id>/test-page/send", methods=["POST"])
@jwt_required()
def site_test_page_send(site_id):
    """Debug of the test page: a test notification to this browser only, with the answer of the push service."""
    site = db.get_site(site_id)
    endpoint = (request.get_json(silent=True) or {}).get('endpoint')
    if site is None or not isinstance(endpoint, str):
        return api.error(400, 'endpoint is required')
    if not site['vapid_private_key']:
        return jsonify({'ok': False, 'error': 'The site has no VAPID keys', 'hint': 'Sites > the site > Keys > Generate'})
    subscriber = db.get_subscriber_by_endpoint(site_id, endpoint)
    if subscriber is None:
        return jsonify({'ok': False, 'error': 'This browser is not subscribed on the server',
                        'hint': 'Enable the notifications (or disable and enable them again): the subscription was not saved.'})
    result = notifier.debug_send(site, subscriber)
    logger.info(f"Test page of {site['code']}: test notification to subscriber {subscriber['id']}: "
                f"{result.get('status')} {result.get('error') or ''}")
    return jsonify(result)


@app.route("/sites/<int:site_id>/test-page/webpush-sw.js", methods=["GET"])
def site_test_page_service_worker(site_id):
    """The service worker of the test page (its scope: the test page)."""
    response = send_from_directory(app.static_folder, 'webpush-sw.js', max_age=0)
    response.headers['Content-Type'] = 'application/javascript'
    return response


@app.route("/sw/<code>/webpush-sw.js", methods=["GET"])
def test_service_worker(code):
    """The service worker of the test browsers of a site, in the console (its scope: /sw/<code>/)."""
    response = send_from_directory(app.static_folder, 'webpush-sw.js', max_age=0)
    response.headers['Content-Type'] = 'application/javascript'
    return response


# API tokens

TOKEN_EXPIRATIONS = {'': 'Never', '30': '30 days', '90': '90 days', '365': '1 year'}


@app.route("/api-tokens", methods=["GET", "POST"])
@jwt_required()
def api_tokens():
    new_token, error = None, None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        expiration = request.form.get('expires', '')
        site_id = request.form.get('site_id', type=int) or None
        site = db.get_site(site_id) if site_id else None
        if not 1 <= len(name) <= 100:
            error = 'The name of the client is required (max 100 characters)'
        elif expiration not in TOKEN_EXPIRATIONS:
            error = 'Unknown expiration'
        elif site_id and site is None:
            error = 'Unknown site'
        else:
            token, token_hash, prefix = api.generate_token()
            expires_at = datetime.now() + timedelta(days=int(expiration)) if expiration else None
            db.create_api_token(name, token_hash, prefix, int(get_jwt_identity()), site_id, expires_at)
            logger.info(f"API token created for '{name}' ({prefix}...), site: {site['code'] if site else 'all'}, expires: {expires_at}")
            # shown only in this response: it can't be read again
            new_token = {'name': name, 'token': token, 'expires_at': expires_at, 'site': site}
    return render_template("api_tokens.html", title="API tokens", tokens=db.get_api_tokens(), new_token=new_token,
                           error=error, expirations=TOKEN_EXPIRATIONS, now=datetime.now(), sites=db.get_sites(),
                           api_url=f"{settings.PUBLIC_URL}/api/v1"), (400 if error else 200)


@app.route("/api-tokens/<int:token_id>/revoke", methods=["POST"])
@jwt_required()
def api_token_revoke(token_id):
    db.revoke_api_token(token_id)
    logger.info(f"API token {token_id} revoked")
    return redirect(url_for('api_tokens'))


# IP blacklist

@app.route("/ip-blacklist", methods=["GET"])
@jwt_required()
def ip_blacklist():
    now = datetime.now()
    ips = db.get_ip_blacklist_all()
    for ip in ips:
        ip['status'], ip['until'] = util.ip_status(ip, now)
    return render_template("ip_blacklist.html", title="IP blacklist", ips=ips,
                           my_ip=util.normalize_ip(request.remote_addr), error=request.args.get('error'),
                           saved=request.args.get('saved'), settings=settings.WEB_SETTINGS)


def ip_from_form():
    value = request.form.get('remote_address', '').strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValueError(f"'{value}' is not a valid IPv4 or IPv6 address")


@app.route("/ip-blacklist/save", methods=["POST"])
@jwt_required()
def ip_blacklist_save():
    try:
        remote_address = ip_from_form()
    except ValueError as e:
        return redirect(url_for('ip_blacklist', error=str(e)))
    blocked = request.form.get('blocked') == 'on'
    if blocked and remote_address == util.normalize_ip(request.remote_addr):
        # blocking the own ip would lock the administrator out of the console
        return redirect(url_for('ip_blacklist', error=f"{remote_address} is your ip: you can't block it"))
    note = request.form.get('note', '').strip()[:255] or None
    db.save_ip_blacklist(remote_address, blocked, note, reset_requests=request.form.get('reset') == 'on')
    logger.info(f"Ip blacklist: {remote_address} saved, blocked: {blocked}, note: {note}")
    return redirect(url_for('ip_blacklist', saved=remote_address))


@app.route("/ip-blacklist/delete", methods=["POST"])
@jwt_required()
def ip_blacklist_delete():
    try:
        remote_address = ip_from_form()
    except ValueError as e:
        return redirect(url_for('ip_blacklist', error=str(e)))
    db.delete_ip_blacklist(remote_address)
    logger.info(f"Ip blacklist: {remote_address} removed")
    return redirect(url_for('ip_blacklist'))


# Errors

ERROR_STATUSES = {'open': 'Open', 'resolved': 'Resolved', 'all': 'All'}


def error_filters(source):
    """Filters of the page Errors from the query string or from a form (bulk actions)."""
    message = source.get('message_id', '')
    return {
        'phase': source.get('phase') if source.get('phase') in errorlog.PHASES else '',
        'status': source.get('status') if source.get('status') in ERROR_STATUSES else 'open',
        'site_id': site_id_filter(),
        'message': int(message) if message.isdigit() else None,
        'q': source.get('q', '').strip()[:200],
    }


def errors_redirect(filters, **notice):
    args = {k: v for k, v in filters.items()
            if k != 'site_id' and v not in ('', None) and not (k == 'status' and v == 'open')}
    if 'message' in args:
        args['message_id'] = args.pop('message')
    return redirect(url_for('errors', **args, **notice))


@app.route("/errors/", methods=["GET"])
@jwt_required()
def errors():
    filters = error_filters(request.args)
    _, total = db.get_error_logs(filters, 1)
    pages = paginate(total)
    error_logs, _ = db.get_error_logs(filters, pages['records_per_page'], pages['offset'])
    return render_template("errors.html", title="Errors", current_route='errors', records_name='errors',
                           args=list_args('phase', 'status', 'message_id', 'q', 'records'),
                           filters=filters, paginate=pages, errors=error_logs,
                           open_by_phase=db.count_open_errors(site_id_filter()), statuses=ERROR_STATUSES,
                           retention_days=settings.ERROR_LOG['retention_days'], notice=request.args.get('notice'))


@app.route("/errors/<int:error_id>/resolve", methods=["POST"])
@jwt_required()
def error_resolve(error_id):
    db.set_error_logs_resolved(request.form.get('resolved', '1') == '1', error_id=error_id)
    return errors_redirect(error_filters(request.form))


@app.route("/errors/resolve", methods=["POST"])
@jwt_required()
def errors_resolve():
    # all the open errors matching the filters of the page
    filters = dict(error_filters(request.form), status='open')
    count = db.set_error_logs_resolved(True, filters=filters)
    logger.info(f"{count} errors marked as resolved, filters: {filters}")
    return errors_redirect(error_filters(request.form), notice=f"{count} errors marked as resolved")


@app.route("/errors/delete-resolved", methods=["POST"])
@jwt_required()
def errors_delete_resolved():
    count = db.delete_error_logs()
    logger.info(f"{count} resolved errors deleted")
    return errors_redirect(error_filters(request.form), notice=f"{count} resolved errors deleted")


# Start and stop

SHUTDOWN_WAIT_SECONDS = 60  # below the stop timeout of the container (docker-start.sh)
stopping = False


def shutdown(signum, frame):
    """SIGTERM / SIGINT (docker stop): stop the scheduler, wait for the sendings running, exit.
    A second signal exits at once."""
    global stopping
    if stopping:
        logger.warning("Second stop signal: exit without waiting for the sendings")
        sys.exit(1)
    stopping = True
    logger.info(f"Application stop ({signal.Signals(signum).name})")
    if not scheduler.stop(SHUTDOWN_WAIT_SECONDS):
        logger.warning(f"Sendings still running after {SHUTDOWN_WAIT_SECONDS} seconds: they end as failed at the next start")
    logger.info('Application stopped')
    sys.exit(0)


if __name__ == '__main__':
    errorlog.install()
    logger.info('Application start')

    # sendings left open by a previous stop are no longer running
    interrupted = db.interrupt_sending_messages()
    if interrupted:
        logger.warning(f"{interrupted} messages were sending when the application stopped: marked as failed")

    scheduler.start()

    # docker stop sends SIGTERM to the application (PID 1 of the container): without a handler it is ignored
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if settings.WEB_SETTINGS['debug']:
        app.run(host=settings.WEB_SETTINGS['host'], port=settings.WEB_SETTINGS['port'], debug=True,
                use_reloader=settings.WEB_SETTINGS['use_reloader'])
    else:
        # Production server: single process (the scheduler must run once)
        from waitress import serve
        serve(app, host=settings.WEB_SETTINGS['host'], port=settings.WEB_SETTINGS['port'],
              threads=settings.WEB_SETTINGS['threads'])
