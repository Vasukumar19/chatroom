"""Regression tests for ISSUE 1 (ACK convergence / slow queue completion).

Root cause (see reliability.py): ReliableSender._wait_for_ack() gives up on a
message after `timeout * (max_retries + 1)` and immediately discards its
bookkeeping (`_pending` / `_pending_events`). If the destination genuinely
received, processed, and ACKed the message, but that ACK simply took longer
than the local wait window to arrive (e.g. due to per-message TCP connection
setup cost), the ACK was previously matched against nothing and silently
dropped -- `_on_transport_message` returned early because `self._pending.get
(ack_id)` was already `None`. StoreForwardManager.replay() would then leave
the message QUEUED, waiting for a *future* discovery-triggered replay
attempt to happen to complete within its own short window, which is what
produced multi-second-to-multi-minute convergence delays in live testing.

These tests use a transport that can *defer* ACK delivery independently of
data delivery, to deterministically reproduce "ACK arrives after the sender
already gave up" without relying on real timing/flakiness.
"""
from __future__ import annotations

from p2p.reliability import ReliableSender, ReliableReceiver
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.transport import MockTransport


class DeferredAckTransport(MockTransport):
    """Delivers data messages synchronously (like MockTransport) but holds
    outbound ACKs until `flush_acks()` is called, simulating an ACK that is
    genuinely in flight and arrives later than the sender's wait window."""

    def __init__(self):
        super().__init__()
        self.deferred_acks = []

    def send(self, address, message):
        if message.get('type') == 'ack':
            self.deferred_acks.append((address, message))
            return
        super().send(address, message)

    def flush_acks(self):
        pending = self.deferred_acks
        self.deferred_acks = []
        for address, message in pending:
            for h in list(self.handlers):
                try:
                    h(message, address)
                except Exception:
                    pass


def test_application_delivery_happens_even_though_ack_is_late():
    """Sanity check the premise: app delivery is never gated on the ACK
    reaching the sender -- it happens on the receiver's side regardless."""
    transport = DeferredAckTransport()
    processed = []
    ReliableReceiver('B', transport, lambda msg, addr: processed.append(msg['payload']))
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1)

    ok = sender.send('B', {'value': 'slow-ack'})

    assert ok is False  # ACK semantics unchanged: sender correctly times out
    assert sender.last_status == 'FAILED'
    assert processed == [{'value': 'slow-ack'}]  # but the app already got it


def test_late_ack_fires_on_late_ack_callback_not_silently_dropped():
    transport = DeferredAckTransport()
    ReliableReceiver('B', transport, lambda msg, addr: None)
    late_acks = []
    sender = ReliableSender(
        'A', transport, timeout=0.01, max_retries=1,
        on_late_ack=lambda mid, dest: late_acks.append((mid, dest)),
    )

    ok = sender.send('B', {'value': 'x'})
    assert ok is False
    assert late_acks == []  # ack hasn't arrived yet

    transport.flush_acks()

    assert late_acks == [(sender.last_message_id, 'B')]


def test_unrelated_ack_still_ignored_after_late_ack_support_added():
    """Regression guard: a truly unknown message_id (never sent, never
    abandoned) must still be silently ignored -- on_late_ack must only fire
    for messages this sender actually gave up on."""
    transport = MockTransport()
    late_acks = []
    sender = ReliableSender(
        'A', transport, timeout=0.01, max_retries=1,
        on_late_ack=lambda mid, dest: late_acks.append((mid, dest)),
    )

    from p2p.protocol import create_envelope
    ack = create_envelope('ack', source='B', destination='A', payload={'message_id': 'ghost'}, message_id='ghost')
    sender._on_transport_message(ack, ('127.0.0.1', 9001))

    assert late_acks == []
    assert sender.last_status != 'ACKED'


def test_store_forward_manager_reconciles_late_ack_to_delivered():
    """End-to-end: a message that StoreForwardManager queued because its
    synchronous send timed out must still converge to DELIVERED as soon as
    the real ACK arrives, without needing another replay() cycle."""
    transport = DeferredAckTransport()
    ReliableReceiver('B', transport, lambda msg, addr: None)
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1)
    queue = StoreForwardQueue()  # in-memory
    manager = StoreForwardManager(queue=queue, reliable_sender=sender)

    result = manager.send('B', {'value': 'x'})

    # The synchronous attempt failed (ack deferred) so it was queued -- this
    # is the exact state that was observed staying QUEUED for a long time.
    assert result.status == 'QUEUED'
    assert [m.message_id for m in queue.get_pending(destination='B')] == [result.message_id]

    # The ACK that was genuinely in flight now arrives.
    transport.flush_acks()

    assert queue.get_pending(destination='B') == []
    assert queue.pending_count(destination='B') == 0


def test_abandoned_entries_are_pruned_by_ttl():
    """The abandoned-message bookkeeping must not grow without bound for a
    destination that never sends an ACK back at all."""
    transport = MockTransport()  # no receiver registered -> nothing ever ACKs
    sender = ReliableSender('A', transport, timeout=0.01, max_retries=1)
    sender._abandoned_ttl = 0.0  # force immediate expiry for a deterministic test

    sender.send('B', {'value': 'never-acked-1'})
    assert len(sender._abandoned) == 1

    sender.send('B', {'value': 'never-acked-2'})
    # Inserting the second entry should have pruned the first (ttl=0 means
    # anything already present is immediately eligible for expiry).
    assert len(sender._abandoned) == 1
