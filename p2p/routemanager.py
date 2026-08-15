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

    def send_route_advertisement(self, address):
        # advertise known destinations and metrics
        routes = {}
        for dest, mapping in self.routing_table._routes.items():
            # choose best metric among next_hops
            best = min((e.metric for e in mapping.values()), default=None)
            if best is not None:
                routes[dest] = best
        env = create_envelope('route_advertisement', source=self.node_id, payload={'routes': routes})
        self.transport.send(address, env)

    def _on_peer_discovered(self, peer_id: str, ip: str, port: int):
        # when a new peer is discovered, announce our known peers to it
        try:
            self.send_announcement((ip, port))
        except Exception:
            pass

    def _on_peer_lost(self, peer_id: str):
        # remove any routes that use this peer as next_hop
        # RoutingTable._routes maps destination -> (next_hop -> RouteEntry)
        for dest, mapping in list(self.routing_table._routes.items()):
            if peer_id in mapping:
                self.routing_table.remove_route(dest, peer_id)

    def _on_transport_message(self, msg, addr):
        try:
            validate_envelope(msg)
        except Exception:
            return

        mtype = msg.get('type')
        if mtype not in ('peer_announcement', 'route_advertisement'):
            return

        src = msg.get('source')
        if not src or src == self.node_id:
            return

        payload = msg.get('payload', {})
        if mtype == 'peer_announcement':
            peers = payload.get('peers', {})
            # peers expected as {peer_id: (ip, port)}
            for pid, (ip, port) in peers.items():
                if pid == self.node_id:
                    continue
                # add route via src to reach pid if none exists
                existing = self.routing_table.get_next_hop(pid)
                if existing is None:
                    try:
                        self.routing_table.add_route(pid, src, ip, port, metric=1, hops=1)
                    except Exception:
                        pass
        elif mtype == 'route_advertisement':
            routes = payload.get('routes', {})
            for dest, advertised_metric in routes.items():
                if dest == self.node_id:
                    continue
                # compute metric via src
                new_metric = int(advertised_metric) + 1
                # prevent storing routes that point back to us
                # if we already have a direct route (metric 0/1), prefer it
                existing_candidates = self.routing_table.list_routes().get(dest, {})
                best_existing = None
                if existing_candidates:
                    # existing_candidates: next_hop -> (ip,port,metric,status,hops)
                    best_existing = min((v[2] for v in existing_candidates.values()))
                # if existing route metric is better or equal, skip
                if best_existing is not None and best_existing <= new_metric:
                    continue
                # otherwise add/update route via src
                try:
                    self.routing_table.add_route(dest, src, addr[0], addr[1], metric=new_metric, hops=new_metric)
                except Exception:
                    pass
