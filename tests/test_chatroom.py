import pytest
from p2p.chatroom import ChatMessage, ChatRoom
from p2p.store_forward_manager import DeliveryResult


class DummyHost:
    def __init__(self, peers=None):
        self.handlers = []
        self.peers = list(peers or ['peer-1'])

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def broadcast_message(self, message):
        for h in list(self.handlers):
            try:
                h(message)
            except Exception:
                pass
        return 1

    def get_peers(self):
        return list(self.peers)

    def get_peer_count(self):
        return len(self.peers)


class DummyDeliveryManager:
    def __init__(self):
        self.sent = []

    def send(self, destination, payload):
        self.sent.append((destination, payload))
        return DeliveryResult(
            status="DELIVERED",
            message_id=payload["data"]["MessageID"],
        )


def test_chatmessage_defaults():
    m = ChatMessage(Message='hello', SenderID='A', SenderNick='Alice')
    assert m.Message == 'hello'
    assert m.SenderID == 'A'
    assert m.SenderNick == 'Alice'
    assert m.MessageID is not None
    assert m.Timestamp is not None


def test_chatroom_publish_and_receive():
    host = DummyHost()
    manager = DummyDeliveryManager()
    room = ChatRoom('testroom', 'Me', 'me-id', host, delivery_manager=manager)

    ok = room.publish('hey there')
    assert ok is True

    msgs = room.get_raw_messages()
    assert len(msgs) == 1
    assert msgs[0]['Message'] == 'hey there'
    assert len(manager.sent) == 1
    assert manager.sent[0][0] == 'peer-1'
    assert manager.sent[0][1]['data']['Message'] == 'hey there'
    # Case: delivered immediately to a known, reachable peer.
    assert room.last_publish_status == 'DELIVERED'


class QueuingDeliveryManager:
    """Simulates a known peer that is currently unreachable: every send is
    persisted for later replay rather than delivered immediately."""

    def __init__(self):
        self.sent = []

    def send(self, destination, payload):
        self.sent.append((destination, payload))
        return DeliveryResult(
            status="QUEUED",
            message_id=payload["data"]["MessageID"],
            error="send failed; queued",
        )


def test_publish_case_a_known_peer_offline_reports_queued():
    """ISSUE 3, Case A: a known peer exists but is currently unreachable.
    publish() must keep returning True (unchanged contract -- queued still
    counts as success), but last_publish_status must say QUEUED so callers
    can report this accurately instead of claiming an unqualified "sent"."""
    host = DummyHost(peers=['peer-offline'])
    manager = QueuingDeliveryManager()
    room = ChatRoom('testroom', 'Me', 'me-id', host, delivery_manager=manager)

    ok = room.publish('OFFLINE-001')

    assert ok is True
    assert room.last_publish_status == 'QUEUED'
    assert manager.sent[0][0] == 'peer-offline'


class EmptyHost:
    """A node that has never discovered/known any peer at all (Case B),
    distinct from DummyHost whose `peers or [...]` fallback would otherwise
    mask an empty peer list."""

    def get_peers(self):
        return {}

    def get_peer_count(self):
        return 0


def test_publish_case_b_no_known_peer_reports_no_known_peers():
    """ISSUE 3, Case B: there has never been a known peer. publish() must
    keep returning True (nothing failed), but last_publish_status must be
    NO_KNOWN_PEERS -- distinct from QUEUED -- so callers do not claim a
    message was queued for reliable delivery when there is no destination
    for it to be queued against."""
    host = EmptyHost()
    manager = DummyDeliveryManager()
    room = ChatRoom('testroom', 'Me', 'me-id', host, delivery_manager=manager)

    ok = room.publish('hello, anybody?')

    assert ok is True
    assert room.last_publish_status == 'NO_KNOWN_PEERS'
    # Case B must never be confused with Case A: nothing should have been
    # handed to the delivery manager at all, since there is no destination.
    assert manager.sent == []


def test_publish_case_a_and_case_b_are_distinguishable():
    """Direct comparison: the two cases must not produce the same status,
    which is the actual bug being fixed (both used to just look like
    `publish() == True` with no way to tell them apart)."""
    manager_a = QueuingDeliveryManager()
    room_a = ChatRoom('room', 'Me', 'me-id', DummyHost(peers=['peer-offline']), delivery_manager=manager_a)
    room_a.publish('a')

    manager_b = DummyDeliveryManager()
    room_b = ChatRoom('room', 'Me', 'me-id', EmptyHost(), delivery_manager=manager_b)
    room_b.publish('b')

    assert room_a.last_publish_status != room_b.last_publish_status
    assert room_a.last_publish_status == 'QUEUED'
    assert room_b.last_publish_status == 'NO_KNOWN_PEERS'
