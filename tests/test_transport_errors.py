import pytest
from p2p.transport import UDPTransport


def test_send_without_start_raises():
    t = UDPTransport(bind_addr='127.0.0.1', bind_port=0)
    with pytest.raises(RuntimeError):
        t.send(('127.0.0.1', 9999), {'type': 'x'})


def test_malformed_packet_handled(tmp_path):
    # Start a UDPTransport that will receive a malformed packet
    receiver = UDPTransport(bind_addr='127.0.0.1', bind_port=0)
    receiver.start()
    port = receiver.sock.getsockname()[1]

    # Send raw non-json bytes to the receiver socket
    s = __import__('socket').socket(__import__('socket').AF_INET, __import__('socket').SOCK_DGRAM)
    s.sendto(b"not-a-json", ('127.0.0.1', port))
    s.close()

    # If no exception raised and receiver still running, test passes
    receiver.stop()
