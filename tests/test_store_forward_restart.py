from __future__ import annotations

from p2p.store_forward import QueuedMessage, StoreForwardQueue


def test_queue_recovers_pending_messages_after_restart(tmp_path):
    db_path = tmp_path / "restart.sqlite"
    queue1 = StoreForwardQueue(str(db_path), max_messages=10)
    message = QueuedMessage(
        message_id="A:restart",
        source="A",
        destination="D",
        envelope={"payload": {"value": "hello"}},
        created_at="2026-01-01T00:00:00Z",
    )
    queue1.enqueue(message)
    queue1.close()

    queue2 = StoreForwardQueue(str(db_path), max_messages=10)
    pending = queue2.get_pending("D")

    assert len(pending) == 1
    assert pending[0].message_id == "A:restart"
    queue2.close()
