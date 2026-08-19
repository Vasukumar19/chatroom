from __future__ import annotations

import inspect
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
        self._message_id_counter = 0
        # If the sender supports it, learn about ACKs that arrive after it
        # already gave up waiting (see ReliableSender._abandoned). This is
        # what lets a queued message reach DELIVERED promptly even when the
        # synchronous send_envelope() call timed out despite the destination
        # having genuinely processed the message and ACKed it.
        if self.reliable_sender is not None and hasattr(self.reliable_sender, "on_late_ack"):
            self.reliable_sender.on_late_ack = self._on_late_ack

    def _on_late_ack(self, message_id: str, destination: str) -> None:
        if self.queue is None:
            return
        try:
            self.queue.mark_delivered(message_id)
        except Exception:
            pass

    def _make_message_id(self, destination: str) -> str:
        self._message_id_counter += 1
        return f"storeforward:{int(time.time() * 1000000)}:{self._message_id_counter}:{destination}"

    def _invoke_sender(self, destination: str, payload: Any, message_id: str, envelope: dict) -> bool:
        sender = self.reliable_sender
        if sender is None:
            return False

        try:
            if hasattr(sender, "send_envelope") and envelope.get("type") == "data":
                return sender.send_envelope(destination, envelope)

            send_method = sender.send
            parameters = inspect.signature(send_method).parameters
            if "message_id" in parameters:
                return send_method(destination, payload, message_id=message_id)
            return send_method(destination, payload)
        except Exception:
            return False

    def _create_queued_message(self, destination: str, payload: Any, *, ttl: Optional[float], priority: int) -> QueuedMessage:
        message_id = self._make_message_id(destination)
        now = datetime.now(timezone.utc)
        expires_at = None
        if ttl is not None:
            expires_at = (now + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        sender = self.reliable_sender
        if sender is not None and hasattr(sender, "prepare_envelope"):
            envelope = sender.prepare_envelope(destination, payload, message_id=message_id)
            source = envelope.get("source", "local")
        else:
            envelope = {"payload": payload}
            source = "local"
        return QueuedMessage(
            message_id=message_id,
            source=source,
            destination=destination,
            envelope=envelope,
            created_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=expires_at,
            priority=priority,
            state="QUEUED",
        )

    def send(self, destination: str, payload: Any, *, ttl: Optional[float] = None, priority: int = 0):
        message = self._create_queued_message(destination, payload, ttl=ttl, priority=priority)
        if self.route_manager is not None and not self.route_manager.route_available(destination):
            if self.queue is not None:
                self.queue.enqueue(message)
            return DeliveryResult(status="QUEUED", message_id=message.message_id)

        if self.reliable_sender is None:
            return DeliveryResult(status="FAILED", message_id=self._make_message_id(destination), error="no reliable sender")

        try:
            ok = self._invoke_sender(destination, payload, message.message_id, message.envelope)
        except Exception:
            ok = False

        if ok:
            return DeliveryResult(status="DELIVERED", message_id=message.message_id)

        # Immediate send failed (e.g. transport connection failure or timeout).
        # Invalidate stale route if route_manager supports it so subsequent sends know destination is unreachable.
        if self.route_manager is not None:
            if hasattr(self.route_manager, "remove_route"):
                try:
                    self.route_manager.remove_route(destination)
                except Exception:
                    pass
            elif hasattr(self.route_manager, "set_status"):
                try:
                    self.route_manager.set_status(destination, "INVALID")
                except Exception:
                    pass

        if self.queue is not None:
            self.queue.enqueue(message)
            return DeliveryResult(status="QUEUED", message_id=message.message_id, error="send failed; queued")
        return DeliveryResult(status="FAILED", message_id=message.message_id, error="send failed")

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

            sender = self.reliable_sender
            if sender is None:
                self.queue.mark_failed(message.message_id, "no reliable sender")
                results.append(DeliveryResult(status="FAILED", message_id=message.message_id, error="no reliable sender"))
                continue

            payload = message.envelope.get("payload", {})
            send_method = sender.send
            parameters = inspect.signature(send_method).parameters

            if hasattr(sender, "send_envelope") and message.envelope.get("type") == "data":
                ok = sender.send_envelope(message.destination, message.envelope)
            elif "message_id" in parameters:
                ok = send_method(
                    message.destination,
                    payload,
                    message_id=message.message_id,
                )
            else:
                ok = send_method(message.destination, payload)

            if ok:
                self.queue.mark_delivered(message.message_id)
                results.append(DeliveryResult(status="DELIVERED", message_id=message.message_id))
            else:
                self.queue.mark_failed(message.message_id, "replay failed")
                results.append(DeliveryResult(status="FAILED", message_id=message.message_id, error="replay failed"))

                # A failed replay is still a queued message waiting for a later retry.
                # Do not remove it from the pending queue.
                self.queue.mark_queued(message.message_id)

        return results

    def on_route_recovered(self, destination: str):
        return self.replay(destination)

    def expire(self):
        if self.queue is None:
            return 0
        return self.queue.expire_messages()
