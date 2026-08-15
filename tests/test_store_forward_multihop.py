from __future__ import annotations

from p2p.store_forward import QueuedMessage, StoreForwardQueue


def test_multihop_route_recovery_queues_then_delivers(tmp_path):
    db_path = tmp_path / "multihop.sqlite"
    queue = StoreForwardQueue(str(db_path), max_messages=10)
    message = QueuedMessage(
        message_id="A:multihop",
        source="A",
        destination="E",
        envelope={"payload": {"value": "hello"}},
        created_at="2026-01-01T00:00:00Z",
    )

    queue.enqueue(message)
    assert queue.pending_count("E") == 1

    queued = queue.get_pending("E")
    assert queued[0].message_id == "A:multihop"

    queue.mark_delivered("A:multihop")
    assert queue.pending_count("E") == 0
    queue.close()
