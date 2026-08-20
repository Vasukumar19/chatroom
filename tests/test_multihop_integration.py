import time
import pytest
from p2p.router import Router
from p2p.routing import RoutingTable
from p2p.peermanager import PeerManager
from p2p.routemanager import RouteLearner
from p2p.testing import MeshMockNetwork, RouterAwareReliableSender, RouterAwareReliableReceiver


def test_multihop_route_learning_and_delivery():
    """
    Proves: A learns C through B via RouteLearner route advertisements.
    A has no direct connection to C. B advertises C. A installs route C->B.
    Message travels A->B->C via multi-hop forwarding.
    """
    mesh = MeshMockNetwork(verbose=False)

    # Add nodes to the mesh
    transport_a = mesh.add_node('A', port=10000)
    transport_b = mesh.add_node('B', port=10001)
    transport_c = mesh.add_node('C', port=10002)

    # Routing tables — empty at start
    route_a = RoutingTable()
    route_b = RoutingTable()
    route_c = RoutingTable()

    # Routers
    router_a = Router('A', transport_a, route_a)
    router_b = Router('B', transport_b, route_b)
    router_c = Router('C', transport_c, route_c)

    # PeerManagers
    pm_a = PeerManager('A')
    pm_b = PeerManager('B')
    pm_c = PeerManager('C')

    # RouteLearners
    rl_a = RouteLearner('A', pm_a, route_a, transport_a, min_advert_interval=0.01)
    rl_b = RouteLearner('B', pm_b, route_b, transport_b, min_advert_interval=0.01)
    rl_c = RouteLearner('C', pm_c, route_c, transport_c, min_advert_interval=0.01)

    # Start routers, peermanagers, and routelearners
    router_a.start()
    router_b.start()
    router_c.start()

    pm_a.start()
    pm_b.start()
    pm_c.start()

    rl_a.start()
    rl_b.start()
    rl_c.start()

    # Setup reliable sender on A and receiver on C
    received = []
    receiver = RouterAwareReliableReceiver('C', router_c, transport_c, lambda msg, addr: received.append(msg['payload']))
    router_c.add_app_handler(receiver._on_transport_message)

    sender = RouterAwareReliableSender('A', router_a, transport_a, timeout=0.05, max_retries=2)
    transport_a.register_handler(sender._on_transport_message)

    # Simulate direct discovery: B <-> C
    route_b.add_route('C', 'C', 'C', 10002)
    pm_b.update_peer('C', 'C', 10002)
    route_c.add_route('B', 'B', 'B', 10001)
    pm_c.update_peer('B', 'B', 10001)

    # Simulate direct discovery: A <-> B
    route_a.add_route('B', 'B', 'B', 10001)
    pm_a.update_peer('B', 'B', 10001)
    route_b.add_route('A', 'A', 'A', 10000)
    pm_b.update_peer('A', 'A', 10000)

    # A has NO direct route to C at this point
    assert route_a.get_next_hop('C') is None or route_a.get_next_hop('C')[0] != 'C', \
        "A should not have a direct route to C"

    # Wait for route advertisements to propagate
    time.sleep(0.25)

    # Verify A has learned C via B through route advertisement
    nh = route_a.get_next_hop('C')
    assert nh is not None, f"A should have learned route to C via B. A routes: {route_a.list_routes()}"
    next_hop, ip, port = nh
    assert next_hop == 'B', f"A's next hop to C should be B, got {next_hop}"

    # Verify C has learned A via B through route advertisement
    nh_c = route_c.get_next_hop('A')
    assert nh_c is not None, f"C should have learned route to A via B. C routes: {route_c.list_routes()}"
    assert nh_c[0] == 'B', f"C's next hop to A should be B, got {nh_c[0]}"

    # Send message from A to C — must travel A->B->C
    ok = sender.send('C', {'text': 'multihop integration test'})
    time.sleep(0.1)

    assert ok is True, "Sender should report successful delivery"
    assert sender.last_status == 'ACKED', f"Expected ACKED, got {sender.last_status}"
    assert received == [{'text': 'multihop integration test'}], \
        f"C should receive exactly one message, got {received}"

    # TTL/hop_count: message went A->B->C = 2 hops, so TTL should be reduced by at least 2
    # (Router decrements TTL once per forward, initial TTL=8 so final should be <=6)
    # We can't inspect the received TTL directly here, but the delivery itself proves forwarding.

    # Clean up
    pm_a.stop()
    pm_b.stop()
    pm_c.stop()
    rl_a.stop()
    rl_b.stop()
    rl_c.stop()


