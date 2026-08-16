import copy

import pytest

from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.security import SecurityContext, SecurityError, generate_aes_key
from p2p.transport import MockTransport


def paired_contexts():
    key = generate_aes_key()
    sender = SecurityContext('A', key)
    receiver = SecurityContext('B', key)
    sender.trust_peer('B', receiver.public_key_bytes())
    receiver.trust_peer('A', sender.public_key_bytes())
    return sender, receiver


def test_secure_envelope_is_encrypted_signed_and_opened():
    sender, receiver = paired_contexts()
    envelope = {'message_id': 'm1', 'type': 'data', 'source': 'A', 'destination': 'B', 'payload': {'text': 'secret'}}

    protected = sender.protect(envelope)

    assert protected['payload'] != envelope['payload']
    assert protected['payload']['security']['algorithm'] == 'AES-256-GCM+Ed25519'
    assert receiver.open(protected)['payload'] == {'text': 'secret'}


def test_secure_envelope_rejects_tampering_and_replay():
    sender, receiver = paired_contexts()
    envelope = {'message_id': 'm1', 'type': 'data', 'source': 'A', 'destination': 'B', 'payload': {'text': 'secret'}}
    protected = sender.protect(envelope)
    tampered = copy.deepcopy(protected)
    tampered['destination'] = 'attacker'

    with pytest.raises(SecurityError):
        receiver.open(tampered)
    assert receiver.open(protected)['payload'] == {'text': 'secret'}
    with pytest.raises(SecurityError, match='Replay'):
        receiver.open(protected)


def test_secure_envelope_rejects_an_untrusted_sender():
    key = generate_aes_key()
    sender = SecurityContext('A', key)
    receiver = SecurityContext('B', key)
    envelope = {'message_id': 'm1', 'type': 'data', 'source': 'A', 'destination': 'B', 'payload': {'text': 'secret'}}

    with pytest.raises(SecurityError):
        receiver.open(sender.protect(envelope))


def test_reliability_delivers_encrypted_payload_once_after_dropped_ack():
    class DropFirstAck(MockTransport):
        def __init__(self):
            super().__init__()
            self.dropped = False

        def send(self, address, message):
            if message.get('type') == 'ack' and not self.dropped:
                self.dropped = True
                return
            super().send(address, message)

    sender_security, receiver_security = paired_contexts()
    transport = DropFirstAck()
    delivered = []
    ReliableReceiver('B', transport, lambda msg, addr: delivered.append(msg['payload']), security=receiver_security)
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1, security=sender_security)

    assert sender.send('B', {'text': 'confidential'}) is True
    assert sender.retry_count == 1
    assert delivered == [{'text': 'confidential'}]
