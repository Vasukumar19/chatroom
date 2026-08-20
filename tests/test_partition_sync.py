import pytest
import time
import os
from p2p.testing import MeshMockNetwork, NetworkTransport, RouterAwareReliableSender
from p2p.routing import RoutingTable
from p2p.router import Router
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.peermanager import PeerManager
from p2p.routemanager import RouteLearner
from p2p.reliability import ReliableReceiver
from p2p.chatroom import ChatRoom
from p2p.qos import PriorityTransport
from p2p.host import P2PHost
from p2p.sync import SyncManager


class DummyHost:
    def __init__(self, peer_id):
        self.peer_id = peer_id
        self.peers = {}
        self.known_peers = set()
        
    def get_known_peers(self):
        return self.known_peers
        
    def get_peers(self):
        return self.peers


class Node:
    def __init__(self, network, peer_id, db_path):
        self.peer_id = peer_id
        self.network = network
        self.base_transport = NetworkTransport(network, peer_id)
        self.transport = PriorityTransport(self.base_transport, max_queue_size=100)
        self.routing_table = RoutingTable()
        self.router = Router(peer_id, self.transport, self.routing_table)
        
        self.peer_manager = PeerManager(peer_id)
        self.route_learner = RouteLearner(peer_id, self.peer_manager, self.routing_table, self.transport, min_advert_interval=0.1)
        
        def address_resolver(dest):
            route = self.routing_table.get_route(dest)
            if not route:
                raise RuntimeError(f"No route to {dest}")
            return (route.ip, route.port)
            
        self.reliable_sender = RouterAwareReliableSender(peer_id, self.router, self.transport, max_retries=1)
        
        self.queue = StoreForwardQueue(db_path)
        self.sf_manager = StoreForwardManager(
            queue=self.queue,
            reliable_sender=self.reliable_sender,
            route_manager=self.routing_table
        )
        self.routing_table.add_route_recovery_callback(self.sf_manager.on_route_recovered)
        
        self.host = DummyHost(peer_id)
        self.chatroom = ChatRoom("room", f"Nick-{peer_id}", peer_id, self.host, delivery_manager=self.sf_manager)
        
        def handle_app(env, addr):
            payload = env.get("payload", {})
            self.chatroom._handle_incoming_message(payload)
            
        def send_ack(ack, destination):
            route = self.routing_table.get_route(destination)
            if route:
                self.router._send_on_route(route, ack)
            else:
                self.base_transport.send(destination, ack)

        self.reliable_receiver = ReliableReceiver(
            peer_id, 
            self.transport, 
            handle_app, 
            auto_register=True, 
            ack_sender=send_ack,
            message_archiver=self.queue.archive_message
        )
        self.sync_manager = SyncManager(
            peer_id,
            self.transport,
            self.queue,
            self.reliable_receiver,
            route_manager=self.routing_table
        )
        self.routing_table.add_route_recovery_callback(self.sync_manager.trigger_sync)

    def start(self):
        self.transport.start()
        self.router.start()
        self.peer_manager.start()
        self.route_learner.start()
        
    def stop(self):
        self.transport.stop()
        self.router.stop()
        self.peer_manager.stop()
        self.route_learner.stop()

