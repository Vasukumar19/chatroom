from p2p.routing import RoutingTable
from p2p.route_manager import RouteManager
from p2p.router import Router
from p2p.transport import MockTransport


def test_dead_next_hop_invalidates_route():
    rt = RoutingTable()
    rt.add_route('C', 'B', '1.1.1.1', 100, metric=1)
    rm = RouteManager(rt)
    assert rt.get_next_hop('C')[0] == 'B'
    rm.on_peer_status_change('B', 'DEAD')
    assert rt.get_next_hop('C') is None


def test_best_route_selected_and_fallback():
    rt = RoutingTable()
    # B has lower metric than D
    rt.add_route('C', 'B', '1.1.1.1', 100, metric=1)
    rt.add_route('C', 'D', '2.2.2.2', 200, metric=2)
    assert rt.get_next_hop('C')[0] == 'B'
    rm = RouteManager(rt)
    rm.on_peer_status_change('B', 'DEAD')
    nh = rt.get_next_hop('C')
    assert nh is not None and nh[0] == 'D'


def test_route_recovers_after_peer_recovery():
    rt = RoutingTable()
    rt.add_route('C', 'B', '1.1.1.1', 100, metric=1)
    rt.add_route('C', 'D', '2.2.2.2', 200, metric=2)
    rm = RouteManager(rt)
    rm.on_peer_status_change('B', 'DEAD')
    assert rt.get_next_hop('C')[0] == 'D'
    rm.on_peer_status_change('B', 'ALIVE')
    assert rt.get_next_hop('C')[0] == 'B'


def test_router_handles_no_available_route():
    t = MockTransport(); t.start()
    rt = RoutingTable()
    router = Router('A', t, rt)
    try:
        router.send('X', {'x': 'y'})
        assert False, 'Expected RuntimeError'
    except RuntimeError:
        pass
