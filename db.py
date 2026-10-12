# Queries of webpush on MariaDB (tables: sql/create_tables.sql).
# Every function takes a connection of the pool (dbpool.py) with cursor(): one transaction, committed at the end of
# the block when commit=True, rolled back on error.
# Times are always local time of the application (datetime.now(), TZ of the container), never NOW() or the defaults
# of MariaDB: the server of the database may have another time zone.
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta

from dbpool import ConnectionPool
from settings import DB_SETTINGS, DB_POOL

# Pool of the process: the connections are opened when needed (see dbpool.py)
connection_pool = ConnectionPool(DB_SETTINGS, DB_POOL['size'], DB_POOL['timeout'], DB_POOL['recycle'])

MESSAGE_STATUSES = ('queued', 'sending', 'sent', 'failed', 'cancelled')


@contextmanager
def cursor(commit=False):
    """A cursor returning dicts on a connection of the pool; the connection goes back to the pool at the end."""
    connection = connection_pool.get_connection()
    db_cursor = connection.cursor(dictionary=True)
    try:
        yield db_cursor
        if commit:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        db_cursor.close()
        connection.close()


def in_clause(values):
    return ', '.join(['%s'] * len(values))


def endpoint_hash(endpoint):
    return hashlib.sha256(endpoint.encode('utf-8')).hexdigest()


def site_where(site_id, column='site_id'):
    """Condition on the site, or none for all the sites (site_id None)."""
    return (f" AND {column} = %s", (site_id,)) if site_id else ("", ())


# Users of the console

def get_user(username=None, user_id=None):
    with cursor() as c:
        if user_id is not None:
            c.execute("SELECT id, username, hash FROM user WHERE id = %s", (user_id,))
        else:
            c.execute("SELECT id, username, hash FROM user WHERE username = %s", (username,))
        return c.fetchone()


def count_users():
    with cursor() as c:
        c.execute("SELECT COUNT(*) AS n FROM user")
        return c.fetchone()['n']


def create_first_user(username, password_hash):
    """Create the administrator only while the user table is empty; returns the new id or None."""
    with cursor(commit=True) as c:
        # single statement: two concurrent setups cannot both create a user
        c.execute("INSERT INTO user (username, hash) SELECT %s, %s FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM user)",
                  (username, password_hash))
        return c.lastrowid if c.rowcount == 1 else None


def update_user_password(user_id, password_hash):
    with cursor(commit=True) as c:
        c.execute("UPDATE user SET hash = %s WHERE id = %s", (password_hash, user_id))


# Sites

SITE_COLUMNS = ('code', 'name', 'url', 'allowed_origins', 'enabled', 'vapid_sub', 'messages_per_day', 'ttl_seconds',
                'default_icon', 'default_badge')


def get_sites(enabled_only=False):
    with cursor() as c:
        c.execute("SELECT * FROM site" + (" WHERE enabled = true" if enabled_only else "") + " ORDER BY name")
        return c.fetchall()


def get_site(site_id=None, code=None):
    with cursor() as c:
        if site_id is not None:
            c.execute("SELECT * FROM site WHERE id = %s", (site_id,))
        else:
            c.execute("SELECT * FROM site WHERE code = %s", (code,))
        return c.fetchone()


def create_site(values):
    with cursor(commit=True) as c:
        c.execute(f"INSERT INTO site ({', '.join(SITE_COLUMNS)}, created_at) VALUES ({in_clause(SITE_COLUMNS)}, %s)",
                  tuple(values[k] for k in SITE_COLUMNS) + (datetime.now(),))
        return c.lastrowid


def update_site(site_id, values):
    with cursor(commit=True) as c:
        c.execute(f"UPDATE site SET {', '.join(k + ' = %s' for k in SITE_COLUMNS)} WHERE id = %s",
                  tuple(values[k] for k in SITE_COLUMNS) + (site_id,))


def set_messages_per_day(site_id, messages_per_day):
    with cursor(commit=True) as c:
        c.execute("UPDATE site SET messages_per_day = %s WHERE id = %s", (messages_per_day, site_id))


