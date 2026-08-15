import pytest
import time

from p2p.transport import MockTransport
from p2p.routing import RoutingTable
from p2p.routemanager import RouteLearner
from p2p.route_manager import RouteManager
from p2p.router import Router
from p2p.peermanager import PeerManager


def make_node(node_id):
    transport = MockTransport()
    rt = RoutingTable()
    pm = PeerManager(node_id)
    learner = RouteLearner(node_id, pm, rt, transport)
    router = Router(node_id, transport, rt)
    # start handlers
    try:
        learner.start()
    except Exception:
        pass
    try:
        router.start()
    except Exception:
        pass
    return {'id': node_id, 'transport': transport, 'rt': rt, 'pm': pm, 'learner': learner, 'router': router}


def test_better_route_selection():
    A = make_node('A')
    # Add two candidate next hops to A's routing table
    A['rt'].add_route('D', 'B', '127.0.0.1', 10001, metric=3, hops=3)
    A['rt'].add_route('D', 'C', '127.0.0.1', 10002, metric=2, hops=2)

    nh = A['rt'].get_next_hop('D')
    assert nh[0] == 'C'


def test_direct_route_preference():
    A = make_node('A')
    # Direct route to D (self) and via B
    A['rt'].add_route('D', 'D', '127.0.0.1', 10003, metric=1, hops=1)
    A['rt'].add_route('D', 'B', '127.0.0.1', 10001, metric=3, hops=3)

    nh = A['rt'].get_next_hop('D')
    # prefer direct
    assert nh[0] == 'D'


def test_failure_recovery_and_restore():
    # Build A, B, C nodes; D is destination
    A = make_node('A')
    B = make_node('B')
    C = make_node('C')

    # A knows D via B (best), but also has via C as fallback
    A['rt'].add_route('D', 'B', '127.0.0.1', 10001, metric=1, hops=1)
    A['rt'].add_route('D', 'C', '127.0.0.1', 10002, metric=2, hops=2)

    rm = RouteManager(A['rt'])

    # simulate B failing
    rm.on_peer_status_change('B', 'DEAD')
    # now A should pick C
    nh = A['rt'].get_next_hop('D')
    assert nh[0] == 'C'

    # B recovers
    rm.on_peer_status_change('B', 'ALIVE')
    # A should return to B (metric 1 better)
    nh2 = A['rt'].get_next_hop('D')
    assert nh2[0] == 'B'


def test_loop_prevention_no_self_route():
    A = make_node('A')
    B = make_node('B')

    # B advertises route to A (destination == A) should be ignored
    # craft advertisement envelope
    env = create_route_ad(env_src='B', routes={'A': 2})
    # simulate incoming at A
    A['transport'].simulate_incoming(env, ('127.0.0.1', 10001))

    # A must not add route to itself
    assert A['rt'].get_next_hop('A') is None


def create_route_ad(env_src: str, routes: dict):
    # lightweight envelope generator matching protocol expectations
    from p2p.protocol import create_envelope
    return create_envelope('route_advertisement', source=env_src, payload={'routes': routes})


def test_duplicate_advertisement_suppression():
    A = make_node('A')
    B = make_node('B')

    # B has route to D
    B['rt'].add_route('D', 'D', '127.0.0.1', 10003, metric=1, hops=1)
    # B advertises multiple times
    B['learner'].send_route_advertisement(('127.0.0.1', 10000))
    env = B['transport'].last_sent
    A['transport'].simulate_incoming(env, ('127.0.0.1', 10000))
    # duplicate
    A['transport'].simulate_incoming(env, ('127.0.0.1', 10000))

    routes = A['rt'].list_routes()
    assert 'D' in routes
    # only one next_hop entry for D
    assert len(routes['D']) == 1


def test_end_to_end_forwarding():
    # A -> B -> C -> D forwarding of a data message
    A = make_node('A')
    B = make_node('B')
    C = make_node('C')
    D = make_node('D')

    # Build routing entries accordingly
    # A -> B
    A['rt'].add_route('D', 'B', '127.0.0.1', 10001, metric=1, hops=1)
    # B -> C
    B['rt'].add_route('D', 'C', '127.0.0.1', 10002, metric=1, hops=1)
    # C -> D
    C['rt'].add_route('D', 'D', '127.0.0.1', 10003, metric=1, hops=1)

    received = []

    def app_handler(msg, addr):
        received.append((msg, addr))

    D['router'].add_app_handler(app_handler)

    # A sends message to D; simulate hop-by-hop using transports
    A['router'].send('D', {'hello': 'world'})
    # simulate B receives A's sent envelope
    env1 = A['transport'].last_sent
    B['transport'].simulate_incoming(env1, ('127.0.0.1', 10001))
    # simulate C receives B's forwarded envelope
    env2 = B['transport'].last_sent
    C['transport'].simulate_incoming(env2, ('127.0.0.1', 10002))
    # simulate D receives C's forwarded envelope
    env3 = C['transport'].last_sent
    D['transport'].simulate_incoming(env3, ('127.0.0.1', 10003))

    assert len(received) == 1
    assert received[0][0]['payload'] == {'hello': 'world'}
