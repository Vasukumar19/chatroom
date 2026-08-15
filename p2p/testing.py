"""Deterministic test networking helpers for multi-hop simulations."""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple, Any, Optional, Callable

from p2p.reliability import ReliableSender, ReliableReceiver
from p2p.transport import MockTransport


class MeshMockNetwork:
    """A transport network that only delivers a message to the next hop the router requested."""

    def __init__(self):
        self.nodes: Dict[str, "NetworkTransport"] = {}
        self.deliveries: List[Dict[str, Any]] = []

    def add_node(self, node_id: str, transport_cls: type = None, *, port: int = 0, **transport_kwargs) -> "NetworkTransport":
        transport_cls = transport_cls or NetworkTransport
        transport = transport_cls(network=self, node_id=node_id, port=port, **transport_kwargs)
        self.nodes[node_id] = transport
        return transport

    def deliver(self, source_node: str, target_node: str, message: Dict[str, Any], *, source_addr: Tuple[str, int] = None):
        target = str(target_node)
        if target not in self.nodes:
            raise KeyError(f"Unknown node in mesh: {target}")

        receiver = self.nodes[target]
        source_addr = source_addr or (source_node, 0)
        self.deliveries.append({
            "source": source_node,
            "target": target,
            "message": message,
            "source_addr": source_addr,
        })
        print(
            "DELIVER",
            source_node,
            "->",
            target,
            message.get("type"),
            message.get("message_id"),
            message.get("destination"),
        )

        for handler in list(receiver.handlers):
            try:
                handler(message, source_addr)
            except Exception:
                pass


class NetworkTransport(MockTransport):
    """A transport bound to a mesh node. It performs exact one-hop delivery only."""

    def __init__(self, network: MeshMockNetwork, node_id: str, *, port: int = 0):
        super().__init__()
        self.network = network
        self.node_id = str(node_id)
        self.port = int(port)

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        self.sent_history.append((address, message))
        self.last_sent = message
        self.last_sent_addr = address

        destination = address[0] if isinstance(address, tuple) and address else None
        if destination is None:
            return

        try:
            self.network.deliver(self.node_id, str(destination), message, source_addr=(self.node_id, self.port))
        except KeyError:
            pass


class DroppingNetworkTransport(NetworkTransport):
    """Drops the first N ACK messages leaving a node."""

    def __init__(self, network: MeshMockNetwork, node_id: str, *, drop_ack_count: int = 0, port: int = 0):
        super().__init__(network, node_id, port=port)
        self.drop_ack_count = int(drop_ack_count)
        self.acks_dropped = 0

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        print(
            "DROP_CHECK",
            self.node_id,
            message.get("type"),
            message.get("message_id"),
            "remaining=",
            self.drop_ack_count - self.acks_dropped,
        )
        if message.get("type") == "ack" and self.acks_dropped < self.drop_ack_count:
            self.acks_dropped += 1
            print("DROP ACK", self.node_id, "->", address[0], message.get("message_id"), "remaining=", self.drop_ack_count - self.acks_dropped)
            self.sent_history.append((address, message))
            self.last_sent = message
            self.last_sent_addr = address
            return
        super().send(address, message)


class RouterAwareReliableSender(ReliableSender):
    """ReliableSender adapter that routes through a Router instead of bypassing it."""

    def __init__(self, node_id: str, router, transport, *, timeout: float = 0.25, max_retries: int = 3, on_failed: Optional[Callable[[str], None]] = None):
        self.router = router
        super().__init__(node_id, transport, timeout=timeout, max_retries=max_retries, on_failed=on_failed)

    def _next_hop_address(self, destination: str):
        nh = self.router.routing_table.get_next_hop(destination)
        if not nh:
            raise RuntimeError(f"No route to {destination}")
        _, ip, port = nh
        return (ip, port)

    def send(self, destination: str, payload: Dict[str, object]) -> bool:
        message_id = f"{self.node_id}:{int(threading.get_ident() * 1000)}:{len(self._pending)}"
        self.last_message_id = message_id
        self.last_status = 'SENT'
        self.retry_count = 0

        env = {
            'message_id': message_id,
            'type': 'data',
            'source': self.node_id,
            'destination': destination,
            'timestamp': str(__import__('time').time()),
            'ttl': 8,
            'priority': 0,
            'payload': payload,
            'protocol_version': '1',
            'delivery_attempt': 0,
        }
        event = threading.Event()

        with self._lock:
            self._pending[message_id] = {
                'destination': destination,
                'payload': payload,
                'env': env,
                'retries': 0,
                'deadline': __import__('time').time() + self.timeout,
                'status': 'SENT',
            }
            self._pending_events[message_id] = event

        self.transport.send(self._next_hop_address(destination), env)
        return self._wait_for_ack(message_id, event)

    def _wait_for_ack(self, message_id: str, event: threading.Event) -> bool:
        retries = 0
        failed_callback = None

        while True:
            if event.wait(self.timeout):
                with self._lock:
                    pending = self._pending.get(message_id)
                    if pending is None:
                        return self.last_status == 'ACKED'
                    if pending['status'] == 'ACKED':
                        self.last_status = 'ACKED'
                        self._pending.pop(message_id, None)
                        self._pending_events.pop(message_id, None)
                        return True
                    if pending['status'] == 'FAILED':
                        self.last_status = 'FAILED'
                        self._pending.pop(message_id, None)
                        self._pending_events.pop(message_id, None)
                        return False
                continue

            with self._lock:
                pending = self._pending.get(message_id)
                if pending is None:
                    return self.last_status == 'ACKED'
                if pending['status'] == 'ACKED':
                    self.last_status = 'ACKED'
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    return True
                if pending['status'] == 'FAILED':
                    self.last_status = 'FAILED'
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    return False
                if retries >= self.max_retries:
                    pending['status'] = 'FAILED'
                    self.last_status = 'FAILED'
                    self._pending.pop(message_id, None)
                    self._pending_events.pop(message_id, None)
                    failed_callback = self.on_failed
                    break

                retries += 1
                self.retry_count = retries
                pending['retries'] = retries
                pending['env']['delivery_attempt'] = retries
                pending['status'] = 'RETRYING'
                self.last_status = 'RETRYING'
                destination = pending['destination']
                env = pending['env']

            event.clear()
            self.transport.send(self._next_hop_address(destination), env)

        if failed_callback:
            failed_callback(message_id)
        return False


class RouteAwareReliableReceiver(ReliableReceiver):
    """ReliableReceiver adapter that acts as a router app handler without registering twice on the transport."""

    def __init__(self, node_id: str, transport, app_handler: Optional[Callable[[dict, tuple], None]] = None):
        self.node_id = node_id
        self.transport = transport
        self.app_handler = app_handler
        self.processed_message_ids = set()
        self._lock = threading.Lock()
        self._transport_handler = self._on_transport_message
