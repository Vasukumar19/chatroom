"""Regression tests for persistent peer identity (peer_identity.json).

Tests
-----
1. Same ID after restart (same identity_dir → same peer_id).
2. Different nodes have different IDs (different identity dirs).
3. Offline queue survives peer restart (peer_id stable so queued messages replay).
4. Port change does not change peer_id.
5. Existing test suites still pass (invoked via full pytest run).
"""
import os
import pytest

from p2p.host import P2PHost
from p2p.identity import load_or_create_identity
from p2p.routing import RoutingTable
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.reliability import ReliableSender, ReliableReceiver
from p2p.transport import MockTransport
from p2p.chatroom import ChatRoom


# ---------------------------------------------------------------------------
# Test 1 — Same ID after restart
# ---------------------------------------------------------------------------

def test_same_id_after_restart(tmp_path):
    """peer_id must survive P2PHost recreation when identity_dir is provided."""
    identity_dir = str(tmp_path)

    host_a = P2PHost(port=0, identity_dir=identity_dir)
    id1 = host_a.peer_id

    # Simulate restart: create a brand-new P2PHost using the same directory.
    host_b = P2PHost(port=0, identity_dir=identity_dir)
    id2 = host_b.peer_id

    assert id1 == id2, f"Expected stable peer_id across restart; got {id1!r} then {id2!r}"


# ---------------------------------------------------------------------------
# Test 2 — Different nodes have different IDs
# ---------------------------------------------------------------------------

def test_different_nodes_have_different_ids(tmp_path):
    """Two nodes with different identity directories must get different IDs."""
    dir_a = str(tmp_path / "node_a")
    dir_b = str(tmp_path / "node_b")

    host_a = P2PHost(port=0, identity_dir=dir_a)
    host_b = P2PHost(port=0, identity_dir=dir_b)

    assert host_a.peer_id != host_b.peer_id


# ---------------------------------------------------------------------------
# Test 3 — Offline queue survives peer restart
# ---------------------------------------------------------------------------

class _NodeHelper:
    def __init__(self, peer_id: str, tmp_dir: str, transport: MockTransport):
        self.peer_id = peer_id
        self.transport = transport

        self.routing_table = RoutingTable()

        def address_resolver(dest):
            route = self.routing_table.get_route(dest)
            if not route:
                raise RuntimeError(f"No route to {dest}")
            return (route.ip, route.port)

        self.reliable_sender = ReliableSender(
            peer_id, transport, address_resolver=address_resolver,
            timeout=0.1, max_retries=2,
        )
        db = os.path.join(tmp_dir, f"dc-{peer_id}.sqlite")
        self.queue = StoreForwardQueue(db)
        self.sfm = StoreForwardManager(
            queue=self.queue,
            reliable_sender=self.reliable_sender,
            route_manager=self.routing_table,
        )
        self.received = []

        def ack_sender(ack, destination):
            route = self.routing_table.get_route(destination)
            if not route:
                return
            self.transport.send((route.ip, route.port), ack)

        self.receiver = ReliableReceiver(
            peer_id, transport,
            lambda env, addr: self.received.append(env.get("payload", {})),
            auto_register=True, ack_sender=ack_sender,
        )
        self.chatroom = ChatRoom(
            room_name="test-room",
            nickname=f"Nick-{peer_id}",
            peer_id=peer_id,
            p2p_host=None,
            delivery_manager=self.sfm,
        )

    def connect(self, other: "_NodeHelper", ip="127.0.0.1", port=5000):
        self.routing_table.add_route(other.peer_id, other.peer_id, ip, port)

    def close(self):
        self.queue.close()


