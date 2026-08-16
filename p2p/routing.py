"""RoutingTable: simple transport-agnostic route store for Phase 5."""
import time
from typing import Dict, Optional, Tuple


class RouteEntry:
    def __init__(self, destination: str, next_hop: str, ip: str, port: int, metric: int = 1, hops: int = 1, transport: Optional[str] = None):
        self.destination = destination
        self.next_hop = next_hop
        self.ip = ip
        self.port = int(port)
        self.metric = metric
        self.hops = hops
        self.transport = transport
        self.status = 'ACTIVE'
        self.last_updated = time.time()


class RoutingTable:
    def __init__(self):
        # dest -> (next_hop -> RouteEntry)
        self._routes: Dict[str, Dict[str, RouteEntry]] = {}

    def add_route(self, destination: str, next_hop: str, ip: str, port: int, metric: int = 1, hops: int = 1, transport: Optional[str] = None):
        self._routes.setdefault(destination, {})
        self._routes[destination][next_hop] = RouteEntry(destination, next_hop, ip, port, metric, hops, transport)

    def get_route(self, destination: str) -> Optional[RouteEntry]:
        """Return the selected route, including its optional link type."""
        entries = self._routes.get(destination)
        if not entries:
            return None
        candidates = list(entries.values())
        active = [entry for entry in candidates if entry.status == 'ACTIVE']
        suspect = [entry for entry in candidates if entry.status == 'SUSPECT']
        selected = active or suspect
        return sorted(selected, key=lambda entry: entry.metric)[0] if selected else None

    def remove_route(self, destination: str, next_hop: Optional[str] = None):
        if destination not in self._routes:
            return
        if next_hop is None:
            del self._routes[destination]
            return
        self._routes[destination].pop(next_hop, None)
        if not self._routes[destination]:
            del self._routes[destination]

    def get_next_hop(self, destination: str) -> Optional[Tuple[str, str, int]]:
        chosen = self.get_route(destination)
        if not chosen:
            return None
        return (chosen.next_hop, chosen.ip, chosen.port)

    def route_available(self, destination: str) -> bool:
        """Small adapter used by StoreForwardManager."""
        return self.get_route(destination) is not None

    def set_next_hop_status(self, next_hop: str, status: str):
        # update all route entries that use next_hop
        for dest, m in self._routes.items():
            if next_hop in m:
                m[next_hop].status = status
                m[next_hop].last_updated = time.time()

    def set_status(self, destination: str, status: str):
        # Backwards-compatible: set status for all next_hops for a destination
        if destination not in self._routes:
            return
        for nh, entry in self._routes[destination].items():
            entry.status = status
            entry.last_updated = time.time()

    def list_routes(self):
        out = {}
        for d, m in self._routes.items():
            out[d] = {nh: (e.ip, e.port, e.metric, e.status, e.hops) for nh, e in m.items()}
        return out
