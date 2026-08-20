"""P2P Host for peer-to-peer communication - DEBUG VERSION"""

import os
import socket
import json
import threading
import uuid
from typing import Callable, Dict, Optional, Tuple, List, Set

from p2p.identity import load_or_create_identity


VERBOSE = os.getenv("DC_VERBOSE") == "1"


def debug(msg: str):
    if VERBOSE:
        print(msg)


class P2PHost:
    """P2P Host for peer-to-peer communication with improved reliability"""
    
    def __init__(self, port: int, *, identity_dir: Optional[str] = None):
        self.port = port
        # Load persistent identity if a storage directory is provided;
        # fall back to a transient uuid for tests that don't need persistence.
        if identity_dir is not None:
            self.peer_id = load_or_create_identity(identity_dir)
        else:
            self.peer_id = str(uuid.uuid4())[:8]
        self.peers: Dict[str, Tuple[str, int]] = {}
        self.known_peers: Set[str] = set()
        self.peer_failures: Dict[str, int] = {}
        self.message_handlers: List[Callable] = []
        self.transport_handlers: List[Callable] = []
        self.running = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.peer_lock = threading.Lock()
        self.transport = None
    
    def start(self) -> str:
        """Start the P2P host"""
        self.socket.bind(('0.0.0.0', self.port))
        self.socket.listen(5)
        self.socket.settimeout(1.0)
        self.running = True
        
        try:
            print(f"✓ P2P Host started (ID: {self.peer_id})")
        except UnicodeEncodeError:
            print(f"[OK] P2P Host started (ID: {self.peer_id})")
        
        listen_thread = threading.Thread(
            target=self._listen_for_connections,
            daemon=True
        )
        listen_thread.start()

        # If a transport was set, register to receive transport messages
        try:
            if self.transport:
                self.transport.register_handler(self._handle_transport_message)
        except Exception:
            pass
        
        return self.peer_id
        
    def _listen_for_connections(self):
        """Listen for incoming peer connections"""
        while self.running:
            try:
                client_socket, address = self.socket.accept()
                client_socket.settimeout(10.0)
                
                debug(f"Incoming connection from {address}")
                
                thread = threading.Thread(
                    target=self._handle_peer_connection,
                    args=(client_socket, address),
                    daemon=True
                )
                thread.start()
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"🔧 DEBUG: Accept error: {e}")
    
    def _handle_peer_connection(self, client_socket: socket.socket, address: Tuple[str, int]):
        """Handle incoming message from peer"""
        try:
            data = client_socket.recv(4096).decode('utf-8')
            debug(f"Received {len(data)} bytes from {address}")
            
            if data:
                message = json.loads(data)
                debug(f"Parsed message type: {message.get('type')}")
                
                # Reset failure count for this peer if message received
                peer_id = message.get('peer_id')
                if peer_id and peer_id in self.peer_failures:
                    self.peer_failures[peer_id] = 0
                
                # Handle handshake so both peers add each other
                if message.get('type') == 'handshake':
                    peer_port = message.get('peer_port')
                    if peer_id and peer_port:
                        with self.peer_lock:
                            if peer_id != self.peer_id:
                                self.known_peers.add(peer_id)
                            if peer_id not in self.peers:
                                self.peers[peer_id] = (address[0], int(peer_port))
                                self.peer_failures[peer_id] = 0
                                print(f"Handshake added peer {peer_id} at {address[0]}:{peer_port}")
                    else:
                        print(f"⚠️  Handshake missing peer info: id={peer_id}, port={peer_port}")
                
                debug(f"Calling {len(self.message_handlers)} message handlers")
                for handler in self.message_handlers:
                    try:
                        handler(message)
                    except Exception as e:
                        print(f"⚠️  Message handler error: {e}")
                        import traceback
                        traceback.print_exc()
                for handler in list(self.transport_handlers):
                    try:
                        handler(message, address)
                    except Exception as e:
                        debug(f"Transport handler error: {e}")
        except json.JSONDecodeError as e:
            print(f"⚠️  Invalid JSON received: {e}")
        except Exception as e:
            print(f"🔧 DEBUG: Connection handling error: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
    
    def connect_to_peer(self, peer_ip: str, peer_port: int, peer_id: str) -> bool:
        """Connect to a discovered peer"""
        try:
            with self.peer_lock:
                if peer_id and peer_id != self.peer_id:
                    self.known_peers.add(peer_id)
                # Don't re-add if already connected
                if peer_id in self.peers:
                    debug(f"Peer {peer_id} already in peer list")
                    return True
                
                self.peers[peer_id] = (peer_ip, peer_port)
                self.peer_failures[peer_id] = 0
                debug(f"Added peer {peer_id} at {peer_ip}:{peer_port}")
            
            # Send handshake (include our listening port so the remote can add us)
            handshake = {
                'type': 'handshake',
                'peer_id': self.peer_id,
                'peer_port': self.port
            }
            debug(f"Sending handshake to {peer_id}")
            # Use transport if available; otherwise fallback to TCP _send_to_peer
            if self.transport:
                try:
                    self.transport.send((peer_ip, peer_port), handshake)
                except Exception as e:
                    debug(f"Transport send to {peer_id} failed: {e}")
            else:
                self._send_to_peer(peer_id, handshake)
            
            try:
                print(f"✓ Connected to peer {peer_id}")
            except UnicodeEncodeError:
                print(f"[OK] Connected to peer {peer_id}")
            return True
        except Exception as e:
            try:
                print(f"⚠️  Failed to connect to peer {peer_id}: {e}")
            except UnicodeEncodeError:
                print(f"[!] Failed to connect to peer {peer_id}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _send_to_peer(self, peer_id: str, message: dict):
        """Send message to single peer with retry logic"""
        try:
            with self.peer_lock:
                if peer_id not in self.peers:
                    debug(f"Peer {peer_id} not in peer list for _send_to_peer")
                    return
                ip, port = self.peers[peer_id]
            
            debug(f"Attempting to send to {peer_id} at {ip}:{port}")
            
            # If transport available and supports send, use it
            if self.transport:
                try:
                    self.transport.send((ip, port), message)
                except Exception as e:
                    raise
            else:
                peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                peer_socket.settimeout(3.0)
                peer_socket.connect((ip, port))
                
                message_json = json.dumps(message)
                peer_socket.send(message_json.encode('utf-8'))
                peer_socket.close()
            
            debug(f"Successfully sent to {peer_id}")
            
            # Reset failure count on success
            if peer_id in self.peer_failures:
                self.peer_failures[peer_id] = 0
                
        except Exception as e:
            print(f"⚠️  Send error to {peer_id}: {e}")
            # Improved peer removal with retry logic
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
                if self.transport:
                    self.transport.send((ip, port), message)
                else:
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
                print(f"⚠️  Broadcast failed to {peer_id}: {e}")
                # Track failures but don't remove immediately
                with self.peer_lock:
                    self.peer_failures[peer_id] = self.peer_failures.get(peer_id, 0) + 1
                    
                    if self.peer_failures[peer_id] >= 3:
                        self.peers.pop(peer_id, None)
                        self.peer_failures.pop(peer_id, None)
                        print(f"⚠️  Peer {peer_id} removed after broadcast failures")
        return successful_sends

    def send_to_address(self, address: Tuple[str, int], message: dict) -> None:
        """Send one JSON message to an explicit TCP next-hop address."""
        peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            peer_socket.settimeout(3.0)
            peer_socket.connect(address)
            peer_socket.send(json.dumps(message).encode('utf-8'))
        finally:
            try:
                peer_socket.close()
            except Exception:
                pass
    
    def add_message_handler(self, handler: Callable):
        """Add a message handler callback"""
        self.message_handlers.append(handler)
        print(f"🔧 DEBUG: Message handler added. Total handlers: {len(self.message_handlers)}")

    def add_transport_handler(self, handler: Callable) -> None:
        if handler not in self.transport_handlers:
            self.transport_handlers.append(handler)

    def set_transport(self, transport):
        """Set a transport (e.g., UDPTransport) to use for sending/receiving."""
        self.transport = transport

    def _handle_transport_message(self, message: dict, addr: tuple):
        """Handle incoming messages from transport layer."""
        try:
            # Expect message is already a dict (JSON decoded by transport)
            # Validate envelope if possible
            try:
                from p2p import protocol
                protocol.validate_envelope(message)
            except Exception:
                # malformed envelope — ignore or notify
                print(f"⚠️  Malformed envelope from {addr}")
                return

            # Update peer failure/reset if peer_id present
            peer_id = message.get('peer_id')
            if peer_id and peer_id != self.peer_id:
                with self.peer_lock:
                    self.known_peers.add(peer_id)
            if peer_id and peer_id in self.peer_failures:
                self.peer_failures[peer_id] = 0

            for handler in self.message_handlers:
                try:
                    handler(message)
                except Exception as e:
                    print(f"⚠️  Message handler error: {e}")
        except Exception as e:
            print(f"🔧 DEBUG: transport message handling error: {e}")
    
    def get_peer_count(self) -> int:
        """Get number of connected peers"""
        with self.peer_lock:
            return len(self.peers)
    
    def get_peers(self) -> Dict[str, Tuple[str, int]]:
        """Get copy of connected peers dictionary"""
        with self.peer_lock:
            return self.peers.copy()

    def get_known_peers(self) -> Set[str]:
        """Get copy of all known peer IDs (historical/session-level)"""
        with self.peer_lock:
            return self.known_peers.copy()
    
    def stop(self):
        """Stop the P2P host"""
        self.running = False
        try:
            self.socket.close()
        except:
            pass


def create_host(port: int, *, identity_dir: Optional[str] = None) -> P2PHost:
    """Create and start a P2P host.

    Parameters
    ----------
    port:
        TCP port to bind to.
    identity_dir:
        Directory used to persist the node identity (``peer_identity.json``).
        When provided the same peer_id is reused across restarts.  When
        omitted a fresh transient id is generated (useful for tests).
    """
    host = P2PHost(port, identity_dir=identity_dir)
    host.start()
    return host