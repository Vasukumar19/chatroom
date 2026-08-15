import time

from p2p.transport import MockTransport
from p2p.routing import RoutingTable
from p2p.routemanager import RouteLearner
from p2p.route_manager import RouteManager
from p2p.peermanager import PeerManager


def make_components(node_id, min_advert_interval=0.2, stabilization_delay=0.3):
    transport = MockTransport()
    pm = PeerManager(node_id)
    rt = RoutingTable()
    learner = RouteLearner(node_id, pm, rt, transport, min_advert_interval=min_advert_interval)
    rm = RouteManager(rt, learner, stabilization_delay=stabilization_delay)
    # start learner to register handlers
    try:
        learner.start()
    except Exception:
        pass
    return transport, pm, rt, learner, rm


def test_route_change_debounce():
    transport, pm, rt, learner, rm = make_components('A')
    # initial route
    rt.add_route('D', 'B', '127.0.0.1', 10001, metric=1)

    # B transient DEAD then ALIVE quickly
    rm.on_peer_status_change('B', 'DEAD')
    time.sleep(0.05)
    rm.on_peer_status_change('B', 'ALIVE')
    # wait longer than stabilization_delay to ensure any timers would have fired
    time.sleep(0.5)

    # route should remain ACTIVE
    routes = rt.list_routes()
    assert routes['D']['B'][3] == 'ACTIVE'


def test_flapping_does_not_churn_routes():
    transport, pm, rt, learner, rm = make_components('A', min_advert_interval=0.1, stabilization_delay=0.1)
    # add route
    rt.add_route('D', 'B', '127.0.0.1', 10001, metric=1)

    # attach peer for learner to have a target
    pm.update_peer('B', '127.0.0.1', 10001)

    # rapid flapping
    for _ in range(5):
        rm.on_peer_status_change('B', 'DEAD')
        time.sleep(0.02)
        rm.on_peer_status_change('B', 'ALIVE')
        time.sleep(0.02)

    # give some time for coalesced adverts to send
    time.sleep(0.5)

    # transport.sent_history should not contain a large number of adverts
    adverts = [s for s in transport.sent_history if s[1].get('type') == 'route_advertisement']
    assert len(adverts) <= 3


def test_advertisement_rate_limit_and_suppression():
    transport, pm, rt, learner, rm = make_components('A', min_advert_interval=0.5, stabilization_delay=0.0)
    pm.update_peer('B', '127.0.0.1', 10001)
    # force multiple requests
    learner.request_advertisement()
    learner.request_advertisement()
    learner.request_advertisement()
    time.sleep(0.2)
    # since min_advert_interval=0.5, at most 1 send should have occurred quickly
    adverts = [s for s in transport.sent_history if s[1].get('type') == 'route_advertisement']
    assert len(adverts) <= 1


def test_identical_advertisement_suppressed():
    transport, pm, rt, learner, rm = make_components('A', min_advert_interval=0.2, stabilization_delay=0.0)
    pm.update_peer('B', '127.0.0.1', 10001)
    # no routes yet => advert empty
    learner.request_advertisement()
    time.sleep(0.05)
    learner.request_advertisement()
    time.sleep(0.2)
    adverts = [s for s in transport.sent_history if s[1].get('type') == 'route_advertisement']
    # multiple identical requests should coalesce to a single advert
    assert len(adverts) == 1


def test_route_stabilizes_after_sustained_failure_and_recovers():
    transport, pm, rt, learner, rm = make_components('A', min_advert_interval=0.1, stabilization_delay=0.2)
    rt.add_route('D', 'B', '127.0.0.1', 10001, metric=1)
    pm.update_peer('B', '127.0.0.1', 10001)

    # B goes DEAD and stays dead
    rm.on_peer_status_change('B', 'DEAD')
    time.sleep(0.3)
    # route should be INVALID now
    assert rt.list_routes()['D']['B'][3] == 'INVALID'

    # B recovers; recovery is also stabilized before route becomes ACTIVE again
    rm.on_peer_status_change('B', 'ALIVE')
    time.sleep(0.3)
    assert rt.list_routes()['D']['B'][3] == 'ACTIVE'
