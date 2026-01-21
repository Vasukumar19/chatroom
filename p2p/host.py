"""P2P Host for peer-to-peer communication - CORRECTED VERSION"""

import socket
import json
import threading
import uuid
from typing import Callable, Dict, Tuple, List


class P2PHost:
    """P2P Host for peer-to-peer communication with improved reliability"""
    
    def __init__(self, port: int):
        self.port = port
        self.peer_id = str(uuid.uuid4())[:8]
        self.peers: Dict[str, Tuple[str, int]] = {}
        self.peer_failures: Dict[str, int] = {}  # Track connection failures
        self.message_handlers: List[Callable] = []
        self.running = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.peer_lock = threading.Lock()
    
    def start(self) -> str:
        """Start the P2P host"""
        self.socket.bind(('0.0.0.0', self.port))
        self.socket.listen(5)
        self.socket.settimeout(1.0)  # FIX: Add timeout for clean shutdown
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
                # Normal timeout, check if still running
                continue
            except Exception as e:
                if self.running:
                    # Only log if we're supposed to be running
                    pass
    
    def _handle_peer_connection(self, client_socket: socket.socket, address: Tuple[str, int]):
        """Handle incoming message from peer"""
        try:
            data = client_socket.recv(4096).decode('utf-8')
            if data:
                message = json.loads(data)
                
                # Reset failure count for this peer if message received
                peer_id = message.get('peer_id')
                if peer_id and peer_id in self.peer_failures:
                    self.peer_failures[peer_id] = 0
                
                for handler in self.message_handlers:
                    try:
                        handler(message)
                    except Exception as e:
                        print(f"⚠️  Message handler error: {e}")
        except json.JSONDecodeError as e:
            print(f"⚠️  Invalid JSON received: {e}")
        except Exception as e:
            pass  # Connection errors are expected
        finally:
            try:
                client_socket.close()
            except:
                pass
    
    def connect_to_peer(self, peer_ip: str, peer_port: int, peer_id: str) -> bool:
        """Connect to a discovered peer"""
        try:
            with self.peer_lock:
                # Don't re-add if already connected
                if peer_id in self.peers:
                    return True
                
                self.peers[peer_id] = (peer_ip, peer_port)
                self.peer_failures[peer_id] = 0  # Initialize failure counter
            
            # Send handshake
            handshake = {'type': 'handshake', 'peer_id': self.peer_id}
            self._send_to_peer(peer_id, handshake)
            
            print(f"✓ Connected to peer {peer_id}")
            return True
        except Exception as e:
            print(f"⚠️  Failed to connect to peer {peer_id}: {e}")
            return False
    
    def _send_to_peer(self, peer_id: str, message: dict):
        """Send message to single peer with retry logic"""
        try:
            with self.peer_lock:
                if peer_id not in self.peers:
                    return
                ip, port = self.peers[peer_id]
            
            peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            peer_socket.settimeout(3.0)
            peer_socket.connect((ip, port))
            peer_socket.send(json.dumps(message).encode('utf-8'))
            peer_socket.close()
            
            # Reset failure count on success
            if peer_id in self.peer_failures:
                self.peer_failures[peer_id] = 0
                
        except Exception as e:
            # FIX: Improved peer removal with retry logic
            with self.peer_lock:
                self.peer_failures[peer_id] = self.peer_failures.get(peer_id, 0) + 1
                
                # Only remove after 3 consecutive failures
                if self.peer_failures[peer_id] >= 3:
                    self.peers.pop(peer_id, None)
                    self.peer_failures.pop(peer_id, None)
                    print(f"⚠️  Peer {peer_id} removed after 3 failed attempts")
    
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
                
                # Reset failure count on success
                if peer_id in self.peer_failures:
                    self.peer_failures[peer_id] = 0
                    
            except Exception as e:
                # Track failures but don't remove immediately
                with self.peer_lock:
                    self.peer_failures[peer_id] = self.peer_failures.get(peer_id, 0) + 1
                    
                    if self.peer_failures[peer_id] >= 3:
                        self.peers.pop(peer_id, None)
                        self.peer_failures.pop(peer_id, None)
                        print(f"⚠️  Peer {peer_id} removed after broadcast failures")
        
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
