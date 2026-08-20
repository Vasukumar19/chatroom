"""Router: central message forwarding and loop prevention."""
import threading
import time
from typing import Callable, Dict, Optional, Tuple, Set

from p2p.protocol import validate_envelope, create_envelope
from p2p.routing import RoutingTable
from p2p.log import get_logger

log = get_logger("p2p.router")


class Router:
    def __init__(self, node_id: str, transport, routing_table: RoutingTable, *, seen_ttl: float = 60.0, seen_cleanup_interval: int = 100):
        self.node_id = node_id
        self.transport = transport
        self.routing_table = routing_table

        self.app_handlers = []
        # seen: message_id -> timestamp
        self.seen = {}
        self._lock = threading.Lock()
        self._seen_ttl = float(seen_ttl)
        self._seen_cleanup_interval = int(seen_cleanup_interval)
        self._seen_checks = 0

    def start(self):
        # register to receive incoming transport messages
        try:
            self.transport.register_handler(self._on_transport_message)
        except Exception:
            pass

    def stop(self):
        try:
            if hasattr(self.transport, 'unregister_handler'):
                self.transport.unregister_handler(self._on_transport_message)
        except Exception:
            pass

    def add_app_handler(self, handler: Callable[[dict, tuple], None]):
        self.app_handlers.append(handler)

    def send(self, destination: str, payload: dict, msg_type: str = 'data'):
        env = create_envelope(msg_type, source=self.node_id, payload=payload, destination=destination)
        route = self.routing_table.get_route(destination)
        if not route:
            raise RuntimeError('No route to destination')
        self._send_on_route(route, env)

    def _send_on_route(self, route, message):
        address = (route.ip, route.port)
        if route.transport and hasattr(self.transport, 'send_via'):
            self.transport.send_via(route.transport, address, message)
        else:
            self.transport.send(address, message)

    def _on_transport_message(self, msg, addr):
        # validate
        try:
            validate_envelope(msg)
        except Exception:
            return

        mid = msg.get('message_id')
        if not mid:
            return

        seen_key = (msg.get('type'), mid, msg.get('delivery_attempt', 0))

        now = time.time()
        with self._lock:
            # periodic cleanup
            self._seen_checks += 1
            if self._seen_checks >= self._seen_cleanup_interval:
                self._cleanup_seen(now)
                self._seen_checks = 0

            if seen_key in self.seen:
                return
            self.seen[seen_key] = now

        # ttl/hop_count handling
        ttl = msg.get('ttl', 0)
        if ttl <= 0:
            return
        msg['ttl'] = ttl - 1
        msg['hop_count'] = msg.get('hop_count', 0) + 1

        dest = msg.get('destination')
        if dest == self.node_id:
            # deliver to application handlers
            for h in list(self.app_handlers):
                try:
                    h(msg, addr)
                except Exception:
                    pass
            return

        # lookup next hop
        route = self.routing_table.get_route(dest) if dest else None
        if not route:
            # no route or next hop unavailable
            return
        try:
            log.info(f"forwarding to {dest} via next_hop={route.next_hop}", extra={"node_id": self.node_id, "dest": dest, "next_hop": route.next_hop, "ip": route.ip, "port": route.port})
            self._send_on_route(route, msg)
        except Exception as e:
            log.error(f"failed to forward to {dest} via {route.next_hop}: {e}", extra={"node_id": self.node_id})

    def _cleanup_seen(self, now: float):
        # Remove seen entries older than TTL
        to_remove = [mid for mid, ts in self.seen.items() if now - ts > self._seen_ttl]
        for mid in to_remove:
            try:
                del self.seen[mid]
            except KeyError:
                pass
        
