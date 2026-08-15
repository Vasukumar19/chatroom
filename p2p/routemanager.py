"""RouteLearner/RouteManager: learns routes from peer announcements and peer events."""
from typing import Callable
import threading
import time

from p2p.protocol import create_envelope, validate_envelope
from p2p.routing import RoutingTable
from p2p.peermanager import PeerManager


class RouteLearner:
    def __init__(self, node_id: str, peer_manager: PeerManager, routing_table: RoutingTable, transport, *, min_advert_interval: float = 1.0):
        self.node_id = node_id
        self.peer_manager = peer_manager
        self.routing_table = routing_table
        self.transport = transport
        self.min_advert_interval = float(min_advert_interval)

        # advertisement rate-limiting/coalescing
        self._advert_lock = threading.Lock()
        self._last_advert_time = 0.0
        self._last_advert_hash = None
        self._scheduled_timer = None

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

    def _compute_routes_snapshot(self):
        routes = {}
        for dest, mapping in self.routing_table._routes.items():
            best = min((e.metric for e in mapping.values()), default=None)
            if best is not None:
                routes[dest] = best
        return routes

    def _ad_payload_hash(self, routes: dict):
        # deterministic hash for route payloads
        items = tuple(sorted(routes.items()))
        return hash(items)

    def request_advertisement(self):
        """Request that a route advertisement be sent to peers.
        This method coalesces requests and rate-limits actual sends.
        """
        with self._advert_lock:
            routes = self._compute_routes_snapshot()
            cur_hash = self._ad_payload_hash(routes)
            now = time.time()
            # suppress identical advertisement
            if self._last_advert_hash == cur_hash and now - self._last_advert_time < (self.min_advert_interval * 10):
                return

            # if last advert was recent, schedule for later
            delta = now - self._last_advert_time
            if delta < self.min_advert_interval:
                # schedule the send at last_advert_time + min_advert_interval
                when = self.min_advert_interval - delta
                if self._scheduled_timer is None:
                    self._scheduled_timer = threading.Timer(when, self._flush_scheduled_advertisement)
                    self._scheduled_timer.daemon = True
                    self._scheduled_timer.start()
                return

            # otherwise send immediately without re-entering the lock
            self._send_advertisement_locked()

    def _flush_scheduled_advertisement(self):
        with self._advert_lock:
            self._scheduled_timer = None
            self._send_advertisement_locked()

    def _send_advertisement_locked(self):
        # clear scheduled timer
        if self._scheduled_timer:
            try:
                self._scheduled_timer.cancel()
            except Exception:
                pass
            self._scheduled_timer = None

        routes = self._compute_routes_snapshot()
        cur_hash = self._ad_payload_hash(routes)
        now = time.time()
        # if identical to last and within interval suppress
        if self._last_advert_hash == cur_hash and now - self._last_advert_time < self.min_advert_interval:
            return

        # send to all known peers
        env = create_envelope('route_advertisement', source=self.node_id, payload={'routes': routes})
        peers = self.peer_manager.get_peers()
        for pid, (ip, port) in peers.items():
            try:
                self.transport.send((ip, port), env)
            except Exception:
                pass

        self._last_advert_time = time.time()
        self._last_advert_hash = cur_hash

    def _send_advertisement(self):
        # Backward compatibility shim: callers may still invoke this directly.
        # It deliberately avoids re-entering the same lock.
        with self._advert_lock:
            self._send_advertisement_locked()

    def _on_peer_discovered(self, peer_id: str, ip: str, port: int):
        # when a new peer is discovered, announce our known peers to it
        try:
            self.send_announcement((ip, port))
        except Exception:
            pass
        # schedule advertisement of our routes to propagate knowledge
        try:
            self.request_advertisement()
        except Exception:
            pass

    def _on_peer_lost(self, peer_id: str):
        # remove any routes that use this peer as next_hop
        # RoutingTable._routes maps destination -> (next_hop -> RouteEntry)
        for dest, mapping in list(self.routing_table._routes.items()):
            if peer_id in mapping:
                self.routing_table.remove_route(dest, peer_id)
        try:
            self.request_advertisement()
        except Exception:
            pass

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
                    else:
                        try:
                            self.request_advertisement()
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
                else:
                    try:
                        self.request_advertisement()
                    except Exception:
                        pass