def set_site_keys(site_id, private_key, public_key):
    with cursor(commit=True) as c:
        c.execute("UPDATE site SET vapid_private_key = %s, vapid_public_key = %s WHERE id = %s",
                  (private_key, public_key, site_id))


# Subscribers

ACTIVE = "removed_at IS NULL AND is_test = false"


def upsert_subscriber(site_id, endpoint, p256dh, auth, expiration_time=None, user_agent=None, is_test=False):
    """Add a subscriber, or update the keys of the same endpoint (active again if removed).
    Returns True if it is new (or active again)."""
    with cursor(commit=True) as c:
        h = endpoint_hash(endpoint)
        c.execute("SELECT id, removed_at FROM subscriber WHERE site_id = %s AND endpoint_hash = %s FOR UPDATE",
                  (site_id, h))
        row = c.fetchone()
        if row is None:
            c.execute("INSERT INTO subscriber (site_id, endpoint, endpoint_hash, keys_p256dh, keys_auth, expiration_time,"
                      " user_agent, is_test, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                      (site_id, endpoint, h, p256dh, auth, expiration_time, user_agent, is_test, datetime.now()))
            return True
        c.execute("UPDATE subscriber SET keys_p256dh = %s, keys_auth = %s, expiration_time = %s, user_agent = %s,"
                  " is_test = %s, updated_at = %s, failures = 0, removed_at = NULL, removed_reason = NULL"
                  " WHERE id = %s", (p256dh, auth, expiration_time, user_agent, is_test, datetime.now(), row['id']))
        return row['removed_at'] is not None


def unsubscribe(site_id, endpoint):
    with cursor(commit=True) as c:
        c.execute("UPDATE subscriber SET removed_at = %s, removed_reason = 'unsubscribed'"
                  " WHERE site_id = %s AND endpoint_hash = %s AND removed_at IS NULL",
                  (datetime.now(), site_id, endpoint_hash(endpoint)))
        return c.rowcount > 0


def count_active_subscribers(site_id):
    with cursor() as c:
        c.execute(f"SELECT COUNT(*) AS n FROM subscriber WHERE site_id = %s AND {ACTIVE}", (site_id,))
        return c.fetchone()['n']


def get_subscribers_partition(site_id, partitions, partition_index):
    """The active subscribers of a partition: MOD of the id, stable when subscribers are added or removed."""
    with cursor() as c:
        c.execute("SELECT id, endpoint, keys_p256dh, keys_auth, expiration_time FROM subscriber"
                  f" WHERE site_id = %s AND {ACTIVE} AND MOD(id, %s) = %s ORDER BY id",
                  (site_id, partitions, partition_index))
        return c.fetchall()


def get_test_subscribers(site_id):
    with cursor() as c:
        c.execute("SELECT id, endpoint, keys_p256dh, keys_auth, expiration_time, user_agent, created_at,"
                  " last_success_at FROM subscriber WHERE site_id = %s AND is_test = true AND removed_at IS NULL"
                  " ORDER BY id", (site_id,))
        return c.fetchall()


def chunks(ids, size=1000):
    ids = list(ids)
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def save_sending_results(success_ids, failed, removed, max_failures):
    """Outcome of a sending: success resets the failures, a failure counts (removed after max_failures in a row),
    failed: {id: error}, removed: {reason: {id: error}} removed at once (gone, expired)."""
    now = datetime.now()
    with cursor(commit=True) as c:
        for ids in chunks(success_ids):
            c.execute("UPDATE subscriber SET last_success_at = %s, failures = 0, last_error = NULL"
                      f" WHERE id IN ({in_clause(ids)})", (now,) + tuple(ids))
        if failed:
            c.executemany("UPDATE subscriber SET failures = failures + 1, last_error = %s WHERE id = %s",
                          [(error[:500], subscriber_id) for subscriber_id, error in failed.items()])
            for ids in chunks(failed):
                c.execute("UPDATE subscriber SET removed_at = %s, removed_reason = 'failures'"
                          f" WHERE id IN ({in_clause(ids)}) AND failures >= %s AND removed_at IS NULL",
                          (now,) + tuple(ids) + (max_failures,))
        for reason, errors in removed.items():
            if errors:
                c.executemany("UPDATE subscriber SET removed_at = %s, removed_reason = %s, last_error = %s"
                              " WHERE id = %s AND removed_at IS NULL",
                              [(now, reason, error[:500], subscriber_id) for subscriber_id, error in errors.items()])


def get_subscriber_by_endpoint(site_id, endpoint):
    with cursor() as c:
        c.execute("SELECT id, site_id, endpoint, keys_p256dh, keys_auth, expiration_time, user_agent, is_test, created_at,"
                  " updated_at, last_success_at, failures, last_error, removed_at, removed_reason FROM subscriber"
                  " WHERE site_id = %s AND endpoint_hash = %s", (site_id, endpoint_hash(endpoint)))
        return c.fetchone()


def remove_subscriber(subscriber_id, reason='manual'):
    with cursor(commit=True) as c:
        c.execute("UPDATE subscriber SET removed_at = %s, removed_reason = %s WHERE id = %s AND removed_at IS NULL",
                  (datetime.now(), reason, subscriber_id))


SUBSCRIBER_STATUSES = {'active': 'Active', 'removed': 'Removed', 'test': 'Test browsers', 'all': 'All'}

# host of the endpoint -> push service (browser)
PUSH_SERVICES = (('fcm.googleapis.com', 'Google FCM (Chrome, Edge, Opera, Android)'),
                 ('push.services.mozilla.com', 'Mozilla (Firefox)'),
                 ('push.apple.com', 'Apple (Safari)'),
                 ('notify.windows.com', 'Microsoft WNS (Edge legacy)'))


def subscribers_where(filters):
    conditions, params = ["1 = 1"], []
    if filters.get('site_id'):
        conditions.append("s.site_id = %s")
        params.append(filters['site_id'])
    status = filters.get('status', 'active')
    if status == 'active':
        conditions.append("s.removed_at IS NULL AND s.is_test = false")
    elif status == 'removed':
        conditions.append("s.removed_at IS NOT NULL")
    elif status == 'test':
        conditions.append("s.is_test = true AND s.removed_at IS NULL")
    if filters.get('service'):
        conditions.append("s.endpoint LIKE %s")
        params.append(f"https://%{filters['service']}/%")
    return " WHERE " + " AND ".join(conditions), tuple(params)


def get_subscribers(filters, limit, offset=0):
    with cursor() as c:
        where, params = subscribers_where(filters)
        c.execute(f"SELECT COUNT(*) AS n FROM subscriber s{where}", params)
        total = c.fetchone()['n']
        c.execute("SELECT s.id, s.site_id, t.name AS site_name, s.endpoint, s.user_agent, s.is_test, s.created_at,"
                  " s.updated_at, s.last_success_at, s.failures, s.last_error, s.removed_at, s.removed_reason"
                  f" FROM subscriber s JOIN site t ON t.id = s.site_id{where}"
                  " ORDER BY s.id DESC LIMIT %s OFFSET %s", params + (limit, offset))
        return c.fetchall(), total


def push_service_of(endpoint):
    host = (endpoint or '').split('/')[2] if (endpoint or '').count('/') >= 2 else ''
    for suffix, label in PUSH_SERVICES:
        if host.endswith(suffix):
            return suffix, label
    return host, host or 'Unknown'


def get_push_services(site_id=None):
    """Active subscribers by push service: [(key, label, count)], most used first."""
    where, params = site_where(site_id)
    with cursor() as c:
        c.execute("SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(endpoint, '/', 3), '/', -1) AS host, COUNT(*) AS n"
                  f" FROM subscriber WHERE {ACTIVE}{where} GROUP BY host", params)
        services = {}
        for row in c.fetchall():
            key, label = push_service_of(f"https://{row['host']}/")
            services.setdefault(key, [key, label, 0])[2] += row['n']
    return sorted((tuple(s) for s in services.values()), key=lambda s: -s[2])


def get_subscriber_growth(site_id=None, days=30):
    """New and removed subscribers per day of the last days, newest first, with the active ones at the end of the day."""
    where, params = site_where(site_id)
    since = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    with cursor() as c:
        c.execute(f"SELECT DATE(created_at) AS d, COUNT(*) AS n FROM subscriber WHERE is_test = false{where}"
                  " AND created_at >= %s GROUP BY d", params + (since,))
        added = {row['d']: row['n'] for row in c.fetchall()}
        c.execute(f"SELECT DATE(removed_at) AS d, COUNT(*) AS n FROM subscriber WHERE is_test = false{where}"
                  " AND removed_at >= %s GROUP BY d", params + (since,))
        removed = {row['d']: row['n'] for row in c.fetchall()}
        c.execute(f"SELECT COUNT(*) AS n FROM subscriber WHERE {ACTIVE}{where}", params)
        active = c.fetchone()['n']
    growth = []
    day = datetime.now().date()
    for _ in range(days):
        growth.append({'day': day, 'added': added.get(day, 0), 'removed': removed.get(day, 0), 'active': active})
        # active at the end of the day before
        active -= added.get(day, 0) - removed.get(day, 0)
        day -= timedelta(days=1)
    return growth


# Messages

def add_message(site_id, title, options, send_after=None, created_by=None):
    with cursor(commit=True) as c:
        c.execute("INSERT INTO message (site_id, title, options, send_after, created_by, created_at)"
                  " VALUES (%s, %s, %s, %s, %s, %s)", (site_id, title, options, send_after, created_by, datetime.now()))
        message_id = c.lastrowid
        # last in the queue
        c.execute("UPDATE message SET sort_order = id WHERE id = %s", (message_id,))
        return message_id


def update_message(message_id, title, options, send_after):
    """Change a message still in the queue; False if it is no longer queued."""
    with cursor(commit=True) as c:
        c.execute("UPDATE message SET title = %s, options = %s, send_after = %s WHERE id = %s AND status = 'queued'",
                  (title, options, send_after, message_id))
        return c.rowcount == 1


MESSAGE_SELECT = ("SELECT m.*, t.name AS site_name, t.code AS site_code,"
                  " (SELECT COALESCE(SUM(d.total), 0) FROM delivery d WHERE d.message_id = m.id AND d.kind != 'test') AS total,"
                  " (SELECT COALESCE(SUM(d.success), 0) FROM delivery d WHERE d.message_id = m.id AND d.kind != 'test') AS success,"
                  " (SELECT COUNT(*) FROM click k WHERE k.message_id = m.id) AS clicks"
                  " FROM message m JOIN site t ON t.id = m.site_id")


def get_message(message_id):
    with cursor() as c:
        c.execute(f"{MESSAGE_SELECT} WHERE m.id = %s", (message_id,))
        return c.fetchone()


def messages_where(filters):
    conditions, params = [], []
    if filters.get('site_id'):
        conditions.append("m.site_id = %s")
        params.append(filters['site_id'])
    if filters.get('status'):
        conditions.append("m.status = %s")
        params.append(filters['status'])
    if filters.get('q'):
        conditions.append("(m.title LIKE %s OR m.options LIKE %s)")
        params += [f"%{filters['q']}%"] * 2
    return (" WHERE " + " AND ".join(conditions)) if conditions else "", tuple(params)


def get_messages(filters, limit, offset=0):
    """Messages matching the filters and their total: the queue in its order, the others newest first."""
    order = "m.sort_order, m.id" if filters.get('status') == 'queued' else "COALESCE(m.sent_at, m.created_at) DESC, m.id DESC"
    with cursor() as c:
        where, params = messages_where(filters)
        c.execute(f"SELECT COUNT(*) AS n FROM message m{where}", params)
        total = c.fetchone()['n']
        c.execute(f"{MESSAGE_SELECT}{where} ORDER BY {order} LIMIT %s OFFSET %s", params + (limit, offset))
        return c.fetchall(), total


def count_messages_by_status(site_id=None):
    where, params = site_where(site_id)
    with cursor() as c:
        c.execute(f"SELECT status, COUNT(*) AS n FROM message WHERE 1 = 1{where} GROUP BY status", params)
        counts = {status: 0 for status in MESSAGE_STATUSES}
        counts.update({row['status']: row['n'] for row in c.fetchall()})
        return counts


def get_next_queued(site_id, limit):
    """The next messages of the queue that can be sent now or later, in their order."""
    with cursor() as c:
        c.execute("SELECT id, title, send_after FROM message WHERE site_id = %s AND status = 'queued'"
                  " ORDER BY sort_order, id LIMIT %s", (site_id, limit))
        return c.fetchall()


def claim_next_message(site_id):
    """The first message of the queue that can be sent now, marked as sending; None if there is none."""
    now = datetime.now()
    with cursor(commit=True) as c:
        c.execute("SELECT id FROM message WHERE site_id = %s AND status = 'queued'"
                  " AND (send_after IS NULL OR send_after <= %s) ORDER BY sort_order, id LIMIT 1 FOR UPDATE",
                  (site_id, now))
        row = c.fetchone()
        if row is None:
            return None
        c.execute("UPDATE message SET status = 'sending', started_at = %s WHERE id = %s", (now, row['id']))
    return get_message(row['id'])


def claim_message(message_id):
    """Mark a queued message as sending (Send now); False if it is no longer queued."""
    with cursor(commit=True) as c:
        c.execute("UPDATE message SET status = 'sending', started_at = %s WHERE id = %s AND status = 'queued'",
                  (datetime.now(), message_id))
        return c.rowcount == 1


def finish_message(message_id, status):
    with cursor(commit=True) as c:
        c.execute("UPDATE message SET status = %s, sent_at = %s WHERE id = %s", (status, datetime.now(), message_id))


def cancel_message(message_id):
    with cursor(commit=True) as c:
        c.execute("UPDATE message SET status = 'cancelled' WHERE id = %s AND status = 'queued'", (message_id,))
        return c.rowcount == 1


def interrupt_sending_messages():
    """At the start: the sendings stopped by the previous stop of the application end as failed."""
    with cursor(commit=True) as c:
        c.execute("UPDATE message SET status = 'failed', sent_at = %s WHERE status = 'sending'", (datetime.now(),))
        return c.rowcount


def move_message(message_id, direction):
    """Move a queued message up (direction -1), down (1), to the top (-2) of the queue of its site."""
    with cursor(commit=True) as c:
        c.execute("SELECT id, site_id, sort_order FROM message WHERE id = %s AND status = 'queued' FOR UPDATE",
                  (message_id,))
        message = c.fetchone()
        if message is None:
            return False
        if direction == -2:
            c.execute("SELECT COALESCE(MIN(sort_order), 0) AS first FROM message WHERE site_id = %s AND status = 'queued'",
                      (message['site_id'],))
            c.execute("UPDATE message SET sort_order = %s WHERE id = %s", (c.fetchone()['first'] - 1, message_id))
            return True
        compare, order = ('<', 'DESC') if direction < 0 else ('>', 'ASC')
        c.execute(f"SELECT id, sort_order FROM message WHERE site_id = %s AND status = 'queued'"
                  f" AND (sort_order, id) {compare} (%s, %s) ORDER BY sort_order {order}, id {order} LIMIT 1 FOR UPDATE",
                  (message['site_id'], message['sort_order'], message['id']))
        other = c.fetchone()
        if other is None:
            return False
        if other['sort_order'] == message['sort_order']:
            # same position (old rows): spread them
            other['sort_order'] += -1 if direction < 0 else 1
        c.execute("UPDATE message SET sort_order = %s WHERE id = %s", (other['sort_order'], message['id']))
        c.execute("UPDATE message SET sort_order = %s WHERE id = %s", (message['sort_order'], other['id']))
        return True


# Schedule slots

def get_slots(site_id=None):
    where, params = site_where(site_id)
    with cursor() as c:
        c.execute(f"SELECT site_id, day, hour, minute FROM schedule_slot WHERE 1 = 1{where}"
                  " ORDER BY site_id, day, hour, minute", params)
        return c.fetchall()


def replace_slots(site_id, slots):
    """The slots of a site: [(day, hour, minute)]."""
    with cursor(commit=True) as c:
        c.execute("DELETE FROM schedule_slot WHERE site_id = %s", (site_id,))
        if slots:
            c.executemany("INSERT INTO schedule_slot (site_id, day, hour, minute) VALUES (%s, %s, %s, %s)",
                          [(site_id,) + tuple(slot) for slot in sorted(set(slots))])


# Deliveries

def start_delivery(site_id, message_id, kind, slot, partition_index, partitions, total):
    with cursor(commit=True) as c:
        c.execute("INSERT INTO delivery (site_id, message_id, kind, slot, partition_index, partitions, total, start_time)"
                  " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                  (site_id, message_id, kind, slot, partition_index, partitions, total, datetime.now()))
        return c.lastrowid


def finish_delivery(delivery_id, success, gone, errors):
    with cursor(commit=True) as c:
        c.execute("UPDATE delivery SET success = %s, gone = %s, errors = %s, end_time = %s WHERE id = %s",
                  (success, gone, errors, datetime.now(), delivery_id))


DELIVERY_SELECT = ("SELECT d.*, t.name AS site_name, m.title FROM delivery d JOIN site t ON t.id = d.site_id"
                   " LEFT JOIN message m ON m.id = d.message_id")


def get_deliveries(site_id=None, message_id=None, since=None, limit=20):
    conditions, params = [], []
    if site_id:
        conditions.append("d.site_id = %s")
        params.append(site_id)
    if message_id:
        conditions.append("d.message_id = %s")
        params.append(message_id)
    if since:
        conditions.append("d.start_time >= %s")
        params.append(since)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with cursor() as c:
        c.execute(f"{DELIVERY_SELECT}{where} ORDER BY d.start_time DESC, d.id DESC LIMIT %s", tuple(params) + (limit,))
        return c.fetchall()


# Clicks

def add_click(site_id, message_id):
    with cursor(commit=True) as c:
        # only the messages of the site: a wrong id is counted without message
        c.execute("INSERT INTO click (site_id, message_id, created_at)"
                  " SELECT %s, (SELECT id FROM message WHERE id = %s AND site_id = %s), %s",
                  (site_id, message_id, site_id, datetime.now()))


# Statistics of the Home

def get_stats(site_id=None):
    """Counters of the Home for a site or all the sites."""
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    where, params = site_where(site_id)
    with cursor() as c:
        c.execute(f"SELECT COUNT(*) AS active,"
                  f" COALESCE(SUM(created_at >= %s), 0) AS new_today, COALESCE(SUM(created_at >= %s), 0) AS new_week"
                  f" FROM subscriber WHERE {ACTIVE}{where}", (today, week) + params)
        stats = c.fetchone()
        c.execute(f"SELECT COUNT(*) AS n FROM subscriber WHERE is_test = false AND removed_at >= %s{where}",
                  (week,) + params)
        stats['removed_week'] = c.fetchone()['n']
        c.execute(f"SELECT COUNT(*) AS n FROM message WHERE status = 'queued'{where}", params)
        stats['queued'] = c.fetchone()['n']
        c.execute(f"SELECT COUNT(DISTINCT message_id) AS n FROM delivery WHERE kind != 'test' AND start_time >= %s{where}",
                  (today,) + params)
        stats['sent_today'] = c.fetchone()['n']
        c.execute(f"SELECT COALESCE(SUM(total), 0) AS total, COALESCE(SUM(success), 0) AS success,"
                  f" COALESCE(SUM(gone), 0) AS gone FROM delivery WHERE kind != 'test' AND start_time >= %s{where}",
                  (week,) + params)
        stats.update(c.fetchone())
        c.execute(f"SELECT COUNT(*) AS n FROM click WHERE created_at >= %s{where}", (week,) + params)
        stats['clicks_week'] = c.fetchone()['n']
    stats = {k: int(v) for k, v in stats.items()}
    stats['delivery_rate'] = stats['success'] / stats['total'] if stats['total'] else None
    stats['ctr'] = stats['clicks_week'] / stats['success'] if stats['success'] else None
    return stats


def get_sites_summary():
    """One row per site for the Home with all the sites."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with cursor() as c:
        c.execute("SELECT t.id, t.code, t.name, t.enabled, t.vapid_public_key IS NOT NULL AS has_keys,"
                  " (SELECT COUNT(*) FROM subscriber s WHERE s.site_id = t.id AND s.removed_at IS NULL"
                  "  AND s.is_test = false) AS subscribers,"
                  " (SELECT COUNT(*) FROM message m WHERE m.site_id = t.id AND m.status = 'queued') AS queued,"
                  " (SELECT COUNT(DISTINCT d.message_id) FROM delivery d WHERE d.site_id = t.id AND d.kind != 'test'"
                  "  AND d.start_time >= %s) AS sent_today,"
                  " (SELECT COUNT(*) FROM schedule_slot l WHERE l.site_id = t.id) AS slots"
                  " FROM site t ORDER BY t.name", (today,))
        return c.fetchall()


# IP blacklist (as in scrapynews)

def get_ip_blacklist(remote_address):
    with cursor() as c:
        c.execute("SELECT remote_address, request_time, number_of_requests, blocked FROM ip_blacklist"
                  " WHERE remote_address = %s", (remote_address,))
        return c.fetchone()


def insert_ip_blacklist(remote_address, request_time: datetime, number_of_requests: int):
    with cursor(commit=True) as c:
        c.execute("INSERT INTO ip_blacklist (remote_address, request_time, number_of_requests, blocked)"
                  " VALUES (%s, %s, %s, false)", (remote_address, request_time, number_of_requests))


def update_ip_blacklist(remote_address: str, request_time: datetime, number_of_requests: int):
    """A new failed request of an ip already in the list (the permanent block is kept)."""
    with cursor(commit=True) as c:
        c.execute("UPDATE ip_blacklist SET request_time = %s, number_of_requests = %s WHERE remote_address = %s",
                  (request_time, number_of_requests, remote_address))


def get_ip_blacklist_all():
    with cursor() as c:
        c.execute("SELECT remote_address, request_time, number_of_requests, blocked, note FROM ip_blacklist"
                  " ORDER BY blocked DESC, request_time DESC")
        return c.fetchall()


def save_ip_blacklist(remote_address, blocked, note, reset_requests=False):
    """Add an ip or change its permanent block and note from the console."""
    with cursor(commit=True) as c:
        c.execute("INSERT INTO ip_blacklist (remote_address, request_time, number_of_requests, blocked, note)"
                  " VALUES (%s, %s, 0, %s, %s)"
                  " ON DUPLICATE KEY UPDATE blocked = VALUES(blocked), note = VALUES(note)"
                  + (", number_of_requests = 0" if reset_requests else ""),
                  (remote_address, datetime.now(), blocked, note))


def delete_ip_blacklist(remote_address):
    with cursor(commit=True) as c:
        c.execute("DELETE FROM ip_blacklist WHERE remote_address = %s", (remote_address,))


def clean_ip_blacklist(limit_time: datetime):
    with cursor(commit=True) as c:
        c.execute("DELETE FROM ip_blacklist WHERE request_time < %s AND blocked = false", (limit_time,))


# API tokens

def create_api_token(name, token_hash, token_prefix, user_id, site_id=None, expires_at=None):
    with cursor(commit=True) as c:
        c.execute("INSERT INTO api_token (name, site_id, token_hash, token_prefix, created_at, created_by, expires_at)"
                  " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (name, site_id, token_hash, token_prefix, datetime.now(), user_id, expires_at))
        return c.lastrowid


def get_api_tokens():
    with cursor() as c:
        c.execute("SELECT t.id, t.name, t.site_id, s.name AS site_name, t.token_prefix AS prefix, t.created_at,"
                  " u.username AS created_by, t.expires_at, t.revoked_at, t.last_used_at, t.last_used_ip"
                  " FROM api_token t LEFT JOIN user u ON u.id = t.created_by LEFT JOIN site s ON s.id = t.site_id"
                  " ORDER BY t.revoked_at IS NOT NULL, t.created_at DESC")
        return c.fetchall()


def revoke_api_token(token_id):
    with cursor(commit=True) as c:
        c.execute("UPDATE api_token SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL", (datetime.now(), token_id))


def use_api_token(token_hash, remote_address):
    """The active (not revoked, not expired) token with this hash, marked as used now; None if not valid."""
    now = datetime.now()
    with cursor(commit=True) as c:
        c.execute("SELECT id, name, site_id FROM api_token WHERE token_hash = %s AND revoked_at IS NULL"
                  " AND (expires_at IS NULL OR expires_at > %s)", (token_hash, now))
        token = c.fetchone()
        if token is not None:
            c.execute("UPDATE api_token SET last_used_at = %s, last_used_ip = %s WHERE id = %s",
                      (now, remote_address, token['id']))
        return token


# Errors (table error_log, page Errors)

def insert_error_log(error):
    with cursor(commit=True) as c:
        c.execute("INSERT INTO error_log (phase, source, site_id, message_id, url, message, traceback, created_at)"
                  " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                  (error['phase'], error['source'], error['site_id'], error['message_id'], error['url'],
                   error['message'], error['traceback'], datetime.now()))


def error_log_where(filters):
    """WHERE of the page Errors: filters phase, status (open | resolved | all), site, message, q (text)."""
    conditions, params = [], []
    if filters.get('phase'):
        conditions.append("e.phase = %s")
        params.append(filters['phase'])
    if filters.get('status', 'open') != 'all':
        conditions.append("e.resolved = %s")
        params.append(filters.get('status', 'open') == 'resolved')
    if filters.get('site_id'):
        conditions.append("e.site_id = %s")
        params.append(filters['site_id'])
    if filters.get('message'):
        conditions.append("e.message_id = %s")
        params.append(filters['message'])
    if filters.get('q'):
        conditions.append("(e.message LIKE %s OR e.url LIKE %s OR e.source LIKE %s)")
        params += [f"%{filters['q']}%"] * 3
    return (" WHERE " + " AND ".join(conditions)) if conditions else "", tuple(params)


def get_error_logs(filters, limit, offset=0):
    """Errors matching the filters, newest first, and their total."""
    with cursor() as c:
        where, params = error_log_where(filters)
        c.execute(f"SELECT COUNT(*) AS n FROM error_log e{where}", params)
        total = c.fetchone()['n']
        c.execute(f"SELECT e.*, s.name AS site_name FROM error_log e LEFT JOIN site s ON s.id = e.site_id{where}"
                  " ORDER BY e.id DESC LIMIT %s OFFSET %s", params + (limit, offset))
        return c.fetchall(), total


def count_open_errors(site_id=None):
    """Open errors by phase, e.g. {'push': 3, 'app': 1}."""
    where, params = site_where(site_id)
    with cursor() as c:
        c.execute(f"SELECT phase, COUNT(*) AS n FROM error_log WHERE resolved = false{where} GROUP BY phase", params)
        return {row['phase']: row['n'] for row in c.fetchall()}


def set_error_logs_resolved(resolved, error_id=None, filters=None):
    """Mark one error, or all the errors matching the filters, as resolved (or open again). Returns the count."""
    with cursor(commit=True) as c:
        if error_id is not None:
            where, params = " WHERE e.id = %s", (error_id,)
        else:
            where, params = error_log_where(filters or {})
        c.execute(f"UPDATE error_log e SET e.resolved = %s{where}", (resolved,) + params)
        return c.rowcount


def delete_error_logs(older_than_days=None):
    """Delete the resolved errors, or all the errors older than the days (retention). Returns the count."""
    with cursor(commit=True) as c:
        if older_than_days is not None:
            c.execute("DELETE FROM error_log WHERE created_at < %s", (datetime.now() - timedelta(days=older_than_days),))
        else:
            c.execute("DELETE FROM error_log WHERE resolved = true")
        return c.rowcount
