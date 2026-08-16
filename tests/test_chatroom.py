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
