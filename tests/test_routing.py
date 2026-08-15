import time

from p2p.transport import MockTransport
from p2p.routing import RoutingTable
from p2p.router import Router
from p2p.protocol import create_envelope


def test_two_hop_forwarding():
    # Setup transports
    tA = MockTransport()
    tB = MockTransport()
    tC = MockTransport()
    tA.start(); tB.start(); tC.start()

    # Routing tables
    rA = RoutingTable()
    rB = RoutingTable()
    rC = RoutingTable()

    # A -> B -> C
    # A knows that to reach C, next_hop is B
    rA.add_route('C', 'B', '127.0.0.1', 10001)
    rB.add_route('C', 'C', '127.0.0.1', 10002)

    routerA = Router('A', tA, rA)
    routerB = Router('B', tB, rB)
    routerC = Router('C', tC, rC)

    # Register app handler on C
    received = []

    def app_handler(msg, addr):
        received.append(msg)

    routerC.add_app_handler(app_handler)

    # start routers (register handlers)
    routerA.start(); routerB.start(); routerC.start()

    # wire transports so tA sends are received by routerB, and tB sends by routerC
    tA.register_handler(routerB._on_transport_message)
    tB.register_handler(routerC._on_transport_message)

    # Create envelope at A destined to C and send via A's transport
    env = create_envelope('chat_message', source='A', payload={'text': 'hello'}, destination='C')
    tA.send(('127.0.0.1', 10001), env)

    time.sleep(0.05)
    assert len(received) == 1
    assert received[0]['destination'] == 'C'
    assert received[0]['hop_count'] >= 1


def test_three_hop_forwarding_and_route_failure():
    tA = MockTransport(); tB = MockTransport(); tC = MockTransport(); tD = MockTransport()
    tA.start(); tB.start(); tC.start(); tD.start()

    rA = RoutingTable(); rB = RoutingTable(); rC = RoutingTable(); rD = RoutingTable()

    # A -> B -> C -> D
    rA.add_route('D', 'B', '127.0.0.1', 20001)
    rB.add_route('D', 'C', '127.0.0.1', 20002)
    rC.add_route('D', 'D', '127.0.0.1', 20003)

    routerA = Router('A', tA, rA)
    routerB = Router('B', tB, rB)
    routerC = Router('C', tC, rC)
    routerD = Router('D', tD, rD)

    received = []
    routerD.add_app_handler(lambda m, a: received.append(m))

    routerA.start(); routerB.start(); routerC.start(); routerD.start()

    # wire transports: A->B, B->C, C->D
    tA.register_handler(routerB._on_transport_message)
    tB.register_handler(routerC._on_transport_message)
    tC.register_handler(routerD._on_transport_message)

    env = create_envelope('chat_message', source='A', payload={'text': 'chain'}, destination='D')
    tA.send(('127.0.0.1', 20001), env)
    time.sleep(0.05)
    assert len(received) == 1

    # Now mark B->C link as failed by setting route to DEAD in rB
    rB.set_status('D', 'DEAD')
    received.clear()
    env2 = create_envelope('chat_message', source='A', payload={'text': 'lost'}, destination='D')
    tA.send(('127.0.0.1', 20001), env2)
    time.sleep(0.05)
    # Should not be delivered because route via B is dead
    assert len(received) == 0
