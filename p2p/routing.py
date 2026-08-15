"""RoutingTable: simple transport-agnostic route store for Phase 5."""
import time
from typing import Dict, Optional, Tuple


class RouteEntry:
    def __init__(self, destination: str, next_hop: str, ip: str, port: int, metric: int = 1):
        self.destination = destination
        self.next_hop = next_hop
        self.ip = ip
        self.port = int(port)
        self.metric = metric
        self.status = 'ALIVE'
        self.last_updated = time.time()


class RoutingTable:
    def __init__(self):
        # dest -> RouteEntry
        self._routes: Dict[str, RouteEntry] = {}

    def add_route(self, destination: str, next_hop: str, ip: str, port: int, metric: int = 1):
        self._routes[destination] = RouteEntry(destination, next_hop, ip, port, metric)

    def remove_route(self, destination: str):
        if destination in self._routes:
            del self._routes[destination]

    def get_next_hop(self, destination: str) -> Optional[Tuple[str, str, int]]:
        entry = self._routes.get(destination)
        if not entry:
            return None
        if entry.status == 'DEAD':
            return None
        return (entry.next_hop, entry.ip, entry.port)

    def set_status(self, destination: str, status: str):
        if destination in self._routes:
            self._routes[destination].status = status
            self._routes[destination].last_updated = time.time()

    def list_routes(self):
        return {d: (e.next_hop, e.ip, e.port, e.metric, e.status) for d, e in self._routes.items()}
