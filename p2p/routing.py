"""RoutingTable: simple transport-agnostic route store for Phase 5."""
import time
from typing import Dict, Optional, Tuple


class RouteEntry:
    def __init__(self, destination: str, next_hop: str, ip: str, port: int, metric: int = 1, hops: int = 1):
        self.destination = destination
        self.next_hop = next_hop
        self.ip = ip
        self.port = int(port)
        self.metric = metric
        self.hops = hops
        self.status = 'ACTIVE'
        self.last_updated = time.time()


class RoutingTable:
    def __init__(self):
        # dest -> (next_hop -> RouteEntry)
        self._routes: Dict[str, Dict[str, RouteEntry]] = {}

    def add_route(self, destination: str, next_hop: str, ip: str, port: int, metric: int = 1, hops: int = 1):
        self._routes.setdefault(destination, {})
        self._routes[destination][next_hop] = RouteEntry(destination, next_hop, ip, port, metric, hops)

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
        entries = self._routes.get(destination)
        if not entries:
            return None
        # prefer ACTIVE routes with lowest metric, then SUSPECT
        candidates = list(entries.values())
        active = [e for e in candidates if e.status == 'ACTIVE']
        suspect = [e for e in candidates if e.status == 'SUSPECT']
        chosen = None
        if active:
            chosen = sorted(active, key=lambda x: x.metric)[0]
        elif suspect:
            chosen = sorted(suspect, key=lambda x: x.metric)[0]
        else:
            return None
        return (chosen.next_hop, chosen.ip, chosen.port)

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
