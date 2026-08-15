from __future__ import annotations

from p2p.store_forward import QueuedMessage, StoreForwardQueue


def test_expired_messages_are_marked_expired(tmp_path):
    db_path = tmp_path / "expiry.sqlite"
    queue = StoreForwardQueue(str(db_path), max_messages=10)
    message = QueuedMessage(
        message_id="A:expiry",
        source="A",
        destination="D",
        envelope={"payload": {"value": "hello"}},
        created_at="2026-01-01T00:00:00Z",
        expires_at="2000-01-01T00:00:00Z",
    )
    queue.enqueue(message)
    queue.expire_messages()

    pending = queue.get_pending("D")
    assert pending == []

    queue.close()


def test_expired_messages_are_not_retransmitted(tmp_path):
    db_path = tmp_path / "expiry_no_send.sqlite"
    queue = StoreForwardQueue(str(db_path), max_messages=10)
    message = QueuedMessage(
        message_id="A:no_send",
        source="A",
        destination="D",
        envelope={"payload": {"value": "hello"}},
        created_at="2026-01-01T00:00:00Z",
        expires_at="2000-01-01T00:00:00Z",
    )
    queue.enqueue(message)
    queue.expire_messages()

    assert queue.pending_count("D") == 0
    queue.close()