class PartitionMockNetwork(MeshMockNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.partitions = {} # node -> set of reachable nodes

    def deliver(self, source_node: str, target_node: str, message, *, source_addr=None):
        if target_node not in self.partitions.get(source_node, set()):
            return # DROP
        super().deliver(source_node, target_node, message, source_addr=source_addr)


def test_partition_and_sync_recovery(tmpdir):
    db_A = str(tmpdir.join("A.db"))
    db_B = str(tmpdir.join("B.db"))
    db_C = str(tmpdir.join("C.db"))
    db_D = str(tmpdir.join("D.db"))
    
    network = PartitionMockNetwork()
    
    nodeA = Node(network, 'A', db_A)
    nodeB = Node(network, 'B', db_B)
    nodeC = Node(network, 'C', db_C)
    nodeD = Node(network, 'D', db_D)
    
    nodes = [nodeA, nodeB, nodeC, nodeD]
    for n in nodes:
        network.add_node(n.peer_id, port=0)
        # Note: In Node.__init__, it created its own NetworkTransport using the original network.
        # Let's manually overwrite it so `network.add_node` returns it or we just register it.
        network.nodes[n.peer_id] = n.base_transport
        n.start()
        
    def link(a, b):
        network.partitions.setdefault(a, set()).add(b)
        network.partitions.setdefault(b, set()).add(a)

    def unlink(a, b):
        if b in network.partitions.get(a, set()):
            network.partitions[a].remove(b)
        if a in network.partitions.get(b, set()):
            network.partitions[b].remove(a)
    
    # Initial topology A-B-C-D
    link('A', 'B')
    link('B', 'C')
    link('C', 'D')
    
    # Simulate peer discovery across the initial topology
    for a, b in [('A', 'B'), ('B', 'C'), ('C', 'D')]:
        nA = next(n for n in nodes if n.peer_id == a)
        nB = next(n for n in nodes if n.peer_id == b)
        nA.routing_table.add_route(b, b, b, 0)
        nB.routing_table.add_route(a, a, a, 0)
        nA.peer_manager.update_peer(b, b, 0)
        nB.peer_manager.update_peer(a, a, 0)

    time.sleep(2.0)
    
    print(f"A routes: {nodeA.routing_table.list_routes()}")
    print(f"B routes: {nodeB.routing_table.list_routes()}")
    print(f"C routes: {nodeC.routing_table.list_routes()}")
    
    # Verify everyone can reach everyone initially
    assert nodeA.routing_table.get_next_hop('D')[0] == 'B'
    
    # CREATE PARTITION: A-B || C-D
    unlink('B', 'C')
    
    # Wait for route expiry or manually simulate lost peer
    nodeB.routing_table.remove_route('C')
    nodeB.routing_table.remove_route('D') # multi-hop lost
    nodeA.routing_table.remove_route('C')
    nodeA.routing_table.remove_route('D')
    nodeC.routing_table.remove_route('B')
    nodeC.routing_table.remove_route('A')
    nodeD.routing_table.remove_route('B')
    nodeD.routing_table.remove_route('A')
    time.sleep(0.5)
    
    assert nodeA.routing_table.get_next_hop('D') is None
    assert nodeC.routing_table.get_next_hop('A') is None
    
    # A creates msg-A1, msg-A2
    nodeA.chatroom.publish("msg-A1")
    nodeA.chatroom.publish("msg-A2")
    
    # C creates msg-C1, msg-C2
    nodeC.chatroom.publish("msg-C1")
    nodeC.chatroom.publish("msg-C2")
    
    time.sleep(0.5)
    
    # HEAL PARTITION
    link('B', 'C')
    nodeB.routing_table.add_route('C', 'C', 'C', 0)
    nodeC.routing_table.add_route('B', 'B', 'B', 0)
    nodeB.peer_manager.update_peer('C', 'C', 0)
    nodeC.peer_manager.update_peer('B', 'B', 0)
    
    time.sleep(1.0) # Let route advertisements propagate and SF queues replay
    
    print(f"A routes: {nodeA.routing_table.list_routes()}")
    print(f"B routes: {nodeB.routing_table.list_routes()}")
    print(f"C routes: {nodeC.routing_table.list_routes()}")

    messages_A = [m.Message for m in nodeA.chatroom.messages]
    messages_D = [m.Message for m in nodeD.chatroom.messages]
    
    # Both sides should have all 4 messages
    for msg in ["msg-A1", "msg-A2", "msg-C1", "msg-C2"]:
        assert msg in messages_A
        assert msg in messages_D
        
    for n in nodes:
        n.stop()


def test_sync_direct_exchange(tmpdir):
    """Test that SyncManager transfers missing messages between two nodes directly."""
    db_B = str(tmpdir.join("B.db"))
    db_C = str(tmpdir.join("C.db"))

    network = PartitionMockNetwork()
    nodeB = Node(network, 'B', db_B)
    nodeC = Node(network, 'C', db_C)

    for n in [nodeB, nodeC]:
        network.add_node(n.peer_id, port=0)
        network.nodes[n.peer_id] = n.base_transport
        n.start()

    # Link B and C
    network.partitions.setdefault('B', set()).add('C')
    network.partitions.setdefault('C', set()).add('B')

    nodeB.routing_table.add_route('C', 'C', 'C', 0)
    nodeC.routing_table.add_route('B', 'B', 'B', 0)

    # Node B creates and archives a message locally (as if received before partition)
    env_X = {
        "message_id": "msg-X-123",
        "type": "data",
        "source": "A",
        "destination": "B",
        "payload": {
            "type": "chat_message",
            "room": "room",
            "data": {
                "Message": "Hello from A",
                "SenderID": "A",
                "SenderNick": "Nick-A",
                "MessageID": "chat-X-1",
                "Timestamp": "2026-08-20T19:00:00Z"
            }
        }
    }
    nodeB.queue.archive_message("msg-X-123", env_X)

    # C triggers sync with B
    nodeC.sync_manager.trigger_sync('B')
    time.sleep(0.5)

    messages_C = [m.Message for m in nodeC.chatroom.messages]
    assert "Hello from A" in messages_C

    nodeB.stop()
    nodeC.stop()


def test_sync_idempotence(tmpdir):
    """Test that repeating sync multiple times does not produce duplicate messages."""
    db_B = str(tmpdir.join("B.db"))
    db_C = str(tmpdir.join("C.db"))

    network = PartitionMockNetwork()
    nodeB = Node(network, 'B', db_B)
    nodeC = Node(network, 'C', db_C)

    for n in [nodeB, nodeC]:
        network.add_node(n.peer_id, port=0)
        network.nodes[n.peer_id] = n.base_transport
        n.start()

    network.partitions.setdefault('B', set()).add('C')
    network.partitions.setdefault('C', set()).add('B')
    nodeB.routing_table.add_route('C', 'C', 'C', 0)
    nodeC.routing_table.add_route('B', 'B', 'B', 0)

    env_Y = {
        "message_id": "msg-Y-456",
        "type": "data",
        "source": "A",
        "destination": "B",
        "payload": {
            "type": "chat_message",
            "room": "room",
            "data": {
                "Message": "Idempotent Message",
                "SenderID": "A",
                "SenderNick": "Nick-A",
                "MessageID": "chat-Y-1",
                "Timestamp": "2026-08-20T19:00:00Z"
            }
        }
    }
    nodeB.queue.archive_message("msg-Y-456", env_Y)

    # Trigger sync multiple times
    nodeC.sync_manager.trigger_sync('B')
    time.sleep(0.2)
    nodeC.sync_manager.trigger_sync('B')
    time.sleep(0.2)
    nodeC.sync_manager.trigger_sync('B')
    time.sleep(0.2)

    messages_C = [m.Message for m in nodeC.chatroom.messages if m.Message == "Idempotent Message"]
    assert len(messages_C) == 1

    nodeB.stop()
    nodeC.stop()


def test_sync_expired_messages_skipped(tmpdir):
    """Test that expired messages are not included in sync responses."""
    db_B = str(tmpdir.join("B.db"))
    db_C = str(tmpdir.join("C.db"))

    network = PartitionMockNetwork()
    nodeB = Node(network, 'B', db_B)
    nodeC = Node(network, 'C', db_C)

    for n in [nodeB, nodeC]:
        network.add_node(n.peer_id, port=0)
        network.nodes[n.peer_id] = n.base_transport
        n.start()

    network.partitions.setdefault('B', set()).add('C')
    network.partitions.setdefault('C', set()).add('B')
    nodeB.routing_table.add_route('C', 'C', 'C', 0)
    nodeC.routing_table.add_route('B', 'B', 'B', 0)

    # Expired message
    env_expired = {
        "message_id": "msg-exp-789",
        "type": "data",
        "source": "A",
        "destination": "B",
        "expires_at": "2020-01-01T00:00:00Z",
        "payload": {
            "type": "chat_message",
            "room": "room",
            "data": {
                "Message": "Expired Message",
                "SenderID": "A",
                "SenderNick": "Nick-A",
                "MessageID": "chat-exp-1",
                "Timestamp": "2020-01-01T00:00:00Z"
            }
        }
    }
    nodeB.queue.archive_message("msg-exp-789", env_expired)

    nodeC.sync_manager.trigger_sync('B')
    time.sleep(0.3)

    messages_C = [m.Message for m in nodeC.chatroom.messages if m.Message == "Expired Message"]
    assert len(messages_C) == 0

    nodeB.stop()
    nodeC.stop()
