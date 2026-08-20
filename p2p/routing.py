"""RoutingTable: transport-agnostic dynamic weighted route store."""
import time
from typing import Dict, Optional, Tuple

BASE_COST = 100
MAX_ROUTE_HOPS = 16


class RouteEntry:
    def __init__(self, destination: str, next_hop: str, ip: str, port: int, metric: int = BASE_COST, hops: int = 1, transport: Optional[str] = None):
        self.destination = destination
        self.next_hop = next_hop
        self.ip = ip
        self.port = int(port)
        self.metric = int(metric)
        self.hops = int(hops)
        self.transport = transport
        self.status = 'ACTIVE'
        self.last_updated = time.time()


class RoutingTable:
    """Manages the mapping of destination node IDs to the best available next-hop."""

    def __init__(self):
        # dest -> (next_hop -> RouteEntry)
        self._routes: Dict[str, Dict[str, RouteEntry]] = {}
        self._active_selection: Dict[str, str] = {}  # dest -> selected next_hop
        self.on_route_recovered_callbacks = []

    def add_route_recovery_callback(self, cb):
        self.on_route_recovered_callbacks.append(cb)

    def add_route(self, destination: str, next_hop: str, ip: str, port: int, metric: int = BASE_COST, hops: int = 1, transport: Optional[str] = None) -> bool:
        """Add or update route. Returns True if route is new or changed."""
        is_new = destination not in self._routes
        existing = self._routes.get(destination, {}).get(next_hop)
        changed = (
            is_new
            or existing is None
            or existing.metric != int(metric)
            or existing.hops != int(hops)
            or existing.ip != ip
            or existing.port != int(port)
            or existing.status != 'ACTIVE'
        )
        self._routes.setdefault(destination, {})
        self._routes[destination][next_hop] = RouteEntry(destination, next_hop, ip, port, metric, hops, transport)
        if is_new:
            for cb in self.on_route_recovered_callbacks:
                try:
                    cb(destination)
                except Exception:
                    pass
        return changed

    def get_route(self, destination: str) -> Optional[RouteEntry]:
        """Return the best route selected by lowest weighted metric with hysteresis protection."""
        entries = self._routes.get(destination)
        if not entries:
            self._active_selection.pop(destination, None)
            return None

        candidates = list(entries.values())
        active = [entry for entry in candidates if entry.status == 'ACTIVE']
        suspect = [entry for entry in candidates if entry.status == 'SUSPECT']
        available = active or suspect
        if not available:
            self._active_selection.pop(destination, None)
            return None

        # Sorted candidates: 1. metric (lowest cost), 2. hops, 3. next_hop
        sorted_candidates = sorted(available, key=lambda entry: (entry.metric, entry.hops, str(entry.next_hop)))
        best_candidate = sorted_candidates[0]

        # Check if we currently have an active selected next_hop
        current_nh = self._active_selection.get(destination)
        current_entry = entries.get(current_nh) if current_nh else None

        if current_entry and current_entry in available:
            if best_candidate.next_hop == current_nh:
                return best_candidate

            # Check hysteresis threshold (for legacy unscaled metrics < 50, threshold is 0)
            if current_entry.metric < 50:
                threshold = 0
            else:
                threshold = max(25, int(0.15 * current_entry.metric))

            if best_candidate.metric < (current_entry.metric - threshold):
                # Significant improvement: switch to best candidate
                self._active_selection[destination] = best_candidate.next_hop
                return best_candidate
            else:
                # Flapping prevention: keep current route
                return current_entry
        else:
            # Current route not available: switch immediately to best candidate
            self._active_selection[destination] = best_candidate.next_hop
            return best_candidate

    def remove_route(self, destination: str, next_hop: Optional[str] = None):
        if destination not in self._routes:
            return
        if next_hop is None:
            del self._routes[destination]
            self._active_selection.pop(destination, None)
            return
        self._routes[destination].pop(next_hop, None)
        if self._active_selection.get(destination) == next_hop:
            self._active_selection.pop(destination, None)
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
                if status not in ('ACTIVE', 'ALIVE'):
                    if self._active_selection.get(dest) == next_hop:
                        self._active_selection.pop(dest, None)

    def set_status(self, destination: str, status: str):
        # Backwards-compatible: set status for all next_hops for a destination
        if destination not in self._routes:
            return
        for nh, entry in self._routes[destination].items():
            entry.status = status
            entry.last_updated = time.time()
            if status not in ('ACTIVE', 'ALIVE'):
                if self._active_selection.get(destination) == nh:
                    self._active_selection.pop(destination, None)

    def list_routes(self):
        out = {}
        for d, m in self._routes.items():
            out[d] = {nh: (e.ip, e.port, e.metric, e.status, e.hops) for nh, e in m.items()}
        return out
