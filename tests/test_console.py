import json

import pytest
from werkzeug.datastructures import MultiDict

import app as webapp


def test_message_form_roundtrip():
    form = MultiDict({'title': 'T', 'body': 'B', 'url': 'https://e.example/a', 'image': 'https://e.example/i.jpg',
                      'tag': 'x', 'requireInteraction': 'on', 'action1_title': 'Read', 'action1_url': 'https://e.example/r',
                      'extra': '{"vibrate": [100], "data": {"campaign": "c"}}'})
    title, options = webapp.form_to_message(form)
    assert json.loads(options) == {
        'vibrate': [100], 'body': 'B', 'image': 'https://e.example/i.jpg', 'tag': 'x', 'requireInteraction': True,
        'actions': [{'action': 'a1', 'title': 'Read'}],
        'data': {'campaign': 'c', 'url': 'https://e.example/a', 'actions': {'a1': 'https://e.example/r'}}}
    back = webapp.message_to_form({'title': title, 'options': options, 'send_after': None})
    assert back['url'] == 'https://e.example/a' and back['action1_url'] == 'https://e.example/r'
    assert back['requireInteraction'] and json.loads(back['extra']) == {'vibrate': [100], 'data': {'campaign': 'c'}}
    # the form of the form gives the same message
    assert webapp.form_to_message(MultiDict({k: ('on' if v is True else v) for k, v in back.items() if v not in (False, '')})) == (title, options)


def test_message_form_bad_extra():
    with pytest.raises(ValueError, match='Extra options'):
        webapp.form_to_message(MultiDict({'title': 'T', 'extra': '{bad'}))


def test_parse_times():
    assert webapp.parse_times(' 8 9:30, 12.15;23:59 8:00 ') == [(8, 0), (9, 30), (12, 15), (23, 59)]
    assert webapp.parse_times('') == []
    for wrong in ('24', '9:60', 'noon'):
        with pytest.raises(ValueError):
            webapp.parse_times(wrong)


SITE_FORM = {'name': 'News', 'code': 'news', 'url': 'https://www.news.example/', 'vapid_sub': 'mailto:a@news.example',
             'ttl_hours': '24', 'enabled': 'on', 'allowed_origins': 'https://m.news.example/\n\n'}


def test_site_form(monkeypatch):
    monkeypatch.setattr(webapp.db, 'get_site', lambda site_id=None, code=None: None)
    values = webapp.form_to_site(MultiDict(SITE_FORM))
    assert values['url'] == 'https://www.news.example' and values['allowed_origins'] == 'https://m.news.example'
    assert values['ttl_seconds'] == 86400 and values['enabled'] is True


@pytest.mark.parametrize('field, value', [('code', 'News Site'), ('url', 'ftp://x'), ('vapid_sub', 'a@news.example'),
                                          ('ttl_hours', '0'),
                                          ('allowed_origins', 'https://m.news.example/path')])
def test_site_form_errors(monkeypatch, field, value):
    monkeypatch.setattr(webapp.db, 'get_site', lambda site_id=None, code=None: None)
    with pytest.raises(ValueError):
        webapp.form_to_site(MultiDict(dict(SITE_FORM, **{field: value})))


def test_safe_next():
    assert webapp.safe_next('/messages?status=sent', '/') == '/messages?status=sent'
    for unsafe in ('//evil.example/x', 'https://evil.example/', None, ''):
        assert webapp.safe_next(unsafe, '/') == '/'


def test_default_schedule():
    # the 5 best times every day: 35 slots a week, created with every site
    assert webapp.DEFAULT_TIMES == ((7, 30), (12, 30), (15, 30), (18, 30), (21, 0))
    assert len(webapp.DEFAULT_SLOTS) == 35 and {d for d, _, _ in webapp.DEFAULT_SLOTS} == set(range(1, 8))
