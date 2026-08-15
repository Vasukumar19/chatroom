import threading
import time

from p2p.protocol import create_envelope
from p2p.transport import MockTransport
from p2p.reliability import ReliableSender, ReliableReceiver


class DroppingTransport(MockTransport):
    def __init__(self, drop_ack_count: int = 0):
        super().__init__()
        self.drop_ack_count = drop_ack_count
        self.acks_dropped = 0

    def send(self, address, message):
        if (
            message.get("type") == "ack"
            and self.acks_dropped < self.drop_ack_count
        ):
            self.acks_dropped += 1
            return

        super().send(address, message)


def test_ack_received():
    transport = MockTransport()
    processed = []
    receiver = ReliableReceiver('B', transport, lambda msg, addr: processed.append(msg['payload']))
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)

    sender.send('B', {'value': 'hello'})

    assert processed == [{'value': 'hello'}]
    assert sender.last_status == 'ACKED'
    assert sender.last_message_id is not None


def test_ack_correlates_message():
    transport = MockTransport()
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)
    receiver = ReliableReceiver('B', transport, lambda msg, addr: None)

    sender.send('B', {'value': 'x'})

    ack = transport.sent_history[-1][1]
    assert ack['type'] == 'ack'
    assert ack['message_id'] == sender.last_message_id
    assert ack['payload']['message_id'] == sender.last_message_id


def test_retry_after_timeout():
    transport = DroppingTransport(drop_ack_count=1)
    sender = ReliableSender('A', transport, timeout=0.02, max_retries=2)
    receiver = ReliableReceiver('B', transport, lambda msg, addr: None)

    ok = sender.send('B', {'value': 'retry'})

    assert ok is True
    assert sender.retry_count == 1
    assert sender.last_status == 'ACKED'


def test_retry_ack_completes_sender():
    transport = DroppingTransport(drop_ack_count=1)
    processed = []
    receiver = ReliableReceiver('B', transport, lambda msg, addr: processed.append(msg['payload']))
    sender = ReliableSender('A', transport, timeout=0.02, max_retries=2)

    ok = sender.send('B', {'value': 'retry'})

    assert ok is True
    assert sender.last_status == 'ACKED'
    assert sender.retry_count == 1
    assert len(processed) == 1
    assert processed[0] == {'value': 'retry'}


def test_synchronous_transport_ack_does_not_deadlock():
    transport = MockTransport()
    receiver = ReliableReceiver('B', transport, lambda msg, addr: None)
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)

    ok = sender.send('B', {'value': 'sync'})

    assert ok is True
    assert sender.last_status == 'ACKED'


def test_max_retries():
    transport = MockTransport()
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1)

    ok = sender.send('B', {'value': 'fail'})

    assert ok is False
    assert sender.last_status == 'FAILED'


def test_success_after_two_retries():
    transport = DroppingTransport(drop_ack_count=2)
    sender = ReliableSender('A', transport, timeout=0.02, max_retries=3)
    receiver = ReliableReceiver('B', transport, lambda msg, addr: None)

    ok = sender.send('B', {'value': 'recovered'})

    assert ok is True
    assert sender.last_status == 'ACKED'
    assert sender.retry_count == 2


def test_duplicate_data_not_processed_twice():
    transport = MockTransport()
    receiver = ReliableReceiver('B', transport, lambda msg, addr: None)
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1)

    data = create_envelope('data', source='A', destination='B', payload={'hi': 1})
    receiver._on_transport_message(data, ('127.0.0.1', 9001))
    receiver._on_transport_message(data, ('127.0.0.1', 9001))

    assert len(receiver.processed_message_ids) == 1
    assert list(receiver.processed_message_ids)[0] == data['message_id']


def test_unknown_ack_ignored():
    transport = MockTransport()
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1)

    ack = create_envelope('ack', source='B', destination='A', payload={'message_id': 'ghost'}, message_id='ghost')
    sender._on_transport_message(ack, ('127.0.0.1', 9001))

    assert sender.last_status != 'ACKED'


def test_failed_delivery_reported():
    transport = MockTransport()
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1, on_failed=lambda mid: None)

    ok = sender.send('B', {'value': 'bad'})

    assert ok is False
    assert sender.last_status == 'FAILED'
