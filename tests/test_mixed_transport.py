import time

from p2p.transport import UDPTransport, MockTransport
from p2p.routing import RoutingTable
from p2p.router import Router
from p2p.protocol import create_envelope


def test_udp_to_mock_multi_hop():
    # A (UDP) -> B (UDP receive, Mock send) -> C (Mock receive)
    tA = UDPTransport(bind_addr='127.0.0.1', bind_port=0, timeout=0.5)
    tB_udp = UDPTransport(bind_addr='127.0.0.1', bind_port=0, timeout=0.5)
    tB_mock = MockTransport()
    tC_mock = MockTransport()

    tA.start(); tB_udp.start(); tB_mock.start(); tC_mock.start()

    pA = RoutingTable(); pB = RoutingTable(); pC = RoutingTable()

    # A routes to C via B (use B's UDP port as next hop)
    b_udp_port = tB_udp.sock.getsockname()[1]
    pA.add_route('C', 'B', '127.0.0.1', b_udp_port)
    # B routes to C directly via Mock (ip/port are not used by MockTransport but kept)
    pB.add_route('C', 'C', '127.0.0.1', 9999)

    # C has local route
    pC.add_route('C', 'C', '127.0.0.1', 0)

    routerA = Router('A', tA, pA)
    # routerB will receive on tB_udp (registered manually), but send via tB_mock
    routerB = Router('B', tB_mock, pB)
    routerC = Router('C', tC_mock, pC)

    received = []
    routerC.add_app_handler(lambda m, a: received.append(m))

    # start routers (register handlers on their send-transport)
    routerA.start(); routerB.start(); routerC.start()

    # wire B's UDP receive to routerB
    received_b = []
    def capture_b(msg, addr):
        received_b.append(msg)
    tB_udp.register_handler(capture_b)
    tB_udp.register_handler(routerB._on_transport_message)
    # wire tB_mock sends to routerC
    sent_by_b = []
    def capture_sent_b(m, a):
        sent_by_b.append((m, a))
    tB_mock.register_handler(capture_sent_b)
    tB_mock.register_handler(routerC._on_transport_message)

    # Now send from A to C via Router A
    routerA.send('C', {'text': 'hello'}, msg_type='chat_message')

    time.sleep(0.5)
    # ensure B received the UDP packet
    assert len(received_b) >= 1
    # ensure envelope looks versioned
    first = received_b[0]
    assert isinstance(first, dict)
    assert 'message_id' in first and 'type' in first and 'source' in first
    assert first.get('destination') == 'C'
    # ensure B attempted to forward via MockTransport
    assert len(sent_by_b) >= 1
    assert len(received) == 1
    msg = received[0]
    assert msg['source'] == 'A'
    assert msg['destination'] == 'C'
    assert msg['message_id']
    assert msg['hop_count'] >= 1
    assert msg['ttl'] < 8

    # Duplicate suppression: send same message_id again through A's transport
    mid = msg['message_id']
    env_dup = msg.copy()
    tA.send(( '127.0.0.1', b_udp_port), env_dup)
    time.sleep(0.2)
    # still only one delivered
    assert len(received) == 1

    tA.stop(); tB_udp.stop(); tB_mock.stop(); tC_mock.stop()
