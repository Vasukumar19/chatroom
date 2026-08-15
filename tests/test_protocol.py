import json
import pytest
from p2p import protocol


def test_create_and_validate_envelope():
    env = protocol.create_envelope(
        msg_type='chat_message',
        source='nodeA',
        payload={'text': 'hello'},
        ttl=5,
        priority=1
    )

    assert env['type'] == 'chat_message'
    assert env['source'] == 'nodeA'
    assert env['ttl'] == 5
    assert env['priority'] == 1
    assert 'message_id' in env

    # validation should pass
    assert protocol.validate_envelope(env) is True


def test_serialize_deserialize_roundtrip():
    env = protocol.create_envelope('chat_message', 'n1', {'t': 'x'})
    raw = protocol.serialize_envelope(env)
    parsed = protocol.deserialize_envelope(raw)
    assert parsed['message_id'] == env['message_id']


def test_invalid_ttl_and_type():
    with pytest.raises(ValueError):
        protocol.create_envelope('unknown_type', 's', {})

    with pytest.raises(ValueError):
        protocol.create_envelope('chat_message', 's', {}, ttl=-1)


def test_unique_ids():
    a = protocol.create_envelope('chat_message', 's', {})
    b = protocol.create_envelope('chat_message', 's', {})
    assert a['message_id'] != b['message_id']


def test_wrap_legacy_message():
    legacy = {'type': 'chat_message', 'data': {'Message': 'hi'}}
    wrapped = protocol.wrap_legacy_message(legacy, source='me', room='r')
    assert 'message_id' in wrapped
    assert wrapped['type'] == 'chat_message'
    assert wrapped['payload']['Message'] == 'hi'
