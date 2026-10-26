from datetime import datetime

import timeline

NOW = datetime(2026, 10, 26, 10, 0)  # a Monday


def test_today_of_a_site_without_schedule(monkeypatch):
    site = {'id': 1, 'name': 'News', 'use_schedule': False}
    monkeypatch.setattr(timeline.db, 'get_slots', lambda: [{'site_id': 1, 'day': 1, 'hour': 12, 'minute': 30}])
    monkeypatch.setattr(timeline.db, 'get_deliveries', lambda since, limit: [
        {'site_id': 1, 'kind': 'time', 'slot': '08:00', 'start_time': datetime(2026, 10, 26, 8, 0), 'end_time': NOW,
         'success': 3, 'total': 3, 'title': 'Sent', 'message_id': 1}])
    monkeypatch.setattr(timeline.db, 'get_next_queued', lambda site_id, limit: [
        {'id': 2, 'title': 'Later', 'send_after': datetime(2026, 10, 26, 18, 45)},
        {'id': 3, 'title': 'Tomorrow', 'send_after': datetime(2026, 10, 27, 7, 0)}])
    entries = [e for hour in timeline.today([site], NOW) for e in hour['entries']]
    # the slots are not used; the message sent at its time and the one still to send today
    assert [(e['time'], e['title'], e['status']) for e in entries] == [('08:00', 'Sent', 'success'),
                                                                       ('18:45', 'Later', 'scheduled')]
