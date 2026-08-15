"""RouteManager: listens to heartbeat status changes and adjusts routing table entries."""
from typing import Optional

from p2p.routing import RoutingTable


class RouteManager:
    def __init__(self, routing_table: RoutingTable):
        self.routing_table = routing_table

    def on_peer_status_change(self, peer_id: str, status: str):
        # Map heartbeat statuses to route statuses
        # heartbeat statuses: ALIVE, SUSPECT, DEAD
        # route statuses: ACTIVE, SUSPECT, INVALID
        mapped = 'INVALID'
        if status == 'ALIVE':
            mapped = 'ACTIVE'
        elif status == 'SUSPECT':
            mapped = 'SUSPECT'
        elif status == 'DEAD':
            mapped = 'INVALID'

        self.routing_table.set_next_hop_status(peer_id, mapped)