def test_offline_queue_survives_peer_restart(tmp_path):
    """
    Scenario:
    - A and B online → online delivery works.
    - B goes offline; A queues 3 messages for B's stable peer_id.
    - B restarts with the SAME identity (same peer_id).
    - Route recovered → all 3 messages replayed exactly once.
    - pending_count == 0.
    """
    transport = MockTransport()
    tmp = str(tmp_path)
    id_dir_b = str(tmp_path / "b_identity")

    # First start of B
    peer_id_b_first = load_or_create_identity(id_dir_b)

    node_a = _NodeHelper("node-a-restart1", tmp, transport)
    node_b1 = _NodeHelper(peer_id_b_first, tmp, transport)

    node_a.connect(node_b1, port=5001)
    node_b1.connect(node_a, port=5002)

    # Online delivery
    result = node_a.sfm.send(peer_id_b_first, {"Message": "ONLINE-REAL-001"})
    assert result.status == "DELIVERED"

    # B goes offline — remove route from A
    node_a.routing_table.remove_route(peer_id_b_first)
    node_b1.close()

    # A queues 3 messages
    node_a.sfm.send(peer_id_b_first, {"Message": "OFFLINE-001"})
    node_a.sfm.send(peer_id_b_first, {"Message": "OFFLINE-002"})
    node_a.sfm.send(peer_id_b_first, {"Message": "OFFLINE-003"})

    assert node_a.queue.pending_count(peer_id_b_first) == 3

    # "Restart" B — reload the SAME identity
    peer_id_b_second = load_or_create_identity(id_dir_b)
    assert peer_id_b_second == peer_id_b_first, "Peer ID must survive restart"

    tmp_b2 = str(tmp_path / "b2_data")
    os.makedirs(tmp_b2, exist_ok=True)
    node_b2 = _NodeHelper(peer_id_b_second, tmp_b2, transport)

    # Route recovery
    node_a.connect(node_b2, port=5003)
    results = node_a.sfm.on_route_recovered(peer_id_b_second)

    assert len(results) == 3
    assert all(r.status == "DELIVERED" for r in results)
    assert node_a.queue.pending_count(peer_id_b_first) == 0

    # B2 received all 3 messages exactly once
    messages = [m.get("Message") for m in node_b2.received]
    assert sorted(messages) == ["OFFLINE-001", "OFFLINE-002", "OFFLINE-003"]

    node_a.close()
    node_b2.close()


# ---------------------------------------------------------------------------
# Test 4 — Port change does not change peer_id
# ---------------------------------------------------------------------------

def test_port_change_does_not_change_peer_id(tmp_path):
    """Peer restarts on a different port but must keep the same peer_id."""
    identity_dir = str(tmp_path)

    host_port_a = P2PHost(port=5100, identity_dir=identity_dir)
    id_before = host_port_a.peer_id

    # "Restart" on different port
    host_port_b = P2PHost(port=5200, identity_dir=identity_dir)
    id_after = host_port_b.peer_id

    assert id_before == id_after, "peer_id must not change when port changes"


# ---------------------------------------------------------------------------
# Test 5 — load_or_create_identity handles malformed file gracefully
# ---------------------------------------------------------------------------

def test_malformed_identity_file_regenerates(tmp_path):
    """A malformed identity file must be regenerated, not crash."""
    identity_dir = str(tmp_path)
    identity_path = os.path.join(identity_dir, "peer_identity.json")

    os.makedirs(identity_dir, exist_ok=True)
    with open(identity_path, "w") as f:
        f.write("NOT VALID JSON {{{")

    peer_id = load_or_create_identity(identity_dir)
    assert isinstance(peer_id, str)
    assert len(peer_id) == 8

    # And a second call should return the same regenerated id
    peer_id2 = load_or_create_identity(identity_dir)
    assert peer_id == peer_id2


# ---------------------------------------------------------------------------
# Test 6 — identity_dir=None (tests path) still generates ephemeral ID
# ---------------------------------------------------------------------------

def test_no_identity_dir_generates_ephemeral_id():
    """P2PHost without identity_dir still works (transient uuid)."""
    h1 = P2PHost(port=0)
    h2 = P2PHost(port=0)
    # Both should have valid 8-char IDs but they will differ
    assert len(h1.peer_id) == 8
    assert len(h2.peer_id) == 8
    assert h1.peer_id != h2.peer_id