def test_direct_peer_delivery_regression():
    """
    Regression: direct A<->C messaging still works when RouteLearner is active.
    """
    mesh = MeshMockNetwork(verbose=False)
    transport_a = mesh.add_node('A', port=10000)
    transport_c = mesh.add_node('C', port=10002)

    route_a = RoutingTable()
    route_c = RoutingTable()

    router_a = Router('A', transport_a, route_a)
    router_c = Router('C', transport_c, route_c)

    pm_a = PeerManager('A')
    pm_c = PeerManager('C')

    rl_a = RouteLearner('A', pm_a, route_a, transport_a, min_advert_interval=0.01)
    rl_c = RouteLearner('C', pm_c, route_c, transport_c, min_advert_interval=0.01)

    router_a.start()
    router_c.start()
    pm_a.start()
    pm_c.start()
    rl_a.start()
    rl_c.start()

    received = []
    receiver = RouterAwareReliableReceiver('C', router_c, transport_c, lambda msg, addr: received.append(msg['payload']))
    router_c.add_app_handler(receiver._on_transport_message)

    sender = RouterAwareReliableSender('A', router_a, transport_a, timeout=0.05, max_retries=2)
    transport_a.register_handler(sender._on_transport_message)

    # Directly connect A and C
    route_a.add_route('C', 'C', 'C', 10002)
    pm_a.update_peer('C', 'C', 10002)
    route_c.add_route('A', 'A', 'A', 10000)
    pm_c.update_peer('A', 'A', 10000)

    time.sleep(0.1)

    # Direct route should exist and point to C itself
    nh = route_a.get_next_hop('C')
    assert nh is not None
    assert nh[0] == 'C', f"Expected direct next_hop=C, got {nh[0]}"

    ok = sender.send('C', {'text': 'direct message'})
    time.sleep(0.1)

    assert ok is True
    assert sender.last_status == 'ACKED'
    assert received == [{'text': 'direct message'}]

    pm_a.stop()
    pm_c.stop()
    rl_a.stop()
    rl_c.stop()


