from __future__ import annotations

import time

import pytest

from p2p.store_forward import QueuedMessage, QueueFullError, StoreForwardQueue


@pytest.fixture
def queue(tmp_path):
    db_path = tmp_path / "store_forward.sqlite"
    q = StoreForwardQueue(str(db_path), max_messages=10)
    yield q
    q.close()


def test_enqueue_message(queue):
    message = QueuedMessage(
        message_id="A:123",
        source="A",
        destination="D",
        envelope={"type": "data", "message_id": "A:123", "payload": {"value": "hello"}},
        created_at="2026-01-01T00:00:00Z",
        expires_at=None,
        priority=5,
    )

    queue.enqueue(message)
    pending = queue.get_pending("D")

    assert len(pending) == 1
    assert pending[0].message_id == "A:123"
    assert pending[0].state == "QUEUED"


def test_get_pending_messages(queue):
    queue.enqueue(QueuedMessage("A:1", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z"))
    queue.enqueue(QueuedMessage("A:2", "A", "D", {"payload": {"v": 2}}, "2026-01-01T00:00:00Z"))

    pending = queue.get_pending("D")
    assert [m.message_id for m in pending] == ["A:1", "A:2"]


def test_message_id_is_unique(queue):
    queue.enqueue(QueuedMessage("A:dup", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z"))
    with pytest.raises(Exception):
        queue.enqueue(QueuedMessage("A:dup", "A", "D", {"payload": {"v": 2}}, "2026-01-01T00:00:00Z"))


def test_message_id_preserved(queue):
    original = QueuedMessage("A:123", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z")
    queue.enqueue(original)
    pending = queue.get_pending("D")
    assert pending[0].message_id == "A:123"


def test_mark_delivered(queue):
    queue.enqueue(QueuedMessage("A:deliver", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z"))
    queue.mark_delivered("A:deliver")

    messages = queue.get_pending("D")
    assert messages == []


def test_mark_failed(queue):
    queue.enqueue(QueuedMessage("A:fail", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z"))
    queue.mark_failed("A:fail", "route unavailable")

    pending = queue.get_pending("D")
    assert pending == []


def test_mark_expired(queue):
    queue.enqueue(QueuedMessage("A:expire", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z", expires_at="2026-01-01T00:00:00Z"))
    queue.mark_expired("A:expire")

    pending = queue.get_pending("D")
    assert pending == []


def test_priority_ordering(queue):
    queue.enqueue(QueuedMessage("A:low", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z", priority=1))
    queue.enqueue(QueuedMessage("A:high", "A", "D", {"payload": {"v": 2}}, "2026-01-01T00:00:00Z", priority=9))

    pending = queue.get_pending("D")
    assert [m.message_id for m in pending] == ["A:high", "A:low"]


def test_pending_count(queue):
    queue.enqueue(QueuedMessage("A:1", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z"))
    queue.enqueue(QueuedMessage("A:2", "A", "D", {"payload": {"v": 2}}, "2026-01-01T00:00:00Z"))

    assert queue.pending_count("D") == 2


def test_multiple_destinations(queue):
    queue.enqueue(QueuedMessage("A:1", "A", "D", {"payload": {"v": 1}}, "2026-01-01T00:00:00Z"))
    queue.enqueue(QueuedMessage("A:2", "A", "E", {"payload": {"v": 2}}, "2026-01-01T00:00:00Z"))

    assert queue.pending_count("D") == 1
    assert queue.pending_count("E") == 1
