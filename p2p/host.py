"""P2P Host for peer-to-peer communication"""

import socket
import json
import threading
import uuid
from typing import Callable, Dict, Tuple, List


class P2PHost:
    """P2P Host for peer-to-peer communication"""
    
    def __init__(self, port: int):
        self.port = port
        self.peer_id = str(uuid.uuid4())[:8]
        self.peers: Dict[str, Tuple[str, int]] = {}
        self.message_handlers: List[Callable] = []
        self.running = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.peer_lock = threading.Lock()
    
    def start(self) -> str:
        """Start the P2P host"""
        self.socket.bind(('0.0.0.0', self.port))
        self.socket.listen(5)
        self.running = True
        
        print(f"✓ P2P Host started (ID: {self.peer_id})")
        
        listen_thread = threading.Thread(
            target=self._listen_for_connections,
            daemon=True
        )
        listen_thread.start()
        
        return self.peer_id
    
    def _listen_for_connections(self):
        """Listen for incoming peer connections"""
        while self.running:
            try:
                client_socket, address = self.socket.accept()
                client_socket.settimeout(10.0)
                
                thread = threading.Thread(
                    target=self._handle_peer_connection,
                    args=(client_socket, address),
                    daemon=True
                )
                thread.start()
                
            except socket.timeout:
                continue
            except Exception:
                if self.running:
                    continue
    
    def _handle_peer_connection(self, client_socket: socket.socket, address: Tuple[str, int]):
        """Handle incoming message from peer"""
        try:
            data = client_socket.recv(4096).decode('utf-8')
            if data:
                message = json.loads(data)
                
                for handler in self.message_handlers:
                    try:
                        handler(message)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            client_socket.close()
    
    def connect_to_peer(self, peer_ip: str, peer_port: int, peer_id: str) -> bool:
        """Connect to a discovered peer"""
        try:
            with self.peer_lock:
                self.peers[peer_id] = (peer_ip, peer_port)
            
            handshake = {'type': 'handshake', 'peer_id': self.peer_id}
            self._send_to_peer(peer_id, handshake)
            
            print(f"✓ Connected to peer {peer_id}")
            return True
        except Exception:
            return False
    
    def _send_to_peer(self, peer_id: str, message: dict):
        """Send message to single peer"""
        try:
            ip, port = self.peers[peer_id]
            peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            peer_socket.settimeout(3.0)
            peer_socket.connect((ip, port))
            peer_socket.send(json.dumps(message).encode('utf-8'))
            peer_socket.close()
        except Exception:
            with self.peer_lock:
                self.peers.pop(peer_id, None)
    
    def broadcast_message(self, message: dict) -> int:
        """Broadcast message to all connected peers"""
        message['peer_id'] = self.peer_id
        message_json = json.dumps(message)
        successful_sends = 0
        
        with self.peer_lock:
            peers_copy = list(self.peers.items())
        
        for peer_id, (ip, port) in peers_copy:
            try:
                peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                peer_socket.settimeout(3.0)
                peer_socket.connect((ip, port))
                peer_socket.send(message_json.encode('utf-8'))
                peer_socket.close()
                successful_sends += 1
            except Exception:
                pass
        
        return successful_sends
    
    def add_message_handler(self, handler: Callable):
        """Add a message handler callback"""
        self.message_handlers.append(handler)
    
    def get_peer_count(self) -> int:
        """Get number of connected peers"""
        with self.peer_lock:
            return len(self.peers)
    
    def get_peers(self) -> Dict[str, Tuple[str, int]]:
        """Get copy of connected peers dictionary"""
        with self.peer_lock:
            return self.peers.copy()
    
    def stop(self):
        """Stop the P2P host"""
        self.running = False
        try:
            self.socket.close()
        except:
            pass


def create_host(port: int) -> P2PHost:
    """Create and start a P2P host"""
    host = P2PHost(port)
    host.start()
    return host