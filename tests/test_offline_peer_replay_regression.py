import os
import pytest
from typing import List, Dict, Any

from p2p.host import P2PHost
from p2p.chatroom import ChatRoom
from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.routing import RoutingTable
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.transport import MockTransport


class NodeHelper:
    def __init__(self, peer_id: str, tmp_path: str, transport: MockTransport):
        self.peer_id = peer_id
        self.host = P2PHost(port=5000)
        self.host.peer_id = peer_id
        self.transport = transport
        
        self.routing_table = RoutingTable()
        
        def address_resolver(dest):
            route = self.routing_table.get_route(dest)
            if not route:
                raise RuntimeError(f"No route to {dest}")
            return (route.ip, route.port)
            
        self.reliable_sender = ReliableSender(
            self.peer_id,
            self.transport,
            address_resolver=address_resolver,
            timeout=0.1,
            max_retries=2,
        )
        
        db_path = os.path.join(tmp_path, f"disasterconnect-{self.peer_id}.sqlite")
        self.queue = StoreForwardQueue(db_path)
        
        self.store_forward_manager = StoreForwardManager(
            queue=self.queue,
            reliable_sender=self.reliable_sender,
            route_manager=self.routing_table,
        )
        
        self.received_messages: List[Dict[str, Any]] = []
        
        def ack_sender(ack_envelope, destination):
            route = self.routing_table.get_route(destination)
            if not route:
                raise RuntimeError(f"No route to {destination}")
            self.transport.send((route.ip, route.port), ack_envelope)
            
        self.reliable_receiver = ReliableReceiver(
            self.peer_id,
            self.transport,
            self._handle_incoming_app_message,
            auto_register=True,
            ack_sender=ack_sender,
        )
        
        self.chatroom = ChatRoom(
            room_name="test-room",
            nickname=f"Nick-{peer_id}",
            peer_id=self.peer_id,
            p2p_host=self.host,
            delivery_manager=self.store_forward_manager,
        )

    def _handle_incoming_app_message(self, envelope: dict, address: tuple):
        payload = envelope.get("payload", {})
        self.chatroom._handle_incoming_message(payload)
        self.received_messages.append(payload)

    def connect_peer(self, remote_node: 'NodeHelper', ip: str = "127.0.0.1", port: int = 5001):
        self.host.connect_to_peer(ip, port, remote_node.peer_id)
        self.routing_table.add_route(remote_node.peer_id, remote_node.peer_id, ip, port)

    def disconnect_peer(self, remote_peer_id: str):
        with self.host.peer_lock:
            self.host.peers.pop(remote_peer_id, None)
        self.routing_table.remove_route(remote_peer_id)


@pytest.fixture
def tmp_path_str(tmpdir):
    return str(tmpdir)


def test_known_peer_preservation_and_offline_replay(tmp_path_str):
    """
    Required 10-step regression test scenario:
    1. Peer known while online.
    2. Peer disconnects.
    3. ChatRoom.publish("OFFLINE-TEST") is called.
    4. StoreForwardQueue contains exactly one destination-specific queued message.
    5. Destination is the known peer ID.
    6. Peer reconnects.
    7. Route recovery triggers replay automatically.
    8. Receiver application handler receives the message exactly once.
    9. ACK is processed.
    10. Queue pending count becomes zero.
    """
    transport = MockTransport()
    node_a = NodeHelper("node-a-1111", tmp_path_str, transport)
    node_b = NodeHelper("node-b-2222", tmp_path_str, transport)

    # 1. Peer known while online
    node_a.connect_peer(node_b, ip="127.0.0.1", port=5002)
    node_b.connect_peer(node_a, ip="127.0.0.1", port=5001)

    assert "node-b-2222" in node_a.host.get_known_peers()
    assert "node-a-1111" in node_b.host.get_known_peers()

    # 2. Peer disconnects
    node_a.disconnect_peer("node-b-2222")
    assert "node-b-2222" not in node_a.host.get_peers()
    assert "node-b-2222" in node_a.host.get_known_peers()  # Identity preserved!

    # 3. ChatRoom.publish("OFFLINE-TEST") is called
    ok = node_a.chatroom.publish("OFFLINE-TEST")
    assert ok is True

    # 4. StoreForwardQueue contains exactly one destination-specific queued message
    # 5. Destination is the known peer ID
    pending = node_a.queue.get_pending("node-b-2222")
    assert len(pending) == 1
    assert pending[0].destination == "node-b-2222"
    assert pending[0].state == "QUEUED"
    assert pending[0].envelope["payload"]["data"]["Message"] == "OFFLINE-TEST"

    # 6. Peer reconnects
    node_a.routing_table.add_route("node-b-2222", "node-b-2222", "127.0.0.1", 5002)
    
    # 7. Route recovery triggers replay automatically
    replay_results = node_a.store_forward_manager.on_route_recovered("node-b-2222")
    assert len(replay_results) == 1
    assert replay_results[0].status == "DELIVERED"

    # 8. Receiver application handler receives the message exactly once
    raw_b = node_b.chatroom.get_raw_messages()
    assert len(raw_b) == 1
    assert raw_b[0]["Message"] == "OFFLINE-TEST"

    # 9. ACK is processed & 10. Queue pending count becomes zero
    assert node_a.queue.pending_count("node-b-2222") == 0

    node_a.queue.close()
    node_b.queue.close()


