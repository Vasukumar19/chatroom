import pytest
from p2p.chatroom import ChatMessage, ChatRoom


class DummyHost:
    def __init__(self):
        self.handlers = []

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def broadcast_message(self, message):
        # Simulate at least one successful send
        # Also simulate immediate delivery to registered handlers for local testing
        for h in list(self.handlers):
            try:
                h(message)
            except Exception:
                pass
        return 1

    def get_peer_count(self):
        return 0


def test_chatmessage_defaults():
    m = ChatMessage(Message='hello', SenderID='A', SenderNick='Alice')
    assert m.Message == 'hello'
    assert m.SenderID == 'A'
    assert m.SenderNick == 'Alice'
    assert m.MessageID is not None
    assert m.Timestamp is not None


def test_chatroom_publish_and_receive():
    host = DummyHost()
    room = ChatRoom('testroom', 'Me', 'me-id', host)

    # Publish a message
    ok = room.publish('hey there')
    assert ok is True

    msgs = room.get_raw_messages()
    assert len(msgs) == 1
    assert msgs[0]['Message'] == 'hey there'
