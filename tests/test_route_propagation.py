import pytest
from p2p.transport import MockTransport
from p2p.routemanager import RouteLearner
from p2p.routing import RoutingTable
from p2p.peermanager import PeerManager


class Node:
    def __init__(self, node_id):
        self.node_id = node_id
        self.transport = MockTransport()
        self.routing_table = RoutingTable()
        self.peer_manager = PeerManager(node_id)
        self.learner = RouteLearner(node_id, self.peer_manager, self.routing_table, self.transport)
        # register transport handlers
        try:
            self.learner.start()
        except Exception:
            pass

    def send_to(self, other, env):
        other.transport.simulate_incoming(env, ("127.0.0.1", 10000))


def test_three_hop_propagation():
    A = Node('A')
    B = Node('B')
    C = Node('C')

    # B knows C directly
    B.routing_table.add_route('C', 'C', '127.0.0.1', 10002, metric=1, hops=1)

    # B advertises routes to A
    B.learner.send_route_advertisement(('127.0.0.1', 10001))
    # simulate A receiving B's advertisement
    env = B.transport.last_sent
    A.transport.simulate_incoming(env, ("127.0.0.1", 10001))

    # A should now have route to C via B
    nh = A.routing_table.get_next_hop('C')
    assert nh[0] == 'B'


def test_four_hop_propagation():
    A = Node('A')
    B = Node('B')
    C = Node('C')
    D = Node('D')

    # D known by C
    C.routing_table.add_route('D', 'D', '127.0.0.1', 10003, metric=1, hops=1)
    # C advertises to B
    C.learner.send_route_advertisement(('127.0.0.1', 10002))
    env = C.transport.last_sent
    B.transport.simulate_incoming(env, ("127.0.0.1", 10002))

    # B should now know D via C
    assert B.routing_table.get_next_hop('D')[0] == 'C'

    # B advertises to A
    B.learner.send_route_advertisement(('127.0.0.1', 10001))
    env2 = B.transport.last_sent
    A.transport.simulate_incoming(env2, ("127.0.0.1", 10001))

    assert A.routing_table.get_next_hop('D')[0] == 'B'
