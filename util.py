import ipaddress
from datetime import datetime, timedelta

import db
import settings


def normalize_ip(remote_addr: str):
    """Canonical form of an ip (e.g. lowercase compressed IPv6), the same for requests and console."""
    try:
        return str(ipaddress.ip_address(remote_addr.strip()))
    except (ValueError, AttributeError):
        return remote_addr


def ip_status(ip, now=None):
    """State of an ip_blacklist row: 'blocked' (permanent), 'locked' (too many failures, until 'until'), 'watching'."""
    now = now or datetime.now()
    duration = timedelta(minutes=settings.WEB_SETTINGS['failed_login_block_duration'])
    if ip['blocked']:
        return 'blocked', None
    if ip['number_of_requests'] >= settings.WEB_SETTINGS['failed_login_block_threshold'] \
            and ip['request_time'] >= now - duration:
        return 'locked', ip['request_time'] + duration
    return 'watching', ip['request_time'] + duration


def block_ip(remote_addr: str):
    remote_addr = normalize_ip(remote_addr)
    ip = db.get_ip_blacklist(remote_addr)
    if ip is None:
        db.insert_ip_blacklist(remote_addr, datetime.now(), 1)
    else:
        db.update_ip_blacklist(remote_addr, datetime.now(), ip['number_of_requests'] + 1)


def clean_ip_blacklist():
    """Remove the failed requests older than the block duration (permanent blocks are kept)."""
    db.clean_ip_blacklist(datetime.now() - timedelta(minutes=settings.WEB_SETTINGS['failed_login_block_duration']))


def is_blocked_ip(remote_addr):
    threshold = settings.WEB_SETTINGS['failed_login_block_threshold']
    limit_time = datetime.now() - timedelta(minutes=settings.WEB_SETTINGS['failed_login_block_duration'])
    # one SELECT per request: the expired rows are removed by the scheduler (clean_ip_blacklist)
    ip = db.get_ip_blacklist(normalize_ip(remote_addr))
    if ip is not None:
        if ip['blocked'] or (ip['number_of_requests'] >= threshold and ip['request_time'] >= limit_time):
            return True
    return False


PER_PAGE_CHOICES = (30, 50, 100)


def get_paginate(size, current, per_page=None):
    per_page = per_page if per_page in PER_PAGE_CHOICES else PER_PAGE_CHOICES[0]
    total_pages = max(1, (size + per_page - 1) // per_page)
    try:
        current = min(max(1, int(current or 1)), total_pages)
    except ValueError:
        current = 1
    paginate = {
        'records_per_page': per_page,
        'current': current,
        'offset': (current - 1) * per_page,
        'total_records': size,
        'total_pages': total_pages,
        'first_record': (current - 1) * per_page + 1 if size else 0,
        'last_record': min(current * per_page, size),
    }

    max_pages_to_display = 10
    half_max = max_pages_to_display // 2
    start_page = max(1, min(current - half_max + 1, total_pages - max_pages_to_display + 1))
    end_page = min(total_pages, start_page + max_pages_to_display - 1)
    paginate['range'] = range(start_page, end_page + 1)
    return paginate
