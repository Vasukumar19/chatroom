"""HeartbeatManager: sends heartbeats, processes ACKs, and detects peer failures."""
import threading
import time
from typing import Callable, Dict, Optional

from p2p.protocol import create_envelope, validate_envelope
from p2p.peermanager import PeerManager


UNKNOWN = 'UNKNOWN'
ALIVE = 'ALIVE'
SUSPECT = 'SUSPECT'
DEAD = 'DEAD'


class HeartbeatManager:
    def __init__(
        self,
        node_id: str,
        peer_manager: PeerManager,
        transport,
        interval: float = 1.0,
        suspect_threshold: int = 1,
        dead_threshold: int = 3,
    ):
        self.node_id = node_id
        self.peer_manager = peer_manager
        self.transport = transport
        self.interval = float(interval)
        self.suspect_threshold = int(suspect_threshold)
        self.dead_threshold = int(dead_threshold)

        # miss counters per peer
        self._miss_counts: Dict[str, int] = {}
        self._status: Dict[str, str] = {}

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # callback(peer_id, new_status)
        self.on_status_change: Optional[Callable[[str, str], None]] = None

    def start(self):
        if self._running:
            return
        # register transport handler
        self.transport.register_handler(self._on_transport_message)
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run_loop(self):
        while self._running:
            try:
                self.send_heartbeats()
                self._check_timeouts()
            except Exception:
                pass
            time.sleep(self.interval)

    def send_heartbeats(self):
        # send heartbeat to all known peers
        peers = self.peer_manager.get_peers()
        for pid, (ip, port) in peers.items():
            env = create_envelope('heartbeat', source=self.node_id, payload={})
            try:
                self.transport.send((ip, port), env)
            except Exception:
                # sending failure should not crash manager
                pass

    def send_heartbeat_once_to(self, address):
        env = create_envelope('heartbeat', source=self.node_id, payload={})
        self.transport.send(address, env)

    def _on_transport_message(self, msg, addr):
        # Validate envelope; ignore malformed
        try:
            validate_envelope(msg)
        except Exception:
            return

        mtype = msg.get('type')
        src = msg.get('source')
        if not src or src == self.node_id:
            return

        # Update peer last_seen via peer_manager
        try:
            self.peer_manager.update_peer(src, addr[0], addr[1])
        except Exception:
            pass

        # reset miss counter on any heartbeat-related message
        self._miss_counts[src] = 0
        self._set_status(src, ALIVE)

        if mtype == 'heartbeat':
            # reply with ack
            env = create_envelope('heartbeat_ack', source=self.node_id, payload={})
            try:
                self.transport.send((addr[0], addr[1]), env)
            except Exception:
                pass
        elif mtype == 'heartbeat_ack':
            # nothing else needed beyond update
            pass

    def _check_timeouts(self):
        # iterate known peers and bump miss counters
        peers = self.peer_manager.get_peers()
        for pid in list(peers.keys()):
            # increment miss count if not seen recently
            self._miss_counts.setdefault(pid, 0)
            self._miss_counts[pid] += 1
            misses = self._miss_counts[pid]
            if misses >= self.dead_threshold:
                self._set_status(pid, DEAD)
            elif misses >= self.suspect_threshold:
                self._set_status(pid, SUSPECT)

    def _set_status(self, peer_id: str, status: str):
        prev = self._status.get(peer_id, UNKNOWN)
        if prev == status:
            return
        self._status[peer_id] = status
        # reflect into PeerManager's PeerInfo.status if present
        try:
            with self.peer_manager.peer_lock:
                if peer_id in self.peer_manager.peers:
                    self.peer_manager.peers[peer_id].status = status
        except Exception:
            pass
        # emit callback
        if self.on_status_change:
            try:
                self.on_status_change(peer_id, status)
            except Exception:
                pass
