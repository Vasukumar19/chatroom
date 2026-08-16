"""Deterministic test networking helpers for multi-hop simulations."""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple, Any, Optional, Callable

from p2p.reliability import ReliableSender, ReliableReceiver
from p2p.transport import (
    MockTransport,
    MockEthernetTransport,
    MockBluetoothTransport,
    MockWiFiDirectTransport,
    MultiTransport,
)


class MeshMockNetwork:
    """A transport network that only delivers a message to the next hop the router requested."""

    def __init__(self, *, verbose: bool = True):
        self.nodes: Dict[str, "NetworkTransport"] = {}
        self.deliveries: List[Dict[str, Any]] = []
        self.verbose = verbose

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
        if self.verbose:
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


class _HeterogeneousLinkTransport:
    """Mixin that binds a named mock link technology to a simulated mesh."""

    def __init__(self, network, node_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network = network
        self.node_id = str(node_id)

    def send(self, address, message):
        self.sent_history.append((address, message))
        self.last_sent = message
        self.last_sent_addr = address
        if address:
            self.network.deliver(self.node_id, self.name, str(address[0]), message)


class HeterogeneousEthernetTransport(_HeterogeneousLinkTransport, MockEthernetTransport):
    pass


class HeterogeneousBluetoothTransport(_HeterogeneousLinkTransport, MockBluetoothTransport):
    pass


class HeterogeneousWiFiDirectTransport(_HeterogeneousLinkTransport, MockWiFiDirectTransport):
    pass


class HeterogeneousMeshNetwork:
    """Deterministic mesh with explicitly typed Ethernet/Bluetooth/Wi-Fi links.

    It models topology and adapter choice only.  It has no fabricated radio
    delay, loss, or bandwidth characteristics, so its metrics remain clearly
    labelled as simulated transport-dispatch overhead.
    """

    _transport_classes = {
        'ethernet': HeterogeneousEthernetTransport,
        'bluetooth': HeterogeneousBluetoothTransport,
        'wifi_direct': HeterogeneousWiFiDirectTransport,
    }

    def __init__(self):
        self.nodes = {}
        self.links = {}
        self.deliveries = []

    def add_node(self, node_id):
        node_id = str(node_id)
        adapters = {
            name: cls(self, node_id)
            for name, cls in self._transport_classes.items()
        }
        transport = MultiTransport(adapters)
        self.nodes[node_id] = transport
        return transport

    def connect(self, left, right, transport_name):
        if transport_name not in self._transport_classes:
            raise ValueError(f'Unsupported mock transport: {transport_name}')
        self.links[frozenset((str(left), str(right)))] = transport_name

    def deliver(self, source, transport_name, target, message):
        if self.links.get(frozenset((str(source), str(target)))) != transport_name:
            return
        receiver = self.nodes.get(str(target))
        if receiver is None:
            return
        self.deliveries.append({'source': source, 'target': target, 'transport': transport_name, 'message': message})
        receiver.transports[transport_name].simulate_incoming(message, (source, 0))


class DroppingNetworkTransport(NetworkTransport):
    """Drops the first N ACK messages leaving a node."""

    def __init__(self, network: MeshMockNetwork, node_id: str, *, drop_ack_count: int = 0, port: int = 0):
        super().__init__(network, node_id, port=port)
        self.drop_ack_count = int(drop_ack_count)
        self.acks_dropped = 0

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        if self.network.verbose:
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
            if self.network.verbose:
                print("DROP ACK", self.node_id, "->", address[0], message.get("message_id"), "remaining=", self.drop_ack_count - self.acks_dropped)
            self.sent_history.append((address, message))
            self.last_sent = message
            self.last_sent_addr = address
            return
        super().send(address, message)


class RouterAwareReliableSender(ReliableSender):
    """ReliableSender adapter that routes through a Router instead of bypassing it."""

    def __init__(self, node_id: str, router, transport, *, timeout: float = 0.25, max_retries: int = 3, on_failed: Optional[Callable[[str], None]] = None, security=None):
        self.router = router
        super().__init__(node_id, transport, timeout=timeout, max_retries=max_retries, on_failed=on_failed, security=security)

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
        if self.security:
            env = self.security.protect(env)
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

        self._send_routed(destination, env)
        return self._wait_for_ack(message_id, event)

    def _send_routed(self, destination, env):
        route = self.router.routing_table.get_route(destination)
        if not route:
            raise RuntimeError(f'No route to {destination}')
        self.router._send_on_route(route, env)

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
            self._send_routed(destination, env)

        if failed_callback:
            failed_callback(message_id)
        return False


class RouterAwareReliableReceiver(ReliableReceiver):
    """Reliable receiver whose ACKs are forwarded by a router-selected link."""

    def __init__(self, node_id, router, transport, app_handler=None, *, security=None):
        def send_ack(ack, destination):
            route = router.routing_table.get_route(destination)
            if not route:
                raise RuntimeError(f'No route to {destination}')
            router._send_on_route(route, ack)

        super().__init__(node_id, transport, app_handler, auto_register=False, security=security, ack_sender=send_ack)
