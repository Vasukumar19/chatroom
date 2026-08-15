"""Reliability layer for DisasterConnect.

Provides a minimal ACK-based delivery guarantee and duplicate protection for
application-level data messages. It intentionally sits above the routing layer
and does not decide route selection.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from p2p.protocol import create_envelope, validate_envelope


class ReliableSender:
    """Sends application payloads with ACK correlation and retry handling."""

    def __init__(
        self,
        node_id: str,
        transport,
        *,
        timeout: float = 0.25,
        max_retries: int = 3,
        on_failed: Optional[Callable[[str], None]] = None,
    ):
        self.node_id = node_id
        self.transport = transport
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.on_failed = on_failed
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, object]] = {}
        self._pending_events: Dict[str, threading.Event] = {}
        self.last_status = 'CREATED'
        self.last_message_id: Optional[str] = None
        self.retry_count = 0
        try:
            self.transport.register_handler(self._on_transport_message)
        except Exception:
            pass

    def send(self, destination: str, payload: Dict[str, object], message_id: Optional[str] = None) -> bool:
        message_id = message_id or f"{self.node_id}:{int(time.time() * 1000000)}:{len(self._pending)}"
        self.last_message_id = message_id
        self.last_status = 'SENT'
        self.retry_count = 0

        env = create_envelope(
            'data',
            source=self.node_id,
            destination=destination,
            payload=payload,
            message_id=message_id,
        )
        env['delivery_attempt'] = 0
        event = threading.Event()

        with self._lock:
            self._pending[message_id] = {
                'destination': destination,
                'payload': payload,
                'env': env,
                'retries': 0,
                'deadline': time.time() + self.timeout,
                'status': 'SENT',
            }
            self._pending_events[message_id] = event

        address = destination if isinstance(destination, tuple) else (destination, 0)
        self.transport.send(address, env)
        return self._wait_for_ack(message_id, event)

    def _wait_for_ack(self, message_id: str, event: threading.Event) -> bool:
        retries = 0
        failed_callback = None

        while True:
            if event.wait(self.timeout):
                with self._lock:
                    pending = self._pending.get(message_id)
                    if pending is None:
                        return self.last_status == 'ACKED'
                    if pending['status'] == 'ACKED':
                        self.last_status = 'ACKED'
                        self._pending.pop(message_id, None)
                        self._pending_events.pop(message_id, None)
                        return True
                    if pending['status'] == 'FAILED':
                        self.last_status = 'FAILED'
                        self._pending.pop(message_id, None)
                        self._pending_events.pop(message_id, None)
                        return False
                continue

            with self._lock:
                pending = self._pending.get(message_id)
                if pending is None:
                    return self.last_status == 'ACKED'
                if pending['status'] == 'ACKED':
                    self.last_status = 'ACKED'
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    return True
                if pending['status'] == 'FAILED':
                    self.last_status = 'FAILED'
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    return False
                if retries >= self.max_retries:
                    pending['status'] = 'FAILED'
                    self.last_status = 'FAILED'
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    failed_callback = self.on_failed
                    break

                retries += 1
                self.retry_count = retries
                pending['retries'] = retries
                pending['env']['delivery_attempt'] = retries
                pending['status'] = 'RETRYING'
                self.last_status = 'RETRYING'
                destination = pending['destination']
                env = pending['env']
                address = destination if isinstance(destination, tuple) else (destination, 0)

            event.clear()
            self.transport.send(address, env)

        if failed_callback:
            failed_callback(message_id)
        return False

    def _on_transport_message(self, msg, addr):
        try:
            validate_envelope(msg)
        except Exception:
            return

        if msg.get('type') != 'ack':
            return

        ack_id = msg.get('payload', {}).get('message_id') or msg.get('message_id')
        if not ack_id:
            return

        with self._lock:
            pending = self._pending.get(ack_id)
            if pending is None:
                return
            pending['status'] = 'ACKED'
            event = self._pending_events.get(ack_id)

        self.last_status = 'ACKED'
        self.last_message_id = ack_id

        if event is not None:
            event.set()


class ReliableReceiver:
    """Receives data messages and emits ACKs while deduplicating application processing."""

    def __init__(
        self,
        node_id: str,
        transport,
        app_handler: Optional[Callable[[dict, tuple], None]] = None,
        *,
        auto_register: bool = True,
    ):
        self.node_id = node_id
        self.transport = transport
        self.app_handler = app_handler
        self.processed_message_ids: Set[str] = set()
        self._lock = threading.Lock()
        self._transport_handler = self._on_transport_message
        if auto_register:
            try:
                transport.register_handler(self._transport_handler)
            except Exception:
                pass

    def _on_transport_message(self, msg, addr):
        try:
            validate_envelope(msg)
        except Exception:
            return

        if msg.get('type') != 'data':
            return

        message_id = msg.get('message_id')
        if not message_id:
            return

        ack = create_envelope(
            'ack',
            source=self.node_id,
            destination=msg.get('source'),
            payload={'message_id': message_id},
            message_id=message_id,
        )

        with self._lock:
            is_duplicate = message_id in self.processed_message_ids
            if not is_duplicate:
                self.processed_message_ids.add(message_id)

        if is_duplicate:
            try:
                self.transport.send((addr[0], addr[1]), ack)
            except Exception:
                pass
            return

        payload = msg.get('payload', {})
        if self.app_handler:
            try:
                self.app_handler(msg, addr)
            except Exception:
                pass

        try:
            self.transport.send((addr[0], addr[1]), ack)
        except Exception:
            pass
