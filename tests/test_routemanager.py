import time

from p2p.transport import MockTransport
from p2p.peermanager import PeerManager
from p2p.routing import RoutingTable
from p2p.routemanager import RouteLearner


def test_route_learned_from_peer():
    tA = MockTransport(); tB = MockTransport(); tC = MockTransport()
    tA.start(); tB.start(); tC.start()

    pA = PeerManager('A'); pB = PeerManager('B'); pC = PeerManager('C')
    rA = RoutingTable(); rB = RoutingTable(); rC = RoutingTable()

    # establish peer adjacencies: A<->B, B<->C
    pA.update_peer('B', '127.0.0.1', 100)
    pB.update_peer('A', '127.0.0.1', 101)
    pB.update_peer('C', '127.0.0.1', 102)
    pC.update_peer('B', '127.0.0.1', 103)

    rlA = RouteLearner('A', pA, rA, tA)
    rlB = RouteLearner('B', pB, rB, tB)
    rlC = RouteLearner('C', pC, rC, tC)

    # wire B's transport to deliver announcements to A and C
    tB.register_handler(rlA._on_transport_message)
    tB.register_handler(rlC._on_transport_message)

    # start B and have it announce
    rlB.start()
    time.sleep(0.05)

    # A should have learned routes to C via B
    nh = rA.get_next_hop('C')
    assert nh is not None
    next_hop, ip, port = nh
    assert next_hop == 'B'


def test_route_removed_when_peer_dead_and_recovered():
    tA = MockTransport(); tB = MockTransport(); tC = MockTransport()
    tA.start(); tB.start(); tC.start()

    pA = PeerManager('A'); pB = PeerManager('B'); pC = PeerManager('C')
    rA = RoutingTable(); rB = RoutingTable(); rC = RoutingTable()

    pA.update_peer('B', '127.0.0.1', 100)
    pB.update_peer('A', '127.0.0.1', 101)
    pB.update_peer('C', '127.0.0.1', 102)
    pC.update_peer('B', '127.0.0.1', 103)

    rlA = RouteLearner('A', pA, rA, tA)
    rlB = RouteLearner('B', pB, rB, tB)
    rlC = RouteLearner('C', pC, rC, tC)

    tB.register_handler(rlA._on_transport_message)
    tB.register_handler(rlC._on_transport_message)

    rlB.start()
    time.sleep(0.05)

    # learned
    assert rA.get_next_hop('C') is not None

    # simulate B lost on A
    rlA._on_peer_lost('B')
    assert rA.get_next_hop('C') is None

    # simulate B returns and announces again
    # re-add peers and announce
    pA.update_peer('B', '127.0.0.1', 100)
    rlB.start()
    time.sleep(0.05)
    assert rA.get_next_hop('C') is not None
