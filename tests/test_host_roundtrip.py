import time
from p2p.transport import UDPTransport
from p2p.host import P2PHost
from p2p import protocol


def test_host_envelope_roundtrip():
    t1 = UDPTransport(bind_addr='127.0.0.1', bind_port=0)
    t2 = UDPTransport(bind_addr='127.0.0.1', bind_port=0)
    t1.start()
    t2.start()

    p1 = t1.sock.getsockname()[1]
    p2 = t2.sock.getsockname()[1]

    # Create hosts but do not auto-start via create_host helper
    hA = P2PHost(port=5001)
    hB = P2PHost(port=5002)

    # Set transports before starting so handlers are registered
    hA.set_transport(t1)
    hB.set_transport(t2)

    hA.start()
    hB.start()

    received = []

    def handler(msg):
        received.append(msg)

    hB.add_message_handler(handler)

    env = protocol.create_envelope('chat_message', source='A', payload={'text': 'hello'}, ttl=5)

    # Send from A to B directly using transport
    hA.transport.send(('127.0.0.1', p2), env)

    time.sleep(0.5)

    assert len(received) >= 1
    assert received[0]['type'] == 'chat_message'

    hA.stop()
    hB.stop()
    t1.stop()
    t2.stop()
