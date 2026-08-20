"""Reliability layer for DisasterConnect.

Provides a minimal ACK-based delivery guarantee and duplicate protection for
application-level data messages. It intentionally sits above the routing layer
and does not decide route selection.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Dict, Optional, Set, Tuple

from p2p.protocol import create_envelope, validate_envelope
from p2p.log import get_logger

log = get_logger("p2p.reliability")


class PeerLinkMetrics:
    """Maintains real RTT (Exponential Moving Average) and retry rates per peer."""

    def __init__(self, peer_id: str):
        self.peer_id = peer_id
        self.rtt_ms: Optional[float] = None
        self.retry_rate: float = 0.0
        self.total_transmissions: int = 0
        self.retry_count: int = 0
        self.successful_acks: int = 0
        self.last_sample_time: float = 0.0
        self._history = deque(maxlen=20)

    def record_ack(self, rtt_sample_ms: float, retries: int):
        self.last_sample_time = time.time()
        self.successful_acks += 1
        self.total_transmissions += (1 + retries)
        self.retry_count += retries

        # RTT Exponential Moving Average (alpha = 0.2)
        if self.rtt_ms is None:
            self.rtt_ms = float(rtt_sample_ms)
        else:
            self.rtt_ms = (0.8 * self.rtt_ms) + (0.2 * float(rtt_sample_ms))

        # Sliding window for retry tracking
        self._history.append(1 if retries > 0 else 0)
        self.retry_rate = float(sum(self._history)) / float(len(self._history)) if self._history else 0.0

    def record_failure(self, retries: int):
        self.last_sample_time = time.time()
        self.total_transmissions += (1 + retries)
        self.retry_count += retries
        self._history.append(1)
        self.retry_rate = float(sum(self._history)) / float(len(self._history)) if self._history else 0.0


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
        on_late_ack: Optional[Callable[[str, str], None]] = None,
        security=None,
        address_resolver: Optional[Callable[[str], Tuple[str, int]]] = None,
    ):
        self.node_id = node_id
        self.transport = transport
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.on_failed = on_failed
        # Fired when an ACK arrives for a message_id we already gave up
        # waiting on (see `_abandoned` below). Signature: (message_id, destination).
        self.on_late_ack = on_late_ack
        self.security = security
        self.address_resolver = address_resolver
        self.peer_metrics: Dict[str, PeerLinkMetrics] = {}
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, object]] = {}
        self._pending_events: Dict[str, threading.Event] = {}
        # Messages whose synchronous wait exhausted retries and was reported
        # FAILED to the caller, but whose ACK may still legitimately arrive
        # afterwards (the destination *did* process it in time -- only our
        # local wait window was too short). Keyed by message_id -> {
        # 'destination': ..., 'abandoned_at': time.time()}. Bounded by
        # `_abandoned_ttl` so a permanently-unreachable destination doesn't
        # grow this dict without limit.
        self._abandoned: Dict[str, Dict[str, object]] = {}
        self._abandoned_ttl = 120.0
        self.last_status = 'CREATED'
        self.last_message_id: Optional[str] = None
        self.retry_count = 0
        try:
            self.transport.register_handler(self._on_transport_message)
        except Exception:
            pass

    def send(self, destination: str, payload: Dict[str, object], message_id: Optional[str] = None) -> bool:
        message_id = message_id or f"{self.node_id}:{int(time.time() * 1000000)}:{len(self._pending)}"
        env = self.prepare_envelope(destination, payload, message_id=message_id)
        return self.send_envelope(destination, env)

    def prepare_envelope(self, destination: str, payload: Dict[str, object], message_id: Optional[str] = None) -> Dict[str, object]:
        """Create an outbound envelope without transmitting it.

        Store-and-forward uses this to persist ciphertext rather than plaintext
        and later replay the exact authenticated envelope.
        """
        message_id = message_id or f"{self.node_id}:{int(time.time() * 1000000)}:{len(self._pending)}"
        env = create_envelope(
            'data',
            source=self.node_id,
            destination=destination,
            payload=payload,
            message_id=message_id,
        )
        env['delivery_attempt'] = 0
        if self.security:
            env = self.security.protect(env)
        return env

    def send_envelope(self, destination: str, env: Dict[str, object]) -> bool:
        """Transmit a previously prepared envelope, retaining its identity."""
        message_id = env.get('message_id')
        if not message_id:
            raise ValueError('Envelope must include message_id')
        self.last_message_id = message_id
        self.last_status = 'SENT'
        self.retry_count = 0
        event = threading.Event()

        with self._lock:
            self._pending[message_id] = {
                'destination': destination,
                'payload': env.get('payload', {}),
                'env': env,
                'retries': 0,
                'start_time': time.time(),
                'deadline': time.time() + self.timeout,
                'status': 'SENT',
            }
            self._pending_events[message_id] = event

        address = self._address_for(destination)
        try:
            self.transport.send(address, env)
        except Exception:
            with self._lock:
                self._pending.pop(message_id, None)
                self._pending_events.pop(message_id, None)
                self.last_status = 'FAILED'
            self._record_peer_failure(destination, 0)
            return False
        return self._wait_for_ack(message_id, event)

    def _wait_for_ack(self, message_id: str, event: threading.Event) -> bool:
        retries = 0
        failed_callback = None
        failed_dest = None

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
                    failed_dest = pending['destination']
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    self._remember_abandoned_locked(message_id, pending['destination'])
                    failed_callback = self.on_failed
                    log.warning(f"message {message_id} to {pending['destination']} failed after {retries} retries", extra={"node_id": self.node_id, "dest": pending['destination'], "message_id": message_id})
                    break

                retries += 1
                self.retry_count = retries
                pending['retries'] = retries
                pending['env']['delivery_attempt'] = retries
                pending['status'] = 'RETRYING'
                self.last_status = 'RETRYING'
                log.debug(f"retrying message {message_id} to {pending['destination']} (attempt {retries})", extra={"node_id": self.node_id, "dest": pending['destination'], "message_id": message_id, "attempt": retries})
                destination = pending['destination']
                env = pending['env']
                address = self._address_for(destination)

            event.clear()
            try:
                self.transport.send(address, env)
            except Exception:
                with self._lock:
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    self.last_status = 'FAILED'
                self._record_peer_failure(destination, retries)
                return False

        if failed_dest:
            self._record_peer_failure(failed_dest, retries)

        if failed_callback:
            failed_callback(message_id)
        return False

    def _record_peer_ack(self, destination: str, rtt_ms: float, retries: int):
        with self._lock:
            if destination not in self.peer_metrics:
                self.peer_metrics[destination] = PeerLinkMetrics(destination)
            self.peer_metrics[destination].record_ack(rtt_ms, retries)

    def _record_peer_failure(self, destination: str, retries: int):
        with self._lock:
            if destination not in self.peer_metrics:
                self.peer_metrics[destination] = PeerLinkMetrics(destination)
            self.peer_metrics[destination].record_failure(retries)

    def get_peer_link_metrics(self, peer_id: str) -> Optional[PeerLinkMetrics]:
        """Return the real RTT and retry metrics for a given peer if available."""
        with self._lock:
            return self.peer_metrics.get(peer_id)

    def _remember_abandoned_locked(self, message_id: str, destination: str) -> None:
        """Record a message we gave up waiting on. Caller must hold `self._lock`."""
        now = time.time()
        if self._abandoned:
            cutoff = now - self._abandoned_ttl
            expired = [mid for mid, info in self._abandoned.items() if info['abandoned_at'] < cutoff]
            for mid in expired:
                self._abandoned.pop(mid, None)
        self._abandoned[message_id] = {'destination': destination, 'abandoned_at': now}

    def _address_for(self, destination):
        if isinstance(destination, tuple):
            return destination
        if self.address_resolver:
            return self.address_resolver(destination)
        return (destination, 0)

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

        late_ack_destination = None
        dest = None
        rtt_sample = 0.0
        retries = 0
        with self._lock:
            pending = self._pending.get(ack_id)
            if pending is None:
                abandoned = self._abandoned.pop(ack_id, None)
                if abandoned is not None:
                    late_ack_destination = abandoned['destination']
                event = None
            else:
                pending['status'] = 'ACKED'
                dest = pending.get('destination')
                start_t = pending.get('start_time', time.time())
                rtt_sample = max(0.0, (time.time() - start_t) * 1000.0)
                retries = pending.get('retries', 0)
                event = self._pending_events.get(ack_id)

        if dest is not None:
            self._record_peer_ack(dest, rtt_sample, retries)

        if pending is None:
            # Either a genuinely unknown/stale ack (nothing to do), or a late
            # ack for a message we already gave up on. In the latter case the
            # destination really did receive and process it -- tell whoever
            # is tracking delivery state (e.g. StoreForwardManager) so it can
            # mark the message delivered without waiting for another replay.
            if late_ack_destination is not None and self.on_late_ack:
                try:
                    self.on_late_ack(ack_id, late_ack_destination)
                except Exception:
                    pass
            return

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
        security=None,
        ack_sender: Optional[Callable[[dict, str], None]] = None,
        message_archiver: Optional[Callable[[str, dict], None]] = None,
    ):
        self.node_id = node_id
        self.transport = transport
        self.app_handler = app_handler
        self.security = security
        self.ack_sender = ack_sender
        self.message_archiver = message_archiver
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

        if msg.get('destination') != self.node_id:
            return

        original_msg = dict(msg) # Copy before security.open potentially modifies it

        if getattr(self, 'security', None):
            try:
                # Reliability owns duplicate delivery and must still ACK a retry.
                # SecurityContext's direct API retains strict replay rejection.
                msg = self.security.open(msg, reject_replay=False)
            except Exception:
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

        new_msg = False
        with self._lock:
            is_duplicate = message_id in self.processed_message_ids
            if not is_duplicate:
                self.processed_message_ids.add(message_id)
                new_msg = True

        if new_msg and self.message_archiver:
            try:
                self.message_archiver(message_id, original_msg)
            except Exception:
                pass

        if is_duplicate:
            self._send_ack(ack, addr)
            return

        if self.app_handler:
            try:
                self.app_handler(msg, addr)
            except Exception:
                pass

        self._send_ack(ack, addr)

    def _send_ack(self, ack, addr):
        try:
            if self.ack_sender:
                self.ack_sender(ack, ack.get('destination'))
            else:
                self.transport.send((addr[0], addr[1]), ack)
        except Exception:
            pass
