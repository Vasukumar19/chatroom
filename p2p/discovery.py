"""Peer discovery using UDP broadcast for local network - CORRECTED VERSION"""

import socket
import json
import threading
import time
from typing import Callable, Set


class PeerDiscovery:
    """Peer discovery using UDP broadcast with improved error handling"""
    
    BROADCAST_PORT = 37020
    DISCOVERY_INTERVAL = 5
    
    def __init__(self, peer_id: str, p2p_port: int, on_peer_found: Callable):
        self.peer_id = peer_id
        self.p2p_port = p2p_port
        self.on_peer_found = on_peer_found
        self.running = False
        self.discovered_peers: Set[str] = set()
        
        self.broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.rendezvous = ""
        self.actual_port = self.BROADCAST_PORT  # Track actual bound port
    
    def start(self, rendezvous: str):
        """Start peer discovery with fallback port binding"""
        self.rendezvous = rendezvous
        self.running = True
        
        # FIX: Improved port binding with fallback
        port_bound = False
        for attempt_port in range(self.BROADCAST_PORT, self.BROADCAST_PORT + 10):
            try:
                self.listen_socket.bind(('', attempt_port))
                self.actual_port = attempt_port
                port_bound = True
                
                if attempt_port != self.BROADCAST_PORT:
                    print(f"⚠️  Port {self.BROADCAST_PORT} in use, using port {attempt_port}")
                break
                
            except OSError:
                continue
        
        if not port_bound:
            print(f"❌ Could not bind discovery port (tried {self.BROADCAST_PORT}-{self.BROADCAST_PORT + 9})")
            print(f"⚠️  Peer discovery will be limited - you can still connect manually")
        
        broadcast_thread = threading.Thread(
            target=self._broadcast_presence,
            daemon=True
        )
        broadcast_thread.start()
        
        if port_bound:
            listen_thread = threading.Thread(
                target=self._listen_for_peers,
                daemon=True
            )
            listen_thread.start()
        
        print(f"✓ Peer discovery started")
    
    def _broadcast_presence(self):
        """Periodically broadcast presence"""
        while self.running:
            try:
                announcement = {
                    'type': 'peer_announcement',
                    'peer_id': self.peer_id,
                    'p2p_port': self.p2p_port,
                    'rendezvous': self.rendezvous
                }
                
                message = json.dumps(announcement).encode('utf-8')
                
                # Broadcast to standard port and actual port if different
                ports_to_try = {self.BROADCAST_PORT, self.actual_port}
                
                for port in ports_to_try:
                    try:
                        self.broadcast_socket.sendto(
                            message,
                            ('255.255.255.255', port)
                        )
                    except Exception:
                        pass  # Broadcast failures are expected
                        
            except Exception as e:
                if self.running:
                    print(f"⚠️  Broadcast error: {e}")
            
            time.sleep(self.DISCOVERY_INTERVAL)
    
    def _listen_for_peers(self):
        """Listen for peer announcements"""
        self.listen_socket.settimeout(1.0)
        
        while self.running:
            try:
                data, addr = self.listen_socket.recvfrom(1024)
                
                try:
                    message = json.loads(data.decode('utf-8'))
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON
                
                # Skip our own announcements
                if message.get('peer_id') == self.peer_id:
                    continue
                
                # Filter by room (rendezvous point)
                if message.get('rendezvous') != self.rendezvous:
                    continue
                
                peer_id = message.get('peer_id')
                peer_port = message.get('p2p_port')
                peer_ip = addr[0]
                
                # Validate data
                if not peer_id or not peer_port:
                    continue
                
                # Only trigger callback for new peers
                if peer_id and peer_id not in self.discovered_peers:
                    self.discovered_peers.add(peer_id)
                    print(f"✓ Discovered peer: {peer_id}")
                    
                    if self.on_peer_found:
                        try:
                            self.on_peer_found(peer_id, peer_ip, peer_port)
                        except Exception as e:
                            print(f"⚠️  Error in peer discovery callback: {e}")
                            
            except socket.timeout:
                continue  # Normal timeout
            except Exception as e:
                if self.running:
                    # Only log unexpected errors while running
                    pass
    
    def get_discovered_peers(self) -> Set[str]:
        """Get set of discovered peer IDs"""
        return self.discovered_peers.copy()
    
    def stop(self):
        """Stop peer discovery"""
        self.running = False
        try:
            self.broadcast_socket.close()
        except:
            pass
        try:
            self.listen_socket.close()
        except:
            pass


def init_mdns(peer_id: str, p2p_port: int, rendezvous: str, 
              on_peer_found: Callable) -> PeerDiscovery:
    """Initialize peer discovery service"""
    discovery = PeerDiscovery(peer_id, p2p_port, on_peer_found)
    discovery.start(rendezvous)
    return discovery
