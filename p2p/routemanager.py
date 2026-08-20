"""RouteLearner/RouteManager: learns dynamic weighted routes from peer announcements and events."""
from typing import Callable, Optional
import threading
import time

from p2p.protocol import create_envelope, validate_envelope
from p2p.routing import RoutingTable, BASE_COST, MAX_ROUTE_HOPS
from p2p.peermanager import PeerManager
from p2p.log import get_logger

log = get_logger("p2p.routemanager")


class RouteLearner:
    def __init__(
        self,
        node_id: str,
        peer_manager: PeerManager,
        routing_table: RoutingTable,
        transport,
        *,
        reliable_sender=None,
        min_advert_interval: float = 1.0,
    ):
        self.node_id = node_id
        self.peer_manager = peer_manager
        self.routing_table = routing_table
        self.transport = transport
        self.reliable_sender = reliable_sender
        self.min_advert_interval = float(min_advert_interval)

        # advertisement rate-limiting/coalescing
        self._advert_lock = threading.RLock()
        self._last_advert_time = 0.0
        self._last_advert_hash = None
        self._scheduled_timer = None

        # register callbacks
        self.peer_manager.on_peer_discovered = self._on_peer_discovered
        self.peer_manager.on_peer_lost = self._on_peer_lost

    def set_reliable_sender(self, reliable_sender):
        """Attach reliable sender for real RTT and retry metrics."""
        self.reliable_sender = reliable_sender

    def get_link_cost(self, neighbor: str) -> int:
        """Calculate dynamic weighted cost for direct link to neighbor."""
        cost = BASE_COST

        # 1. RTT penalty: rtt_ms / 10
        # 2. Retry penalty: 150 * retry_rate
        if self.reliable_sender and hasattr(self.reliable_sender, "get_peer_link_metrics"):
            metrics = self.reliable_sender.get_peer_link_metrics(neighbor)
            if metrics:
                if metrics.rtt_ms is not None:
                    cost += int(metrics.rtt_ms / 10.0)
                if metrics.retry_rate is not None:
                    cost += int(150.0 * metrics.retry_rate)

        # 3. Congestion penalty: 100 * queue_pressure
        if self.transport and hasattr(self.transport, "get_queue_pressure"):
            qp = self.transport.get_queue_pressure()
            cost += int(100.0 * qp)

        return int(cost)

    def start(self):
        # register handler for peer announcements and route advertisements
        try:
            self.transport.register_handler(self._on_transport_message)
        except Exception:
            pass
        # send initial announcements to known peers
        self.announce_to_all()

    def stop(self):
        with self._advert_lock:
            if self._scheduled_timer:
                try:
                    self._scheduled_timer.cancel()
                except Exception:
                    pass
                self._scheduled_timer = None

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
        routes, hops = self._compute_routes_snapshot()
        env = create_envelope('route_advertisement', source=self.node_id, payload={'routes': routes, 'hops': hops})
        self.transport.send(address, env)

    def _compute_routes_snapshot(self):
        routes = {}
        hops = {}
        for dest, mapping in self.routing_table._routes.items():
            best_entry = self.routing_table.get_route(dest)
            if best_entry is not None:
                routes[dest] = best_entry.metric
                hops[dest] = best_entry.hops
        return routes, hops

    def _ad_payload_hash(self, routes: dict):
        items = tuple(sorted(routes.items()))
        return hash(items)

    def request_advertisement(self, force: bool = False):
        """Request that a route advertisement be sent to peers.
        This method coalesces requests and rate-limits actual sends.
        """
        with self._advert_lock:
            routes, _ = self._compute_routes_snapshot()
            cur_hash = self._ad_payload_hash(routes)
            now = time.time()
            if not force and self._last_advert_hash == cur_hash and now - self._last_advert_time < (self.min_advert_interval * 10):
                return

            delta = now - self._last_advert_time
            if not force and delta < self.min_advert_interval:
                when = self.min_advert_interval - delta
                if self._scheduled_timer is None:
                    self._scheduled_timer = threading.Timer(when, self._flush_scheduled_advertisement)
                    self._scheduled_timer.daemon = True
                    self._scheduled_timer.start()
                return

            self._send_advertisement_locked(force=force)

    def _flush_scheduled_advertisement(self):
        with self._advert_lock:
            self._scheduled_timer = None
            self._send_advertisement_locked()

    def _send_advertisement_locked(self, force: bool = False):
        if self._scheduled_timer:
            try:
                self._scheduled_timer.cancel()
            except Exception:
                pass
            self._scheduled_timer = None

        routes, hops = self._compute_routes_snapshot()
        cur_hash = self._ad_payload_hash(routes)
        now = time.time()
        if not force and self._last_advert_hash == cur_hash and now - self._last_advert_time < self.min_advert_interval:
            return

        peers = self.peer_manager.get_peers()
        for pid, (ip, port) in peers.items():
            try:
                env = create_envelope('route_advertisement', source=self.node_id, payload={'routes': routes, 'hops': hops})
                self.transport.send((ip, port), env)
            except Exception:
                pass

        self._last_advert_time = time.time()
        self._last_advert_hash = cur_hash

    def _send_advertisement(self):
        with self._advert_lock:
            self._send_advertisement_locked()

    def _on_peer_discovered(self, peer_id: str, ip: str, port: int):
        link_cost = self.get_link_cost(peer_id)
        self.routing_table.add_route(peer_id, peer_id, ip, port, metric=link_cost, hops=1)
        try:
            self.send_announcement((ip, port))
        except Exception:
            pass
        try:
            self.request_advertisement()
        except Exception:
            pass

    def _on_peer_lost(self, peer_id: str):
        for dest, mapping in list(self.routing_table._routes.items()):
            if peer_id in mapping:
                self.routing_table.remove_route(dest, peer_id)
                log.info(f"route invalidated (next_hop={peer_id} lost) dest={dest}", extra={"node_id": self.node_id, "dest": dest, "next_hop": peer_id})
        try:
            self.request_advertisement()
        except Exception:
            pass

    def _on_transport_message(self, msg, addr):
        src = msg.get('source')
        mtype = msg.get('type')
        try:
            validate_envelope(msg)
        except Exception:
            return

        if mtype not in ('peer_announcement', 'route_advertisement'):
            return

        if not src or src == self.node_id:
            return

        # Resolve the reachable endpoint (ip, port) of next_hop (src)
        src_ip, src_port = None, None
        if self.peer_manager:
            peers_map = self.peer_manager.get_peers()
            if src in peers_map:
                src_ip, src_port = peers_map[src]
        if src_ip is None and self.routing_table:
            direct_route = self.routing_table.get_route(src)
            if direct_route:
                src_ip, src_port = direct_route.ip, direct_route.port
        if src_ip is None and addr and isinstance(addr, (list, tuple)) and len(addr) >= 2:
            src_ip, src_port = addr[0], addr[1]
        elif src_ip is None:
            src_ip, src_port = src, 0

        link_cost = self.get_link_cost(src)

        payload = msg.get('payload', {})
        if mtype == 'peer_announcement':
            peers = payload.get('peers', {})
            any_changed = False
            for pid, addr_tuple in peers.items():
                if pid == self.node_id:
                    continue
                try:
                    ip, port = addr_tuple
                except (TypeError, ValueError):
                    continue
                if pid == src:
                    changed = self.routing_table.add_route(pid, pid, ip, port, metric=link_cost, hops=1)
                    if changed:
                        any_changed = True
                else:
                    existing = self.routing_table.get_next_hop(pid)
                    if existing is None:
                        try:
                            changed = self.routing_table.add_route(pid, src, src_ip, src_port, metric=link_cost + BASE_COST, hops=2)
                            if changed:
                                any_changed = True
                            log.info(f"route learned via peer_announcement: {pid} via {src}", extra={"node_id": self.node_id, "dest": pid, "next_hop": src})
                        except Exception as e:
                            log.error(f"failed to add peer_announcement route {pid}: {e}", extra={"node_id": self.node_id})
            if any_changed:
                try:
                    self.request_advertisement()
                except Exception:
                    pass
        elif mtype == 'route_advertisement':
            routes = payload.get('routes', {})
            hops_map = payload.get('hops', {})
            log.debug(f"route_advertisement from {src}: {routes}", extra={"node_id": self.node_id, "src": src})
            any_changed = False
            for dest, advertised_metric in routes.items():
                if dest == self.node_id:
                    continue

                adv_metric_int = int(advertised_metric)
                # Backward compatibility: scale small legacy hop count metrics
                if 0 < adv_metric_int < 50:
                    adv_cost = adv_metric_int * BASE_COST
                    adv_hops = adv_metric_int
                else:
                    adv_cost = adv_metric_int
                    adv_hops = int(hops_map.get(dest, max(1, adv_cost // BASE_COST)))

                new_hops = adv_hops + 1
                if new_hops >= MAX_ROUTE_HOPS:
                    continue

                new_cost = adv_cost + link_cost

                try:
                    changed = self.routing_table.add_route(dest, src, src_ip, src_port, metric=new_cost, hops=new_hops)
                    if changed:
                        any_changed = True
                    log.info(f"multi-hop route installed: {dest} via {src} ({src_ip}:{src_port}) cost={new_cost} hops={new_hops}", extra={"node_id": self.node_id, "dest": dest, "next_hop": src, "cost": new_cost, "hops": new_hops})
                except Exception as e:
                    log.error(f"failed to add route {dest} via {src}: {e}", extra={"node_id": self.node_id})

            if any_changed:
                try:
                    self.request_advertisement()
                except Exception:
                    pass
