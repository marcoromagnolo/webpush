# Hourly view of today in the Home: the slots of the day of the sites with what happened (deliveries) or what is
# going to be sent (the next messages of the queues), and the sendings started by hand. The sites without schedule
# (use_schedule false): their messages sent today at their time and the ones still to send today.
from collections import defaultdict
from datetime import datetime, timedelta

import db


def entry_status(delivery):
    if delivery['end_time'] is None:
        return 'running'
    return 'success' if delivery['success'] or not delivery['total'] else 'error'


def today(sites, now=None):
    """[{'hour', 'now', 'entries'}] of the hours from the first entry (or now) to the last one."""
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    names = {site['id']: site['name'] for site in sites}
    many = len(sites) > 1
    timed = {site['id'] for site in sites if not site.get('use_schedule', True)}
    slots = [s for s in db.get_slots() if s['site_id'] in names and s['site_id'] not in timed
             and s['day'] == now.isoweekday()]
    deliveries = [d for d in db.get_deliveries(since=midnight, limit=2000) if d['site_id'] in names and d['kind'] != 'test']
    by_slot = {(d['site_id'], d['slot']): d for d in deliveries if d['kind'] == 'slot'}
    queues = {site_id: db.get_next_queued(site_id, 100) for site_id in {s['site_id'] for s in slots}}
    upcoming = defaultdict(int)

    entries = []
    for slot in sorted(slots, key=lambda s: (s['hour'], s['minute'], names[s['site_id']])):
        at = f"{slot['hour']:02}:{slot['minute']:02}"
        entry = {'hour': slot['hour'], 'time': at, 'site': names[slot['site_id']] if many else None}
        delivery = by_slot.get((slot['site_id'], at))
        if delivery:
            entry.update(status=entry_status(delivery), title=delivery['title'], message_id=delivery['message_id'],
                         detail=f"{delivery['success']}/{delivery['total']} delivered")
        elif now.replace(hour=slot['hour'], minute=slot['minute'], second=0, microsecond=0) <= now:
            entry.update(status='missed', title=None, message_id=None,
                         detail='nothing sent: empty queue, no subscriber or site without keys')
        else:
            # the slots still to come take the messages of the queue in order
            queue = queues[slot['site_id']]
            position = upcoming[slot['site_id']]
            upcoming[slot['site_id']] += 1
            message = queue[position] if position < len(queue) else None
            entry.update(status='scheduled', title=message['title'] if message else None,
                         message_id=message['id'] if message else None,
                         detail='next in the queue' if message else 'the queue will be empty: nothing to send')
        entries.append(entry)
    tomorrow = midnight + timedelta(days=1)
    for site_id in timed:
        for message in db.get_next_queued(site_id, 100):
            at = max(message['send_after'] or now, now)
            if at < tomorrow:
                entries.append({'hour': at.hour, 'time': at.strftime('%H:%M'), 'site': names[site_id] if many else None,
                                'status': 'scheduled', 'title': message['title'], 'message_id': message['id'],
                                'detail': 'at its time' if message['send_after'] and message['send_after'] > now else 'now'})
    for delivery in deliveries:
        if delivery['kind'] == 'time':
            entries.append({'hour': delivery['start_time'].hour, 'time': delivery['start_time'].strftime('%H:%M'),
                            'site': names[delivery['site_id']] if many else None, 'status': entry_status(delivery),
                            'title': delivery['title'], 'message_id': delivery['message_id'],
                            'detail': f"at its time: {delivery['success']}/{delivery['total']} delivered"})
        if delivery['kind'] == 'now':
            entries.append({'hour': delivery['start_time'].hour, 'time': delivery['start_time'].strftime('%H:%M'),
                            'site': names[delivery['site_id']] if many else None, 'status': entry_status(delivery),
                            'title': delivery['title'], 'message_id': delivery['message_id'],
                            'detail': f"sent by hand: {delivery['success']}/{delivery['total']} delivered"})
    entries.sort(key=lambda e: e['time'])

    if not entries:
        return []
    first = min(entries[0]['hour'], now.hour)
    last = max(max(e['hour'] for e in entries), now.hour)
    by_hour = defaultdict(list)
    for entry in entries:
        by_hour[entry['hour']].append(entry)
    return [{'hour': hour, 'now': hour == now.hour, 'entries': by_hour[hour]} for hour in range(first, last + 1)]
