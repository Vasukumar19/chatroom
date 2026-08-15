import time

from p2p.router import Router
from p2p.routing import RoutingTable
from p2p.reliability import ReliableReceiver
from p2p.testing import MeshMockNetwork, DroppingNetworkTransport, RouterAwareReliableSender
from p2p.transport import MockTransport


def test_reliable_multihop_delivery_with_retry():
    mesh = MeshMockNetwork()
    transport_a = mesh.add_node('A')
    transport_b = mesh.add_node('B')
    transport_c = mesh.add_node('C')
    transport_d = mesh.add_node('D', transport_cls=DroppingNetworkTransport, port=10003, drop_ack_count=1)

    route_a = RoutingTable()
    route_b = RoutingTable()
    route_c = RoutingTable()
    route_d = RoutingTable()

    route_a.add_route('D', 'B', 'B', 10001)
    route_b.add_route('D', 'C', 'C', 10002)
    route_b.add_route('A', 'A', 'A', 10000)
    route_c.add_route('D', 'D', 'D', 10003)
    route_c.add_route('A', 'B', 'B', 10001)
    route_d.add_route('A', 'C', 'C', 10002)

    router_a = Router('A', transport_a, route_a)
    router_b = Router('B', transport_b, route_b)
    router_c = Router('C', transport_c, route_c)
    router_d = Router('D', transport_d, route_d)

    router_a.start(); router_b.start(); router_c.start(); router_d.start()

    received = []
    seen_message_ids = []

    def app_handler(msg, addr):
        received.append(msg['payload'])
        seen_message_ids.append(msg['message_id'])

    receiver = ReliableReceiver('D', transport_d, app_handler, auto_register=False)
    router_d.add_app_handler(receiver._on_transport_message)

    sender = RouterAwareReliableSender('A', router_a, transport_a, timeout=0.02, max_retries=2)
    transport_a.register_handler(sender._on_transport_message)

    ok = sender.send('D', {'value': 'multihop'})
    time.sleep(0.1)

    assert ok is True
    assert sender.last_status == 'ACKED'
    assert sender.retry_count == 1
    assert received == [{'value': 'multihop'}]
    assert len(seen_message_ids) == 1
    assert seen_message_ids[0] == sender.last_message_id
