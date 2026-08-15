"""RouteLearner/RouteManager: learns routes from peer announcements and peer events."""
from typing import Callable

from p2p.protocol import create_envelope, validate_envelope
from p2p.routing import RoutingTable
from p2p.peermanager import PeerManager


class RouteLearner:
    def __init__(self, node_id: str, peer_manager: PeerManager, routing_table: RoutingTable, transport):
        self.node_id = node_id
        self.peer_manager = peer_manager
        self.routing_table = routing_table
        self.transport = transport

        # register callbacks
        self.peer_manager.on_peer_discovered = self._on_peer_discovered
        self.peer_manager.on_peer_lost = self._on_peer_lost

    def start(self):
        # register handler for peer announcements
        try:
            self.transport.register_handler(self._on_transport_message)
        except Exception:
            pass
        # send initial announcements to known peers
        self.announce_to_all()

    def announce_to_all(self):
        peers = self.peer_manager.get_peers()
        for pid, (ip, port) in peers.items():
            try:
                self.send_announcement((ip, port))
            except Exception:
                pass

    def send_announcement(self, address):
        payload = {'peers': self.peer_manager.get_peers()}
        env = create_envelope('peer_announcement', source=self.node_id, payload=payload)
        self.transport.send(address, env)

    def _on_peer_discovered(self, peer_id: str, ip: str, port: int):
        # when a new peer is discovered, announce our known peers to it
        try:
            self.send_announcement((ip, port))
        except Exception:
            pass

    def _on_peer_lost(self, peer_id: str):
        # remove any routes that use this peer as next_hop
        for dest, entry in list(self.routing_table._routes.items()):
            if entry.next_hop == peer_id:
                self.routing_table.remove_route(dest)

    def _on_transport_message(self, msg, addr):
        try:
            validate_envelope(msg)
        except Exception:
            return

        if msg.get('type') != 'peer_announcement':
            return

        src = msg.get('source')
        if not src or src == self.node_id:
            return

        payload = msg.get('payload', {})
        peers = payload.get('peers', {})
        # peers expected as {peer_id: (ip, port)}
        for pid, (ip, port) in peers.items():
            if pid == self.node_id:
                continue
            # add route via src to reach pid
            # do not overwrite existing direct routes
            existing = self.routing_table.get_next_hop(pid)
            if existing is None:
                try:
                    self.routing_table.add_route(pid, src, ip, port)
                except Exception:
                    pass
