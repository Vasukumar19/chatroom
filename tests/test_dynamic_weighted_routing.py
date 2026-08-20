import time
import pytest
from p2p.host import create_host
from p2p.transport import HostTransport, Transport
from p2p.qos import PriorityTransport
from p2p.routing import RoutingTable, BASE_COST
from p2p.router import Router
from p2p.peermanager import PeerManager
from p2p.routemanager import RouteLearner
from p2p.reliability import ReliableReceiver, ReliableSender, PeerLinkMetrics
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.chatroom import join_chat_room
from main import find_free_port


def test_peer_link_metrics_rtt_ema_and_retry_rate():
    """Unit test for PeerLinkMetrics EMA and sliding window retry rate calculation."""
    metrics = PeerLinkMetrics("node-1")
    assert metrics.rtt_ms is None
    assert metrics.retry_rate == 0.0

    # First sample: 100ms, 0 retries
    metrics.record_ack(100.0, 0)
    assert metrics.rtt_ms == 100.0
    assert metrics.retry_rate == 0.0

    # Second sample: 50ms, 0 retries -> (0.8 * 100) + (0.2 * 50) = 90.0ms
    metrics.record_ack(50.0, 0)
    assert abs(metrics.rtt_ms - 90.0) < 1e-5
    assert metrics.retry_rate == 0.0

    # Third sample: with 1 retry
    metrics.record_ack(120.0, 1)
    # history: [0, 0, 1] -> 1/3
    assert abs(metrics.retry_rate - (1.0 / 3.0)) < 1e-5


def test_priority_transport_queue_pressure():
    """Unit test for PriorityTransport get_queue_pressure."""
    from p2p.testing import MockTransport
    base = MockTransport()
    pt = PriorityTransport(base, max_queue_size=10)
    assert pt.get_queue_pressure() == 0.0

    # Add 5 items
    for i in range(5):
        pt.send(("127.0.0.1", 5000), {"message_id": f"m-{i}", "priority": 1})
    assert abs(pt.get_queue_pressure() - 0.5) < 1e-5