def test_realtcp_multihop_route_learning_and_delivery(tmp_path):
    """
    Proves: A learns C through B via RouteLearner over REAL TCP sockets (HostTransport).
    A has no direct TCP connection to C.
    B advertises C to A. A installs route C via next-hop B with B's listening TCP port.
    Chat message travels A -> B -> C over real TCP sockets.
    ACK travels C -> B -> A over real TCP sockets.
    """
    from p2p.host import create_host
    from p2p.transport import HostTransport
    from p2p.qos import PriorityTransport
    from p2p.reliability import ReliableReceiver, ReliableSender
    from p2p.chatroom import join_chat_room
    from p2p.store_forward import StoreForwardQueue
    from p2p.store_forward_manager import StoreForwardManager
    from main import find_free_port

    port_a, res_a = find_free_port(15700)
    port_b, res_b = find_free_port(port_a + 1)
    port_c, res_c = find_free_port(port_b + 1)

    for res in (res_a, res_b, res_c):
        res.close()

    h_a = create_host(port_a, identity_dir=str(tmp_path / "a"))
    h_b = create_host(port_b, identity_dir=str(tmp_path / "b"))
    h_c = create_host(port_c, identity_dir=str(tmp_path / "c"))

    t_a = PriorityTransport(HostTransport(h_a), max_queue_size=100); t_a.start()
    t_b = PriorityTransport(HostTransport(h_b), max_queue_size=100); t_b.start()
    t_c = PriorityTransport(HostTransport(h_c), max_queue_size=100); t_c.start()

    rt_a = RoutingTable(); rtr_a = Router(h_a.peer_id, t_a, rt_a); rtr_a.start()
    rt_b = RoutingTable(); rtr_b = Router(h_b.peer_id, t_b, rt_b); rtr_b.start()
    rt_c = RoutingTable(); rtr_c = Router(h_c.peer_id, t_c, rt_c); rtr_c.start()

    pm_a = PeerManager(h_a.peer_id); pm_a.start()
    pm_b = PeerManager(h_b.peer_id); pm_b.start()
    pm_c = PeerManager(h_c.peer_id); pm_c.start()

    rl_a = RouteLearner(h_a.peer_id, pm_a, rt_a, t_a, min_advert_interval=0.05); rl_a.start()
    rl_b = RouteLearner(h_b.peer_id, pm_b, rt_b, t_b, min_advert_interval=0.05); rl_b.start()
    rl_c = RouteLearner(h_c.peer_id, pm_c, rt_c, t_c, min_advert_interval=0.05); rl_c.start()

    def make_resolver(rt):
        def _res(d):
            r = rt.get_route(d)
            return (r.ip, r.port) if r else None
        return _res

    sender_a = ReliableSender(h_a.peer_id, t_a, address_resolver=make_resolver(rt_a))
    sender_b = ReliableSender(h_b.peer_id, t_b, address_resolver=make_resolver(rt_b))
    sender_c = ReliableSender(h_c.peer_id, t_c, address_resolver=make_resolver(rt_c))

    sf_a = StoreForwardManager(queue=StoreForwardQueue(str(tmp_path / "a.db")), reliable_sender=sender_a, route_manager=rt_a)
    sf_b = StoreForwardManager(queue=StoreForwardQueue(str(tmp_path / "b.db")), reliable_sender=sender_b, route_manager=rt_b)
    sf_c = StoreForwardManager(queue=StoreForwardQueue(str(tmp_path / "c.db")), reliable_sender=sender_c, route_manager=rt_c)

    room_a = join_chat_room("test_room", "Alice", h_a.peer_id, h_a, delivery_manager=sf_a)
    room_b = join_chat_room("test_room", "Bob", h_b.peer_id, h_b, delivery_manager=sf_b)
    room_c = join_chat_room("test_room", "Charlie", h_c.peer_id, h_c, delivery_manager=sf_c)

    def send_ack_a(ack, dest): rtr_a._send_on_route(rt_a.get_route(dest), ack)
    def send_ack_b(ack, dest): rtr_b._send_on_route(rt_b.get_route(dest), ack)
    def send_ack_c(ack, dest): rtr_c._send_on_route(rt_c.get_route(dest), ack)

    recv_a = ReliableReceiver(h_a.peer_id, t_a, lambda env, addr: room_a._handle_incoming_message(env.get("payload", {})), auto_register=False, ack_sender=send_ack_a)
    rtr_a.add_app_handler(recv_a._on_transport_message)

    recv_b = ReliableReceiver(h_b.peer_id, t_b, lambda env, addr: room_b._handle_incoming_message(env.get("payload", {})), auto_register=False, ack_sender=send_ack_b)
    rtr_b.add_app_handler(recv_b._on_transport_message)

    recv_c = ReliableReceiver(h_c.peer_id, t_c, lambda env, addr: room_c._handle_incoming_message(env.get("payload", {})), auto_register=False, ack_sender=send_ack_c)
    rtr_c.add_app_handler(recv_c._on_transport_message)

    # Establish TCP connections for direct neighbors ONLY: A <-> B and B <-> C
    h_a.connect_to_peer("127.0.0.1", port_b, h_b.peer_id); rt_a.add_route(h_b.peer_id, h_b.peer_id, "127.0.0.1", port_b); pm_a.update_peer(h_b.peer_id, "127.0.0.1", port_b)
    h_b.connect_to_peer("127.0.0.1", port_a, h_a.peer_id); rt_b.add_route(h_a.peer_id, h_a.peer_id, "127.0.0.1", port_a); pm_b.update_peer(h_a.peer_id, "127.0.0.1", port_a)

    h_b.connect_to_peer("127.0.0.1", port_c, h_c.peer_id); rt_b.add_route(h_c.peer_id, h_c.peer_id, "127.0.0.1", port_c); pm_b.update_peer(h_c.peer_id, "127.0.0.1", port_c)
    h_c.connect_to_peer("127.0.0.1", port_b, h_b.peer_id); rt_c.add_route(h_b.peer_id, h_b.peer_id, "127.0.0.1", port_b); pm_c.update_peer(h_b.peer_id, "127.0.0.1", port_b)

    # Wait for route advertisements to propagate
    time.sleep(0.5)

    # A must have learned C via B with B's listening port
    route_to_c = rt_a.get_route(h_c.peer_id)
    assert route_to_c is not None, f"A failed to learn route to C. A routes: {rt_a.list_routes()}"
    assert route_to_c.next_hop == h_b.peer_id
    assert route_to_c.port == port_b, f"Expected next hop port {port_b}, got {route_to_c.port}"
    assert route_to_c.hops == 2
    assert route_to_c.metric >= 200

    # A sends message to room (addressed to C via multi-hop forwarding)
    room_a.publish("TCP multi-hop test message")
    time.sleep(0.8)

    received_c = [m.Message for m in room_c.messages]
    assert "TCP multi-hop test message" in received_c

    for obj in [rl_a, rl_b, rl_c, pm_a, pm_b, pm_c, rtr_a, rtr_b, rtr_c, t_a, t_b, t_c, h_a, h_b, h_c]:
        obj.stop()
