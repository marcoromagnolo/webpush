import json

import pytest
from pywebpush import WebPushException

import notifier
import vapid


@pytest.mark.parametrize('day_slots, slot, per_day, expected', [
    ([(8, 0), (9, 0), (10, 0)], (9, 0), 3, (0, 1)),  # 3 slots, 3 messages: everybody at every slot
    ([(h, 0) for h in range(8, 23)], (8, 0), 3, (0, 5)),  # 15 slots, 3 messages: 5 partitions
    ([(h, 0) for h in range(8, 23)], (13, 0), 3, (0, 5)),  # the 6th slot: the first partition again
    ([(h, 0) for h in range(8, 23)], (14, 0), 3, (1, 5)),
    ([(8, 0), (9, 30), (12, 0)], (9, 30), 2, (1, 2)),
    ([(8, 0)], (9, 0), 3, None),  # not a slot of the day
])
def test_partition_for_slot(day_slots, slot, per_day, expected):
    assert notifier.partition_for_slot(day_slots, *slot, per_day) == expected


def test_clean_message_keeps_known_options():
    title, options = notifier.clean_message('  Title ', {'body': ' Text ', 'unknown': 1, 'icon': '', 'tag': 't',
                                                          'data': {'url': 'https://example.com/a'}})
    assert title == 'Title'
    assert json.loads(options) == {'body': 'Text', 'tag': 't', 'data': {'url': 'https://example.com/a'}}


@pytest.mark.parametrize('title, options, error', [
    ('', {}, 'title'),
    ('x' * 256, {}, 'title'),
    ('T', [], 'object'),
    ('T', {'body': 5}, 'options.body'),
    ('T', {'timestamp': True}, 'options.timestamp'),
    ('T', {'dir': 'up'}, 'options.dir'),
    ('T', {'actions': [{'action': 'a'}]}, 'options.actions'),
    ('T', {'actions': [{'action': str(i), 'title': 't'} for i in range(3)]}, 'options.actions'),
    ('T', {'vibrate': [100, 'x']}, 'options.vibrate'),
    ('T', {'data': {'url': 'javascript:alert(1)'}}, 'options.data.url'),
    ('T', {'body': 'x' * 4000}, 'too long'),
])
def test_clean_message_errors(title, options, error):
    with pytest.raises(ValueError, match=error):
        notifier.clean_message(title, options)


SITE = {'id': 1, 'code': 'news', 'vapid_sub': 'mailto:a@example.com', 'ttl_seconds': 3600,
        'default_icon': 'https://example.com/icon.png', 'default_badge': None}


def test_build_payload_defaults_and_tracking():
    message = {'id': 7, 'title': 'T', 'options': json.dumps({'body': 'B', 'data': {'url': 'https://example.com/a'}})}
    payload = json.loads(notifier.build_payload(SITE, message))
    assert payload['icon'] == 'https://example.com/icon.png'
    assert 'badge' not in payload
    assert payload['data']['url'] == 'https://example.com/a'
    assert payload['data']['webpush'] == {'message_id': 7, 'click_url': 'https://push.example.com/api/v1/sites/news/clicks'}
    assert 'webpush' not in json.loads(notifier.build_payload(SITE, message, track=False))['data']


class Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.reason = 'Reason'


SUBSCRIBER = {'id': 1, 'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys_p256dh': 'p', 'keys_auth': 'a',
              'expiration_time': None}


@pytest.mark.parametrize('status, outcome', [(None, 'success'), (404, 'gone'), (410, 'gone'), (429, 'error'),
                                             (500, 'error'), (403, 'error')])
def test_send_one_outcomes(monkeypatch, status, outcome):
    def fake_webpush(**kwargs):
        if status:
            raise WebPushException('failed', response=Response(status))
    monkeypatch.setattr(notifier, 'webpush', fake_webpush)
    assert notifier.send_one(SITE, None, SUBSCRIBER, '{}')[0] == outcome


def test_send_one_new_claims_every_time(monkeypatch):
    claims = []
    monkeypatch.setattr(notifier, 'webpush', lambda **kwargs: claims.append(kwargs['vapid_claims']))
    notifier.send_one(SITE, None, SUBSCRIBER, '{}')
    claims[0]['exp'] = 1  # pywebpush writes in the dict: it must not change the next sending
    notifier.send_one(SITE, None, dict(SUBSCRIBER, endpoint='https://updates.push.services.mozilla.com/wpush/v2/x'), '{}')
    assert claims[1] == {'sub': 'mailto:a@example.com', 'aud': 'https://updates.push.services.mozilla.com'}
    assert claims[0]['aud'] == 'https://fcm.googleapis.com'


def test_send_one_expired_subscription_is_not_sent(monkeypatch):
    monkeypatch.setattr(notifier, 'webpush', lambda **kwargs: pytest.fail('sent'))
    assert notifier.send_one(SITE, None, dict(SUBSCRIBER, expiration_time=1000), '{}')[0] == 'expired'


def test_vapid_keys():
    private_key, public_key = vapid.generate()
    assert vapid.check(private_key, public_key) == (private_key, public_key)
    # base64 wraps the public key on more lines (public_key.txt of the README)
    assert vapid.check(private_key, '\n' + public_key[:76] + '\n' + public_key[76:] + '\n')[1] == public_key
    with pytest.raises(vapid.InvalidKeys):
        vapid.check(private_key, vapid.generate()[1])
    with pytest.raises(vapid.InvalidKeys):
        vapid.check('not a key')


class FullResponse(Response):
    def __init__(self, status_code, text=''):
        super().__init__(status_code)
        self.text = text
        self.headers = {'Location': 'https://push.example/m/1', 'Server': 'x'}


@pytest.mark.parametrize('status, ok, hint', [(201, True, 'Accepted'), (403, False, 'VAPID key different'),
                                              (410, False, "doesn't exist")])
def test_debug_send_describes_the_answer(monkeypatch, status, ok, hint):
    def fake_webpush(**kwargs):
        response = FullResponse(status, 'answer  of\nthe service')
        if status >= 400:
            raise WebPushException('failed', response=response)
        return response
    monkeypatch.setattr(notifier, 'webpush', fake_webpush)
    monkeypatch.setattr(notifier.vapid, 'signer', lambda key: None)
    site = dict(SITE, name='News', vapid_private_key='k', vapid_public_key='P')
    result = notifier.debug_send(site, SUBSCRIBER)
    assert result['ok'] is ok and result['status'] == status and hint in result['hint']
    assert result['answer'] == 'answer of the service' and result['headers'] == {'Location': 'https://push.example/m/1'}
    assert result['claims']['aud'] == 'https://fcm.googleapis.com'


def test_send_one_detail_has_the_answer(monkeypatch):
    def fake_webpush(**kwargs):
        raise WebPushException('failed', response=FullResponse(403, 'the key does not match'))
    monkeypatch.setattr(notifier, 'webpush', fake_webpush)
    outcome, summary, detail = notifier.send_one(SITE, None, SUBSCRIBER, '{}')
    assert outcome == 'error' and summary == '403 Reason (fcm.googleapis.com)'
    assert detail == '403 Reason from fcm.googleapis.com: the key does not match'