def test_dynamic_weighted_lower_latency_path_selected_real_tcp(tmp_path):
    """
    Test 1: Diamond topology (A-B-D vs A-C-D) over REAL TCP sockets.
    Both paths have 2 hops. Path via B has lower latency than Path via C.
    Verifies:
      1. Node A selects B as next-hop to D.
      2. Route metric via B is lower than via C.
      3. Message published at A is forwarded A -> B -> D.
    """
    port_a, r_a = find_free_port(18000)
    port_b, r_b = find_free_port(port_a + 1)
    port_c, r_c = find_free_port(port_b + 1)
    port_d, r_d = find_free_port(port_c + 1)
    for r in (r_a, r_b, r_c, r_d): r.close()

    h_a = create_host(port_a, identity_dir=str(tmp_path / "a"))
    h_b = create_host(port_b, identity_dir=str(tmp_path / "b"))
    h_c = create_host(port_c, identity_dir=str(tmp_path / "c"))
    h_d = create_host(port_d, identity_dir=str(tmp_path / "d"))

    nodes = {'A': h_a, 'B': h_b, 'C': h_c, 'D': h_d}
    ports = {'A': port_a, 'B': port_b, 'C': port_c, 'D': port_d}

    transports, routers, r_tables, p_managers, r_learners, senders, rooms = {}, {}, {}, {}, {}, {}, {}

    for name, h in nodes.items():
        t = PriorityTransport(HostTransport(h), max_queue_size=100); t.start()
        transports[name] = t
        rt = RoutingTable(); r_tables[name] = rt
        rtr = Router(h.peer_id, t, rt); rtr.start()
        routers[name] = rtr
        pm = PeerManager(h.peer_id); pm.start()
        p_managers[name] = pm

        def make_resolver(rt_obj):
            return lambda d: (rt_obj.get_route(d).ip, rt_obj.get_route(d).port) if rt_obj.get_route(d) else None

        sender = ReliableSender(h.peer_id, t, timeout=0.5, max_retries=2, address_resolver=make_resolver(rt))
        senders[name] = sender

        rl = RouteLearner(h.peer_id, pm, rt, t, reliable_sender=sender, min_advert_interval=0.05); rl.start()
        r_learners[name] = rl

        sf = StoreForwardManager(queue=StoreForwardQueue(str(tmp_path / f"{name}.db")), reliable_sender=sender, route_manager=rt)
        room = join_chat_room("weighted_room", f"User-{name}", h.peer_id, h, delivery_manager=sf)
        rooms[name] = room

        def make_ack_sender(rtr_obj, rt_obj):
            return lambda ack, dest: rtr_obj._send_on_route(rt_obj.get_route(dest), ack) if rt_obj.get_route(dest) else None

        rcv = ReliableReceiver(h.peer_id, t, lambda env, addr, rm=room: rm._handle_incoming_message(env.get("payload", {})), auto_register=False, ack_sender=make_ack_sender(rtr, rt))
        rtr.add_app_handler(rcv._on_transport_message)

    def link(n1, n2):
        nodes[n1].connect_to_peer("127.0.0.1", ports[n2], nodes[n2].peer_id)
        nodes[n2].connect_to_peer("127.0.0.1", ports[n1], nodes[n1].peer_id)
        r_tables[n1].add_route(nodes[n2].peer_id, nodes[n2].peer_id, "127.0.0.1", ports[n2])
        r_tables[n2].add_route(nodes[n1].peer_id, nodes[n1].peer_id, "127.0.0.1", ports[n1])
        p_managers[n1].update_peer(nodes[n2].peer_id, "127.0.0.1", ports[n2])
        p_managers[n2].update_peer(nodes[n1].peer_id, "127.0.0.1", ports[n1])

    link('A', 'B')
    link('A', 'C')
    link('B', 'D')
    link('C', 'D')

    # Record real RTT measurements on A:
    # A -> B has low RTT (10ms penalty: +1)
    # A -> C has high RTT (800ms penalty: +80)
    senders['A'].peer_metrics[nodes['B'].peer_id] = PeerLinkMetrics(nodes['B'].peer_id)
    senders['A'].peer_metrics[nodes['B'].peer_id].record_ack(10.0, 0)

    senders['A'].peer_metrics[nodes['C'].peer_id] = PeerLinkMetrics(nodes['C'].peer_id)
    senders['A'].peer_metrics[nodes['C'].peer_id].record_ack(800.0, 0)

    # Let advertisements propagate
    for name in nodes:
        r_learners[name].request_advertisement()
    time.sleep(0.8)

    # Verify A selected B over C for destination D
    route_d = r_tables['A'].get_route(nodes['D'].peer_id)
    assert route_d is not None
    assert route_d.next_hop == nodes['B'].peer_id
    assert route_d.metric < 250  # B path cost: 100 + 1 (A-B) + 100 (B-D) = ~201

    # Send message A -> D and verify delivery
    rooms['A'].publish("Real TCP Weighted Routing Message")
    time.sleep(0.8)

    received_d = [m.Message for m in rooms['D'].messages]
    assert "Real TCP Weighted Routing Message" in received_d

    for name in nodes:
        r_learners[name].stop()
        p_managers[name].stop()
        routers[name].stop()
        transports[name].stop()
        nodes[name].stop()


