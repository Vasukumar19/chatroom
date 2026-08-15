"""RouteManager: stabilizes peer status changes and updates RoutingTable.

This class debounces rapid peer status changes and requests route
advertisements from the RouteLearner only when stabilized.
"""
from typing import Optional
import threading
from p2p.routing import RoutingTable


class RouteManager:
    def __init__(self, routing_table: RoutingTable, route_learner=None, *, stabilization_delay: float = 0.0):
        self.routing_table = routing_table
        self.route_learner = route_learner
        self.stabilization_delay = float(stabilization_delay)

        # per-peer timers for debouncing
        self._timers = {}
        self._lock = threading.Lock()

    def _map_status(self, status: str) -> str:
        mapped = 'INVALID'
        if status == 'ALIVE':
            mapped = 'ACTIVE'
        elif status == 'SUSPECT':
            mapped = 'SUSPECT'
        elif status == 'DEAD':
            mapped = 'INVALID'
        return mapped

    def on_peer_status_change(self, peer_id: str, status: str):
        mapped = self._map_status(status)

        if self.stabilization_delay <= 0:
            # immediate apply
            self._apply_status(peer_id, mapped)
            return

        with self._lock:
            # cancel previous timer if any
            t = self._timers.get(peer_id)
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            # schedule a new timer to apply status after stabilization delay
            timer = threading.Timer(self.stabilization_delay, self._apply_status, args=(peer_id, mapped))
            timer.daemon = True
            self._timers[peer_id] = timer
            timer.start()

    def _apply_status(self, peer_id: str, mapped: str):
        # apply mapped status to routing table
        try:
            self.routing_table.set_next_hop_status(peer_id, mapped)
        except Exception:
            pass

        # request advertisement to inform neighbors of stabilized change
        if self.route_learner:
            try:
                self.route_learner.request_advertisement()
            except Exception:
                pass

    def attach_to_heartbeat(self, heartbeat_manager):
        try:
            heartbeat_manager.on_status_change = self.on_peer_status_change
        except Exception:
            pass
