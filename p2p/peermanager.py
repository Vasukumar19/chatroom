"""PeerManager: maintain peer state independently of transport."""
import threading
import time
from typing import Callable, Dict, Optional


class PeerInfo:
    def __init__(self, peer_id: str, ip: str, port: int):
        self.peer_id = peer_id
        self.ip = ip
        self.port = int(port)
        self.last_seen = time.time()
        self.status = 'ONLINE'


class PeerManager:
    def __init__(self, node_id: str, peer_timeout: int = 30):
        self.node_id = node_id
        self.peers: Dict[str, PeerInfo] = {}
        self.peer_lock = threading.Lock()
        self.peer_timeout = peer_timeout
        self.on_peer_discovered: Optional[Callable[[str, str, int], None]] = None
        self.on_peer_updated: Optional[Callable[[str], None]] = None
        self.on_peer_lost: Optional[Callable[[str], None]] = None
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            now = time.time()
            to_remove = []
            with self.peer_lock:
                for pid, info in list(self.peers.items()):
                    if now - info.last_seen > self.peer_timeout:
                        info.status = 'OFFLINE'
                        to_remove.append(pid)
            for pid in to_remove:
                with self.peer_lock:
                    self.peers.pop(pid, None)
                if self.on_peer_lost:
                    try:
                        self.on_peer_lost(pid)
                    except Exception:
                        pass
            time.sleep(1)

    def update_peer(self, peer_id: str, ip: str, port: int):
        if peer_id == self.node_id:
            return
        with self.peer_lock:
            if peer_id in self.peers:
                info = self.peers[peer_id]
                info.ip = ip
                info.port = int(port)
                info.last_seen = time.time()
                info.status = 'ONLINE'
                updated = True
            else:
                self.peers[peer_id] = PeerInfo(peer_id, ip, port)
                updated = False

        if updated and self.on_peer_updated:
            try:
                self.on_peer_updated(peer_id)
            except Exception:
                pass
        if not updated and self.on_peer_discovered:
            try:
                self.on_peer_discovered(peer_id, ip, port)
            except Exception:
                pass

    def get_peers(self):
        with self.peer_lock:
            return {pid: (p.ip, p.port) for pid, p in self.peers.items()}