def test_three_offline_messages_replay(tmp_path_str):
    """
    Test sending 3 offline messages (OFFLINE-REAL-001, 002, 003) to a known disconnected peer.
    """
    transport = MockTransport()
    node_a = NodeHelper("node-a-3333", tmp_path_str, transport)
    node_b = NodeHelper("node-b-4444", tmp_path_str, transport)

    node_a.connect_peer(node_b, ip="127.0.0.1", port=5002)
    node_b.connect_peer(node_a, ip="127.0.0.1", port=5001)

    # Disconnect peer B
    node_a.disconnect_peer("node-b-4444")

    # Send 3 offline messages
    node_a.chatroom.publish("OFFLINE-REAL-001")
    node_a.chatroom.publish("OFFLINE-REAL-002")
    node_a.chatroom.publish("OFFLINE-REAL-003")

    pending = node_a.queue.get_pending("node-b-4444")
    assert len(pending) == 3
    assert [p.envelope["payload"]["data"]["Message"] for p in pending] == [
        "OFFLINE-REAL-001", "OFFLINE-REAL-002", "OFFLINE-REAL-003"
    ]

    # Reconnect peer B & trigger route recovery
    node_a.routing_table.add_route("node-b-4444", "node-b-4444", "127.0.0.1", 5002)
    results = node_a.store_forward_manager.on_route_recovered("node-b-4444")

    assert len(results) == 3
    assert all(r.status == "DELIVERED" for r in results)

    raw_b = node_b.chatroom.get_raw_messages()
    assert len(raw_b) == 3
    assert [m["Message"] for m in raw_b] == [
        "OFFLINE-REAL-001", "OFFLINE-REAL-002", "OFFLINE-REAL-003"
    ]

    assert node_a.queue.pending_count("node-b-4444") == 0

    node_a.queue.close()
    node_b.queue.close()


def test_unique_transport_message_ids(tmp_path_str):
    """
    Test that every queued message has a unique message_id.
    """
    transport = MockTransport()
    node_a = NodeHelper("node-a-5555", tmp_path_str, transport)
    node_b = NodeHelper("node-b-6666", tmp_path_str, transport)

    node_a.connect_peer(node_b)
    node_a.disconnect_peer("node-b-6666")

    node_a.chatroom.publish("MSG-1")
    node_a.chatroom.publish("MSG-2")

    pending = node_a.queue.get_pending("node-b-6666")
    msg_ids = [p.message_id for p in pending]
    assert len(msg_ids) == 2
    assert len(set(msg_ids)) == 2

    node_a.queue.close()
    node_b.queue.close()


def test_duplicate_discovery_does_not_duplicate_replay(tmp_path_str):
    """
    Test that duplicate route recovery triggers do not duplicate replay or application delivery.
    """
    transport = MockTransport()
    node_a = NodeHelper("node-a-7777", tmp_path_str, transport)
    node_b = NodeHelper("node-b-8888", tmp_path_str, transport)

    node_a.connect_peer(node_b)
    node_b.connect_peer(node_a)
    node_a.disconnect_peer("node-b-8888")

    node_a.chatroom.publish("DUPLICATE-TEST")

    node_a.routing_table.add_route("node-b-8888", "node-b-8888", "127.0.0.1", 5002)

    # First route recovery
    res1 = node_a.store_forward_manager.on_route_recovered("node-b-8888")
    assert len(res1) == 1
    assert res1[0].status == "DELIVERED"

    # Second route recovery (duplicate discovery)
    res2 = node_a.store_forward_manager.on_route_recovered("node-b-8888")
    assert len(res2) == 0

    # Verify B received it only once
    assert len(node_b.chatroom.get_raw_messages()) == 1

    node_a.queue.close()
    node_b.queue.close()


