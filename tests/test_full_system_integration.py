from __future__ import annotations

import pytest
from typing import List, Dict, Any

from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.transport import MockTransport

# Helper classes from test_store_forward_replay.py to isolate the test
class DummyRouteManager:
    def __init__(self):
        self.routes: Dict[str, bool] = {}

    def route_available(self, destination: str) -> bool:
        return self.routes.get(destination, False)

    def add_route(self, destination: str):
        self.routes[destination] = True

    def remove_route(self, destination: str):
        self.routes[destination] = False

class Node:
    """A helper class to encapsulate a full P2P stack for a test node."""
    def __init__(self, name: str, transport: MockTransport, route_manager: DummyRouteManager, tmp_path: str, *, with_sender: bool = True):
        self.name = name
        self.transport = transport
        self.route_manager = route_manager
        self.received_messages: List[Dict[str, Any]] = []

        self.receiver = ReliableReceiver(
            self.name,
            self.transport,
            self._handle_received_message,
        )

        if with_sender:
            self.sender = ReliableSender(self.name, self.transport, timeout=0.05, max_retries=2)

            db_path = f"{tmp_path}/{self.name}-s_f.sqlite"
            self.queue = StoreForwardQueue(db_path)

            self.manager = StoreForwardManager(
                queue=self.queue,
                reliable_sender=self.sender,
                route_manager=self.route_manager
            )

    def _handle_received_message(self, message: Dict[str, Any], addr: Any):
        self.received_messages.append(message['payload'])

    def send_message(self, destination: str, message: Dict[str, Any]):
        """Simulates the ChatRoom calling the delivery manager."""
        chat_message_payload = {
            'type': 'chat_message',
            'room': 'test-room',
            'data': message
        }
        return self.manager.send(destination, chat_message_payload)

@pytest.fixture
def tmp_path(tmpdir):
    return str(tmpdir)

def test_online_offline_replay_integration(tmp_path):
    """
    A full integration test for the scenario provided by the user.
    1. A and B are online, message is delivered.
    2. B goes offline, A sends a message, it gets queued.
    3. B comes back online, the message is replayed and delivered.
    """
    # Shared network components for the two nodes
    transport = MockTransport()
    route_manager = DummyRouteManager()

    # 1. Setup: Create two nodes, A and B.
    node_a = Node("A", transport, route_manager, tmp_path, with_sender=True)
    node_b = Node("B", transport, route_manager, tmp_path, with_sender=False)
    
    # --- Part 1: Online path ---
    # 2. Establish route between A and B.
    route_manager.add_route("B")

    # 3. A sends "hello-1" to B.
    msg1 = {"MessageID": "msg-1", "Message": "hello-1"}
    node_a.send_message("B", msg1)

    # 4. Verify B received it.
    assert len(node_b.received_messages) == 1
    assert node_b.received_messages[0]['data'] == msg1
    
    # 5. B sends an ACK (handled automatically by MockTransport and ReliableReceiver).
    # 6. Confirm no pending queue item on A for B.
    assert node_a.queue.pending_count("B") == 0

    # --- Part 2: Offline path ---
    # 7. Disconnect B (from A's perspective).
    route_manager.remove_route("B")

    # 8. A sends "hello-offline".
    msg2 = {"MessageID": "msg-2", "Message": "hello-offline"}
    result = node_a.send_message("B", msg2)
    assert result.status == "QUEUED"

    # 9. Confirm SQLite on A contains the pending message for B.
    assert node_a.queue.pending_count("B") == 1
    assert len(node_b.received_messages) == 1 # B has not received the new message
    
    pending_msg = node_a.queue.get_pending("B")[0]
    assert pending_msg.destination == "B"
    assert pending_msg.state == "QUEUED"
    # Check that the inner payload is correct
    assert pending_msg.envelope['payload']['data']['MessageID'] == "msg-2"
    assert pending_msg.message_id == result.message_id

    # --- Part 3: Replay path ---
    # 10. Reconnect B.
    route_manager.add_route("B")

    # 11. Route becomes available, A replays.
    # This is triggered by the on_peer_discovered -> on_route_recovered flow.
    replay_results = node_a.manager.on_route_recovered("B")
    assert len(replay_results) == 1
    assert replay_results[0].status == "DELIVERED"
    assert replay_results[0].message_id == pending_msg.message_id

    # 12. Verify B receives exactly one "hello-offline".
    assert len(node_b.received_messages) == 2
    assert node_b.received_messages[1]['data'] == msg2
    
    # 13. B's ACK reaches A (handled automatically).
    # 14. Verify SQLite pending count on A is now 0.
    assert node_a.queue.pending_count("B") == 0

    node_a.queue.close()