def test_dynamic_weighted_failover_and_recovery_real_tcp(tmp_path):
    """
    Test 2 & 3: Dynamic failover when active path latency degrades, and recovery when restored.
    """
    port_a, r_a = find_free_port(18100)
    port_b, r_b = find_free_port(port_a + 1)
    port_c, r_c = find_free_port(port_b + 1)
    port_d, r_d = find_free_port(port_c + 1)
    for r in (r_a, r_b, r_c, r_d): r.close()

    h_a = create_host(port_a, identity_dir=str(tmp_path / "a"))
    h_b = create_host(port_b, identity_dir=str(tmp_path / "b"))
    h_c = create_host(port_c, identity_dir=str(tmp_path / "c"))
    h_d = create_host(port_d, identity_dir=str(tmp_path / "d"))

    nodes = {'A': h_a, 'B': h_b, 'C': h_c, 'D': h_d}
    ports = {'A': port_a, 'B': port_b, 'C': port_c, 'D': port_d}

    transports, routers, r_tables, p_managers, r_learners, senders, rooms = {}, {}, {}, {}, {}, {}, {}

    for name, h in nodes.items():
        t = PriorityTransport(HostTransport(h), max_queue_size=100); t.start()
        transports[name] = t
        rt = RoutingTable(); r_tables[name] = rt
        rtr = Router(h.peer_id, t, rt); rtr.start()
        routers[name] = rtr
        pm = PeerManager(h.peer_id); pm.start()
        p_managers[name] = pm

        def make_resolver(rt_obj):
            return lambda d: (rt_obj.get_route(d).ip, rt_obj.get_route(d).port) if rt_obj.get_route(d) else None

        sender = ReliableSender(h.peer_id, t, timeout=0.5, max_retries=2, address_resolver=make_resolver(rt))
        senders[name] = sender

        rl = RouteLearner(h.peer_id, pm, rt, t, reliable_sender=sender, min_advert_interval=0.05); rl.start()
        r_learners[name] = rl

        sf = StoreForwardManager(queue=StoreForwardQueue(str(tmp_path / f"{name}.db")), reliable_sender=sender, route_manager=rt)
        room = join_chat_room("weighted_failover", f"User-{name}", h.peer_id, h, delivery_manager=sf)
        rooms[name] = room

        def make_ack_sender(rtr_obj, rt_obj):
            return lambda ack, dest: rtr_obj._send_on_route(rt_obj.get_route(dest), ack) if rt_obj.get_route(dest) else None

        rcv = ReliableReceiver(h.peer_id, t, lambda env, addr, rm=room: rm._handle_incoming_message(env.get("payload", {})), auto_register=False, ack_sender=make_ack_sender(rtr, rt))
        rtr.add_app_handler(rcv._on_transport_message)

    def link(n1, n2):
        nodes[n1].connect_to_peer("127.0.0.1", ports[n2], nodes[n2].peer_id)
        nodes[n2].connect_to_peer("127.0.0.1", ports[n1], nodes[n1].peer_id)
        r_tables[n1].add_route(nodes[n2].peer_id, nodes[n2].peer_id, "127.0.0.1", ports[n2])
        r_tables[n2].add_route(nodes[n1].peer_id, nodes[n1].peer_id, "127.0.0.1", ports[n1])
        p_managers[n1].update_peer(nodes[n2].peer_id, "127.0.0.1", ports[n2])
        p_managers[n2].update_peer(nodes[n1].peer_id, "127.0.0.1", ports[n1])

    link('A', 'B')
    link('A', 'C')
    link('B', 'D')
    link('C', 'D')

    # Initial state: A-B low latency (10ms), A-C higher latency (500ms)
    senders['A'].peer_metrics[nodes['B'].peer_id] = PeerLinkMetrics(nodes['B'].peer_id)
    senders['A'].peer_metrics[nodes['B'].peer_id].record_ack(10.0, 0)
    senders['A'].peer_metrics[nodes['C'].peer_id] = PeerLinkMetrics(nodes['C'].peer_id)
    senders['A'].peer_metrics[nodes['C'].peer_id].record_ack(500.0, 0)

    for name in nodes:
        r_learners[name].request_advertisement(force=True)
    time.sleep(0.8)

    assert r_tables['A'].get_route(nodes['D'].peer_id).next_hop == nodes['B'].peer_id

    # Now degrade A-B latency significantly (2000ms), while C is 500ms (cost ~250)
    for _ in range(5):
        senders['A'].peer_metrics[nodes['B'].peer_id].record_ack(2000.0, 0)
        senders['A'].peer_metrics[nodes['C'].peer_id].record_ack(500.0, 0)

    for name in nodes:
        r_learners[name].request_advertisement(force=True)
    time.sleep(0.8)

    # Verify A has failed over to C
    route_after_degrade = r_tables['A'].get_route(nodes['D'].peer_id)
    assert route_after_degrade.next_hop == nodes['C'].peer_id

    rooms['A'].publish("Message via failed over path C")
    time.sleep(0.8)
    assert "Message via failed over path C" in [m.Message for m in rooms['D'].messages]

    # Restore B (10ms -> cost ~201). After 25 samples EMA fully converges from 2000ms to ~10ms.
    for _ in range(25):
        senders['A'].peer_metrics[nodes['B'].peer_id].record_ack(10.0, 0)

    for name in nodes:
        r_learners[name].request_advertisement(force=True)
    time.sleep(0.8)

    # Reconvergence back to B
    routes_d = r_tables['A']._routes[nodes['D'].peer_id]
    debug_info = {nh: (e.metric, e.hops, e.status) for nh, e in routes_d.items()}
    route_restored = r_tables['A'].get_route(nodes['D'].peer_id)
    assert route_restored.next_hop == nodes['B'].peer_id, f"Expected B, got {route_restored.next_hop}. Active: {r_tables['A']._active_selection}, Candidates: {debug_info}"

    for name in nodes:
        r_learners[name].stop()
        p_managers[name].stop()
        routers[name].stop()
        transports[name].stop()
        nodes[name].stop()


