import pytest
from p2p.transport import MockTransport


def test_mock_transport_send_and_handler_called():
    mt = MockTransport()
    received = []

    def handler(msg, addr):
        received.append((msg, addr))

    mt.register_handler(handler)
    mt.start()

    mt.send(('127.0.0.1', 12345), {'type': 'test', 'data': 'x'})

    assert len(received) == 1
    assert received[0][0]['type'] == 'test'
    assert received[0][1][0] == '127.0.0.1'

    mt.stop()
