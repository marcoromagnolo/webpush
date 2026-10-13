import json

import pytest

import api
import app as webapp
import db
import util

SITE = {'id': 1, 'code': 'news', 'name': 'News', 'url': 'https://www.news.example', 'enabled': True,
        'allowed_origins': 'https://m.news.example', 'vapid_public_key': 'BPUBLIC', 'vapid_private_key': 'x'}
OTHER = dict(SITE, id=2, code='other', url='https://other.example', allowed_origins=None)
TOKENS = {'site-token': {'id': 1, 'name': 'cms', 'site_id': 1},
          'other-token': {'id': 3, 'name': 'other', 'site_id': 2}}
ORIGIN = {'Origin': 'https://www.news.example'}
SUBSCRIPTION = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'expirationTime': 1893456000000,
                'keys': {'p256dh': 'P256', 'auth': 'AUTH'}}


@pytest.fixture
def calls(monkeypatch):
    calls = []
    sites = {'news': SITE, 'other': OTHER, 'off': dict(SITE, id=3, code='off', enabled=False)}
    monkeypatch.setattr(util, 'is_blocked_ip', lambda ip: False)
    monkeypatch.setattr(util, 'block_ip', lambda ip: calls.append(('block_ip', ip)))
    monkeypatch.setattr(db, 'get_site', lambda site_id=None, code=None: sites.get(code))
    monkeypatch.setattr(db, 'use_api_token', lambda token_hash, ip: next(
        (t for k, t in TOKENS.items() if api.hash_token(k) == token_hash), None))
    monkeypatch.setattr(db, 'upsert_subscriber', lambda *args: calls.append(('subscribe',) + args) or True)
    monkeypatch.setattr(db, 'unsubscribe', lambda *args: calls.append(('unsubscribe',) + args) or True)
    monkeypatch.setattr(db, 'add_message', lambda *args: calls.append(('message',) + args) or 42)
    monkeypatch.setattr(db, 'add_click', lambda *args: calls.append(('click',) + args))
    monkeypatch.setattr(api.settings, 'DEFAULT_SITE', None)
    return calls


@pytest.fixture
def client():
    return webapp.app.test_client()


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


def test_vapid_public_key(client, calls):
    assert client.get('/api/v1/sites/news/vapid-public-key').get_json() == {'public_key': 'BPUBLIC'}
    assert client.get('/api/v1/sites/nope/vapid-public-key').status_code == 404
    assert client.get('/api/v1/sites/off/vapid-public-key').status_code == 404


def test_subscribe_needs_an_origin_of_the_site(client, calls):
    url = '/api/v1/sites/news/subscriptions'
    assert client.post(url, json={'subscription': SUBSCRIPTION}).status_code == 403
    assert client.post(url, json={'subscription': SUBSCRIPTION}, headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.post(url, json={'subscription': SUBSCRIPTION}, headers={'Origin': 'https://m.news.example'})
    assert response.status_code == 201
    assert response.headers['Access-Control-Allow-Origin'] == 'https://m.news.example'
    assert calls[-1][:6] == ('subscribe', 1, SUBSCRIPTION['endpoint'], 'P256', 'AUTH', 1893456000000)


def test_preflight(client, calls):
    response = client.options('/api/v1/sites/news/subscriptions',
                              headers=dict(ORIGIN, **{'Access-Control-Request-Method': 'POST'}))
    assert response.headers['Access-Control-Allow-Origin'] == 'https://www.news.example'
    assert 'Origin' in response.headers['Vary']


@pytest.mark.parametrize('body', [{}, {'subscription': dict(SUBSCRIPTION, endpoint='http://x')},
                                  {'subscription': dict(SUBSCRIPTION, keys={})},
                                  {'subscription': dict(SUBSCRIPTION, expirationTime='tomorrow')}])
def test_subscribe_invalid(client, calls, body):
    assert client.post('/api/v1/sites/news/subscriptions', json=body, headers=ORIGIN).status_code == 400


def test_unsubscribe_and_click(client, calls):
    response = client.delete('/api/v1/sites/news/subscriptions', json={'endpoint': SUBSCRIPTION['endpoint']}, headers=ORIGIN)
    assert response.get_json() == {'unsubscribed': True}
    assert client.post('/api/v1/sites/news/clicks', json={'message_id': 7}, headers=ORIGIN).status_code == 204
    assert ('click', 1, 7) in calls


def test_messages_need_a_token_of_the_site(client, calls):
    url = '/api/v1/sites/news/messages'
    body = {'title': 'T', 'url': 'https://www.news.example/a', 'options': {'body': 'B'}}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=bearer('wrong')).status_code == 401
    assert ('block_ip', '127.0.0.1') in calls
    assert client.post(url, json=body, headers=bearer('other-token')).status_code == 403
    response = client.post(url, json=body, headers=bearer('site-token'))
    assert response.status_code == 202 and response.get_json() == {'id': 42, 'status': 'queued'}
    _, site_id, title, options, send_after, created_by = calls[-1]
    assert (site_id, title, send_after, created_by) == (1, 'T', None, 'token cms')
    assert json.loads(options) == {'body': 'B', 'data': {'url': 'https://www.news.example/a'}}


def test_message_send_after_and_errors(client, calls):
    url = '/api/v1/sites/news/messages'
    response = client.post(url, json={'title': 'T', 'send_after': '2030-01-01T10:00:00'}, headers=bearer('site-token'))
    assert response.status_code == 202 and str(calls[-1][4]) == '2030-01-01 10:00:00'
    assert client.post(url, json={'title': 'T', 'send_after': 'soon'}, headers=bearer('site-token')).status_code == 400
    assert client.post(url, json={'title': ''}, headers=bearer('site-token')).status_code == 400
    assert client.post(url, data='not json', headers=bearer('site-token')).status_code == 400


def test_legacy_endpoints(client, calls, monkeypatch):
    assert client.get('/subscription/').status_code == 404  # no DEFAULT_SITE
    monkeypatch.setattr(api.settings, 'DEFAULT_SITE', 'news')
    assert client.get('/subscription/').get_json() == {'public_key': 'BPUBLIC'}
    assert client.post('/subscription/', json={'subscription_token': SUBSCRIPTION}, headers=ORIGIN).status_code == 201
    assert client.post('/push_message/', json={'title': 'T', 'options': {}}).status_code == 401
    assert client.post('/push_message/', json={'title': 'T', 'options': {}}, headers=bearer('site-token')).status_code == 202


def test_console_origin_is_allowed(client, calls):
    # the test page of the console (PUBLIC_URL of conftest.py) subscribes like the site
    response = client.post('/api/v1/sites/news/subscriptions', json={'subscription': SUBSCRIPTION},
                           headers={'Origin': 'https://push.example.com'})
    assert response.status_code == 201
