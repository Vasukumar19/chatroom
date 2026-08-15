from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from p2p.store_forward import QueuedMessage


@dataclass
class DeliveryResult:
    status: str
    message_id: str
    retry_count: int = 0
    error: Optional[str] = None


class StoreForwardManager:
    """Simple coordinator for queueing messages when a route is unavailable."""

    def __init__(self, *, queue=None, reliable_sender=None, route_manager=None, db_path: Optional[str] = None, max_messages: Optional[int] = None):
        self.queue = queue
        self.reliable_sender = reliable_sender
        self.route_manager = route_manager
        self.db_path = db_path
        self.max_messages = max_messages

    def _make_message_id(self, destination: str) -> str:
        return f"storeforward:{int(time.time() * 1000000)}:{destination}"

    def _create_queued_message(self, destination: str, payload: Any, *, ttl: Optional[float], priority: int) -> QueuedMessage:
        message_id = self._make_message_id(destination)
        now = datetime.now(timezone.utc)
        expires_at = None
        if ttl is not None:
            expires_at = (now + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        return QueuedMessage(
            message_id=message_id,
            source="local",
            destination=destination,
            envelope={"payload": payload},
            created_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=expires_at,
            priority=priority,
            state="QUEUED",
        )

    def send(self, destination: str, payload: Any, *, ttl: Optional[float] = None, priority: int = 0):
        if self.route_manager is not None and not self.route_manager.route_available(destination):
            message = self._create_queued_message(destination, payload, ttl=ttl, priority=priority)
            if self.queue is not None:
                self.queue.enqueue(message)
            return DeliveryResult(status="QUEUED", message_id=message.message_id)

        if self.reliable_sender is None:
            return DeliveryResult(status="FAILED", message_id=self._make_message_id(destination), error="no reliable sender")

        ok = self.reliable_sender.send(destination, payload)
        if ok:
            return DeliveryResult(status="DELIVERED", message_id=self._make_message_id(destination))
        return DeliveryResult(status="FAILED", message_id=self._make_message_id(destination), error="send failed")

    def replay(self, destination: Optional[str] = None):
        if self.queue is None:
            return []

        pending = self.queue.get_pending(destination=destination)
        results = []
        for message in pending:
            if message.expires_at is not None:
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if message.expires_at <= now:
                    self.queue.mark_expired(message.message_id)
                    continue

            if self.route_manager is not None and not self.route_manager.route_available(message.destination):
                continue

            self.queue.mark_replaying(message.message_id)

            payload = message.envelope.get("payload", {})
            sender = self.reliable_sender
            if sender is None:
                self.queue.mark_failed(message.message_id, "no reliable sender")
                results.append(DeliveryResult(status="FAILED", message_id=message.message_id, error="no reliable sender"))
                continue

            try:
                ok = sender.send(message.destination, payload, message_id=message.message_id)
            except TypeError:
                ok = sender.send(message.destination, payload)

            if ok:
                self.queue.mark_delivered(message.message_id)
                results.append(DeliveryResult(status="DELIVERED", message_id=message.message_id))
            else:
                self.queue.mark_failed(message.message_id, "replay failed")
                results.append(DeliveryResult(status="FAILED", message_id=message.message_id, error="replay failed"))

                # A failed replay is still a queued message waiting for a later retry.
                # Do not remove it from the pending queue.
                self.queue._conn.execute(
                    "UPDATE messages SET state = 'QUEUED' WHERE message_id = ?",
                    (message.message_id,),
                )
                self.queue._conn.commit()

        return results

    def on_route_recovered(self, destination: str):
        return self.replay(destination)

    def expire(self):
        if self.queue is None:
            return 0
        return self.queue.expire_messages()
