from __future__ import annotations

import pytest

from p2p.protocol import create_envelope
from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.store_forward import QueuedMessage, StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.transport import MockTransport


class DummyRouteManager:
    def __init__(self, available: bool):
        self.available = available

    def route_available(self, destination: str) -> bool:
        return self.available


def test_queued_message_replays_after_route_recovery(tmp_path):
    transport = MockTransport()
    received = []
    ReliableReceiver('E', transport, lambda payload, addr: received.append(payload['payload']))
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)
    route = DummyRouteManager(False)
    queue = StoreForwardQueue(str(tmp_path / 'replay.sqlite'), max_messages=10)
    manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

    queue.enqueue(QueuedMessage(
        message_id='A:123',
        source='A',
        destination='E',
        envelope={'type': 'data', 'message_id': 'A:123', 'source': 'A', 'destination': 'E', 'payload': {'value': 'hello'}},
        created_at='2026-01-01T00:00:00Z',
    ))

    route.available = True
    results = manager.replay('E')

    assert len(results) == 1
    assert results[0].status == 'DELIVERED'
    assert results[0].message_id == 'A:123'
    assert received == [{'value': 'hello'}]
    assert queue.pending_count('E') == 0
    queue.close()


def test_original_message_id_is_preserved_on_replay(tmp_path):
    transport = MockTransport()
    received = []
    ReliableReceiver('E', transport, lambda payload, addr: received.append(payload['payload']))
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)
    route = DummyRouteManager(True)
    queue = StoreForwardQueue(str(tmp_path / 'id.sqlite'), max_messages=10)
    manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

    queue.enqueue(QueuedMessage(
        message_id='A:123',
        source='A',
        destination='E',
        envelope={'type': 'data', 'message_id': 'A:123', 'source': 'A', 'destination': 'E', 'payload': {'value': 'id-check'}},
        created_at='2026-01-01T00:00:00Z',
    ))

    results = manager.replay('E')

    assert results[0].message_id == 'A:123'
    assert sender.last_message_id == 'A:123'
    queue.close()


def test_failed_replay_stays_pending(tmp_path):
    transport = MockTransport()
    class FailingSender:
        def __init__(self):
            self.calls = 0
        def send(self, destination, payload, message_id=None):
            self.calls += 1
            return False

    queue = StoreForwardQueue(str(tmp_path / 'failed.sqlite'), max_messages=10)
    sender = FailingSender()
    route = DummyRouteManager(True)
    manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

    queue.enqueue(QueuedMessage(
        message_id='A:fail',
        source='A',
        destination='E',
        envelope={'type': 'data', 'message_id': 'A:fail', 'source': 'A', 'destination': 'E', 'payload': {'value': 'retry-later'}},
        created_at='2026-01-01T00:00:00Z',
    ))

    results = manager.replay('E')

    assert results[0].status == 'FAILED'
    assert queue.pending_count('E') >= 1
    queue.close()


def test_multiple_pending_messages_replay_in_order(tmp_path):
    transport = MockTransport()
    received = []
    ReliableReceiver('E', transport, lambda payload, addr: received.append(payload['payload']))
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)
    route = DummyRouteManager(True)
    queue = StoreForwardQueue(str(tmp_path / 'multi.sqlite'), max_messages=10)
    manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

    queue.enqueue(QueuedMessage('A:1', 'A', 'E', {'type': 'data', 'message_id': 'A:1', 'source': 'A', 'destination': 'E', 'payload': {'value': 'one'}}, '2026-01-01T00:00:00Z'))
    queue.enqueue(QueuedMessage('A:2', 'A', 'E', {'type': 'data', 'message_id': 'A:2', 'source': 'A', 'destination': 'E', 'payload': {'value': 'two'}}, '2026-01-01T00:00:00Z'))

    results = manager.replay('E')

    assert [r.message_id for r in results] == ['A:1', 'A:2']
    assert len(received) == 2
    assert queue.pending_count('E') == 0
    queue.close()


def test_expired_message_is_not_replayed(tmp_path):
    transport = MockTransport()
    received = []
    ReliableReceiver('E', transport, lambda payload, addr: received.append(payload['payload']))
    sender = ReliableSender('A', transport, timeout=0.05, max_retries=1)
    route = DummyRouteManager(True)
    queue = StoreForwardQueue(str(tmp_path / 'expired.sqlite'), max_messages=10)
    manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

    queue.enqueue(QueuedMessage(
        message_id='A:expired',
        source='A',
        destination='E',
        envelope={'type': 'data', 'message_id': 'A:expired', 'source': 'A', 'destination': 'E', 'payload': {'value': 'should-not-send'}},
        created_at='2026-01-01T00:00:00Z',
        expires_at='2000-01-01T00:00:00Z',
    ))

    queue.expire_messages()
    results = manager.replay('E')

    assert results == []
    assert received == []
    assert queue.pending_count('E') == 0
    queue.close()