def test_dynamic_weighted_hysteresis_threshold():
    """Test that metric improvements smaller than hysteresis threshold do not cause route flapping."""
    from p2p.testing import MockTransport
    pm = PeerManager("A")
    rt = RoutingTable()
    t = MockTransport()
    rl = RouteLearner("A", pm, rt, t, min_advert_interval=0.01)

    # Current route via B has cost 200
    rt.add_route("D", "B", "127.0.0.1", 5001, metric=200, hops=2)
    assert rt.get_route("D").next_hop == "B"

    # Receive advertisement from C with cost 90 + local link cost 100 = 190.
    # Improvement is 10 (which is < max(25, 0.15 * 200) = 30).
    # Hysteresis should keep B as the selected route.
    env = {
        "type": "route_advertisement",
        "source": "C",
        "message_id": "ad-1",
        "payload": {"routes": {"D": 90}, "hops": {"D": 1}}
    }
    rl._on_transport_message(env, ("127.0.0.1", 5002))

    best = rt.get_route("D")
    assert best.next_hop == "B", f"Hysteresis failed: route flapped to {best.next_hop}"

    # Now receive advertisement from C with cost 50 + local link cost 100 = 150.
    # Improvement is 50 (> 30 threshold).
    # Hysteresis should now allow switching to C.
    env2 = {
        "type": "route_advertisement",
        "source": "C",
        "message_id": "ad-2",
        "payload": {"routes": {"D": 50}, "hops": {"D": 1}}
    }
    rl._on_transport_message(env2, ("127.0.0.1", 5002))

    best2 = rt.get_route("D")
    assert best2.next_hop == "C", f"Switching failed: expected C, got {best2.next_hop}"


def test_dynamic_weighted_retry_penalty_selection():
    """Test that retry rate penalty shifts route selection to the more reliable path."""
    from p2p.testing import MockTransport
    pm = PeerManager("A")
    rt = RoutingTable()
    t = MockTransport()
    sender = ReliableSender("A", t)
    rl = RouteLearner("A", pm, rt, t, reliable_sender=sender, min_advert_interval=0.01)

    # Link to B has high retry rate (50% retry -> penalty 75, cost = 175)
    # Link to C is clean (0% retry -> penalty 0, cost = 100)
    sender.peer_metrics["B"] = PeerLinkMetrics("B")
    for _ in range(10):
        sender.peer_metrics["B"].record_ack(10.0, 1)  # 100% retried
    sender.peer_metrics["C"] = PeerLinkMetrics("C")
    for _ in range(10):
        sender.peer_metrics["C"].record_ack(10.0, 0)  # 0% retried

    # Both advertise destination D at cost 100
    env_b = {"type": "route_advertisement", "source": "B", "payload": {"routes": {"D": 100}, "hops": {"D": 1}}}
    env_c = {"type": "route_advertisement", "source": "C", "payload": {"routes": {"D": 100}, "hops": {"D": 1}}}

    rl._on_transport_message(env_b, ("127.0.0.1", 5001))
    rl._on_transport_message(env_c, ("127.0.0.1", 5002))

    best = rt.get_route("D")
    assert best.next_hop == "C"
    assert best.metric == 201  # 100 (base) + 100 (adv) + 1 (10ms RTT) = 201 via C vs 351 via B


def test_dynamic_weighted_max_hops_loop_prevention():
    """Test that routes reaching MAX_ROUTE_HOPS (16) are rejected to prevent loops/count-to-infinity."""
    from p2p.testing import MockTransport
    pm = PeerManager("A")
    rt = RoutingTable()
    t = MockTransport()
    rl = RouteLearner("A", pm, rt, t, min_advert_interval=0.01)

    # Advertisement claiming 15 hops (next hop would be 16)
    env = {"type": "route_advertisement", "source": "B", "payload": {"routes": {"Z": 1500}, "hops": {"Z": 15}}}
    rl._on_transport_message(env, ("127.0.0.1", 5001))

    # Route should be rejected
    assert rt.get_route("Z") is None