def test_online_delivery(tmp_path_str):
    """
    Test that online delivery works directly and pending queue stays empty.
    """
    transport = MockTransport()
    node_a = NodeHelper("node-a-9999", tmp_path_str, transport)
    node_b = NodeHelper("node-b-0000", tmp_path_str, transport)

    node_a.connect_peer(node_b)
    node_b.connect_peer(node_a)

    ok = node_a.chatroom.publish("ONLINE-MSG")
    assert ok is True

    raw_b = node_b.chatroom.get_raw_messages()
    assert len(raw_b) == 1
    assert raw_b[0]["Message"] == "ONLINE-MSG"
    assert node_a.queue.pending_count("node-b-0000") == 0

    node_a.queue.close()
    node_b.queue.close()


def test_discovery_reconnection_triggers_route_recovery():
    from p2p.discovery import PeerDiscovery
    found_calls = []
    def on_peer_found(peer_id, ip, port):
        found_calls.append(peer_id)
        
    discovery = PeerDiscovery("my-peer-id", 5000, on_peer_found)
    discovery.rendezvous = "test-room"
    
    announcement = {'type': 'peer_announcement', 'peer_id': 'peer-X', 'p2p_port': 5001, 'rendezvous': 'test-room'}
    discovery._on_transport_message(announcement, ('127.0.0.1', 5001))
    assert len(found_calls) == 1

    # peer-X re-announces after restart/reconnection
    discovery._on_transport_message(announcement, ('127.0.0.1', 5001))
    assert len(found_calls) == 2, "Reconnection failed to trigger on_peer_found"


class FailingMockTransport(MockTransport):
    def __init__(self, fail_address: tuple):
        super().__init__()
        self.fail_address = fail_address

    def send(self, address: tuple, message: dict) -> None:
        if address == self.fail_address:
            raise ConnectionRefusedError(10061, "No connection could be made because the target machine actively refused it")
        super().send(address, message)


def test_stale_route_transport_failure_queues_message(tmp_path_str):
    """
    Scenario A: Stale-route transport failure handles WinError 10061 cleanly,
    invalidates the stale route, enqueues the message into SQLite, and returns QUEUED.
    """
    failing_transport = FailingMockTransport(('127.0.0.1', 5002))
    node_a = NodeHelper("node-a-stale1", tmp_path_str, failing_transport)
    node_b = NodeHelper("node-b-stale2", tmp_path_str, failing_transport)

    node_a.connect_peer(node_b, ip="127.0.0.1", port=5002)

    # Confirm route is present in routing table before send
    assert node_a.routing_table.route_available("node-b-stale2") is True

    # Send message while TCP endpoint actively refuses connection (simulating stopped peer)
    ok = node_a.chatroom.publish("OFFLINE-STALE-001")
    assert ok is True

    # Message must be in SQLite queue
    pending = node_a.queue.get_pending("node-b-stale2")
    assert len(pending) == 1
    assert pending[0].destination == "node-b-stale2"
    assert pending[0].state == "QUEUED"
    assert pending[0].envelope["payload"]["data"]["Message"] == "OFFLINE-STALE-001"

    # Stale route should now be invalidated
    assert node_a.routing_table.route_available("node-b-stale2") is False

    node_a.queue.close()
    node_b.queue.close()


def test_stale_route_multiple_offline_messages_and_recovery(tmp_path_str):
    """
    Scenario B & C: Multiple offline messages sent while route is stale,
    all enqueued into SQLite with unique IDs, and replayed on reconnection.
    """
    failing_transport = FailingMockTransport(('127.0.0.1', 5002))
    node_a = NodeHelper("node-a-stale3", tmp_path_str, failing_transport)
    node_b = NodeHelper("node-b-stale4", tmp_path_str, failing_transport)

    node_a.connect_peer(node_b, ip="127.0.0.1", port=5002)
    node_b.connect_peer(node_a, ip="127.0.0.1", port=5001)

    # Send 3 messages while transport fails
    node_a.chatroom.publish("OFFLINE-REAL-001")
    node_a.chatroom.publish("OFFLINE-REAL-002")
    node_a.chatroom.publish("OFFLINE-REAL-003")

    pending = node_a.queue.get_pending("node-b-stale4")
    assert len(pending) == 3
    msg_ids = [p.message_id for p in pending]
    assert len(set(msg_ids)) == 3

    # Reconnect B: clear transport failure
    failing_transport.fail_address = None

    node_a.routing_table.add_route("node-b-stale4", "node-b-stale4", "127.0.0.1", 5002)
    results = node_a.store_forward_manager.on_route_recovered("node-b-stale4")

    assert len(results) == 3
    assert all(r.status == "DELIVERED" for r in results)

    raw_b = node_b.chatroom.get_raw_messages()
    assert len(raw_b) == 3
    assert [m["Message"] for m in raw_b] == [
        "OFFLINE-REAL-001", "OFFLINE-REAL-002", "OFFLINE-REAL-003"
    ]
    assert node_a.queue.pending_count("node-b-stale4") == 0

    node_a.queue.close()
    node_b.queue.close()

