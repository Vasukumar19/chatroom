from __future__ import annotations

import pytest

from p2p.store_forward import QueuedMessage


class RouteManager:
    def __init__(self, available: bool):
        self.available = available

    def route_available(self, destination: str) -> bool:
        return self.available


class Sender:
    def __init__(self):
        self.sent = []

    def send(self, destination: str, payload: dict):
        self.sent.append((destination, payload))
        return True


class DummyQueue:
    def __init__(self):
        self.items = []

    def enqueue(self, message):
        self.items.append(message)

    def pending_count(self, destination=None):
        return len(self.items)


@pytest.fixture
def manager():
    q = DummyQueue()
    sender = Sender()
    route = RouteManager(available=False)
    from p2p.store_forward_manager import StoreForwardManager
    return StoreForwardManager(queue=q, reliable_sender=sender, route_manager=route)


def test_manager_enqueues_when_route_unavailable(manager):
    result = manager.send("D", {"value": "hello"})
    assert result.status == "QUEUED"
    assert manager.queue.pending_count() == 1


def test_manager_uses_reliable_sender_when_route_available():
    q = DummyQueue()
    sender = Sender()
    route = RouteManager(available=True)
    from p2p.store_forward_manager import StoreForwardManager
    manager = StoreForwardManager(queue=q, reliable_sender=sender, route_manager=route)

    result = manager.send("D", {"value": "hello"})

    assert result.status == "DELIVERED"
    assert sender.sent == [("D", {"value": "hello"})]
    assert q.pending_count() == 0
