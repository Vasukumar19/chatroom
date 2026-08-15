from p2p.transport import MockTransport
from p2p.peermanager import PeerManager
from p2p.routing import RoutingTable
from p2p.heartbeat import HeartbeatManager, ALIVE, SUSPECT, DEAD
from p2p.route_manager import RouteManager
from p2p.router import Router


def test_heartbeat_route_activation_and_invalidation():
    t = MockTransport(); t.start()
    pm = PeerManager('A')
    rt = RoutingTable()
    # route to C via B
    rt.add_route('C', 'B', '1.1.1.1', 100, metric=1)

    rm = RouteManager(rt)
    hb = HeartbeatManager('A', pm, t)
    rm.attach_to_heartbeat(hb)

    # simulate heartbeat from B -> sets ALIVE
    hb._on_transport_message({'type': 'heartbeat', 'source': 'B', 'message_id': 'm1', 'timestamp': 't', 'ttl': 1, 'payload': {}}, ('1.1.1.1', 100))
    # route should be ACTIVE
    nh = rt.get_next_hop('C')
    assert nh is not None and nh[0] == 'B'

    # set SUSPECT
    hb._set_status('B', SUSPECT)
    # route still available (SUSPECT)
    nh2 = rt.get_next_hop('C')
    assert nh2 is not None and nh2[0] == 'B'

    # set DEAD
    hb._set_status('B', DEAD)
    # route invalidated
    assert rt.get_next_hop('C') is None


def test_fallback_and_recovery():
    t = MockTransport(); t.start()
    pm = PeerManager('A')
    rt = RoutingTable()
    rt.add_route('C', 'B', '1.1.1.1', 100, metric=1)
    rt.add_route('C', 'D', '2.2.2.2', 200, metric=2)

    rm = RouteManager(rt)
    hb = HeartbeatManager('A', pm, t)
    rm.attach_to_heartbeat(hb)

    # B alive initially
    hb._set_status('B', ALIVE)
    assert rt.get_next_hop('C')[0] == 'B'

    # B dies -> fallback to D
    hb._set_status('B', DEAD)
    assert rt.get_next_hop('C')[0] == 'D'

    # B recovers
    hb._set_status('B', ALIVE)
    assert rt.get_next_hop('C')[0] == 'B'


def test_no_available_route_raises():
    t = MockTransport(); t.start()
    pm = PeerManager('A')
    rt = RoutingTable()
    rm = RouteManager(rt)
    hb = HeartbeatManager('A', pm, t)
    rm.attach_to_heartbeat(hb)

    router = Router('A', t, rt)
    try:
        router.send('X', {'x': 'y'})
        assert False
    except RuntimeError:
        pass
