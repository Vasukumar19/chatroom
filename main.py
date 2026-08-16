"""
DisasterConnect - Best P2P Local Chat Application
Production-ready with automatic configuration - CORRECTED VERSION
"""
import os
import sys
import socket
import time
from flask import Flask, jsonify, request
from flask_cors import CORS

from p2p.host import create_host
from p2p.discovery import init_mdns
from p2p.chatroom import join_chat_room
from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.router import Router
from p2p.routing import RoutingTable
from p2p.store_forward import StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.transport import HostTransport
from cli_interface import start_terminal_interface

# Flask setup with minimal logging
app = Flask(__name__)
CORS(app)

import logging
log = logging.getLogger('werkzeug')
log.disabled = True
app.logger.disabled = True

# Global instances
p2p_host = None
chat_room = None
peer_discovery = None
terminal_interface = None
transport = None
routing_table = None
router = None
reliable_sender = None
reliable_receiver = None
store_forward_manager = None
store_forward_queue = None


def find_free_port(start_port=5000, max_attempts=100):
    """Find an available port automatically"""
    for port in range(start_port, start_port + max_attempts):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('', port))
            sock.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports found in range {start_port}-{start_port + max_attempts}")


def get_user_input():
    """Get room name and nickname from user"""
    print("\n" + "="*70)
    print("💬 DisasterConnect - Local P2P Chat")
    print("="*70)
    print("\nConnect with nearby devices instantly - no internet needed!\n")
    
    # Get room name
    while True:
        room_name = input("📍 Enter chat room name (e.g., 'team-alpha'): ").strip()
        if room_name:
            break
        print("❌ Room name cannot be empty.\n")
    
    # Get nickname
    while True:
        nickname = input("👤 Enter your nickname (e.g., 'Alex'): ").strip()
        if nickname:
            break
        print("❌ Nickname cannot be empty.\n")
    
    return room_name, nickname


# ==================== HTTP API ENDPOINTS ====================

@app.route('/messages', methods=['GET'])
def get_messages():
    """Get all chat messages"""
    if chat_room:
        return jsonify(chat_room.get_messages())
    return jsonify([])


@app.route('/messages/raw', methods=['GET'])
def get_raw_messages():
    """Get all messages in raw format (with metadata)"""
    if chat_room:
        return jsonify(chat_room.get_raw_messages())
    return jsonify([])


@app.route('/send', methods=['POST'])
def send_message():
    """Send a chat message"""
    try:
        # FIX: Improved input validation
        data = request.get_json()
        
        if not data:
            return jsonify({
                "status": "error", 
                "message": "No data provided"
            }), 400
        
        if not isinstance(data, dict):
            return jsonify({
                "status": "error", 
                "message": "Invalid data format"
            }), 400
        
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({
                "status": "error", 
                "message": "Empty message"
            }), 400
        
        if len(message) > 1000:
            return jsonify({
                "status": "error", 
                "message": "Message too long (max 1000 characters)"
            }), 400
        
        if not chat_room:
            return jsonify({
                "status": "error", 
                "message": "Chat not ready"
            }), 503
        
        success = chat_room.publish(message)
        
        return jsonify({
            "status": "success",
            "message": "Message sent" if success else "Message saved (no peers connected)"
        }), 200
            
    except Exception as e:
        return jsonify({
            "status": "error", 
            "message": f"Internal error: {str(e)}"
        }), 500


@app.route('/peers', methods=['GET'])
def get_peers():
    """Get list of connected peers"""
    if p2p_host:
        peers_dict = p2p_host.get_peers()
        peers = [{"peer_id": pid, "address": f"{ip}:{port}"} 
                 for pid, (ip, port) in peers_dict.items()]
        return jsonify({
            "self_id": p2p_host.peer_id,
            "peers": peers,
            "peer_count": len(peers)
        })
    return jsonify({"self_id": "unknown", "peers": [], "peer_count": 0})


@app.route('/health', methods=['GET'])
def health_check():
    """System health check"""
    return jsonify({
        "status": "healthy",
        "peer_id": p2p_host.peer_id if p2p_host else "unknown",
        "room": chat_room.room_name if chat_room else "unknown",
        "message_count": chat_room.get_message_count() if chat_room else 0,
        "connected_peers": p2p_host.get_peer_count() if p2p_host else 0
    })


@app.route('/room-info', methods=['GET'])
def room_info():
    """Get current room information"""
    if chat_room:
        return jsonify(chat_room.get_room_info())
    return jsonify({"error": "Not connected"}), 503


@app.route('/status', methods=['GET'])
def get_status():
    """Get detailed system status"""
    if not chat_room or not p2p_host:
        return jsonify({"error": "System not initialized"}), 503
    
    return jsonify({
        "room_name": chat_room.room_name,
        "nickname": chat_room.nickname,
        "peer_id": p2p_host.peer_id,
        "connected_peers": p2p_host.get_peer_count(),
        "total_messages": chat_room.get_message_count(),
        "status": "active"
    })


# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


# ==================== P2P INITIALIZATION ====================

def on_peer_discovered(peer_id: str, peer_ip: str, peer_port: int):
    """Handle newly discovered peer"""
    if p2p_host:
        p2p_host.connect_to_peer(peer_ip, peer_port, peer_id)
    if routing_table and store_forward_manager:
        routing_table.add_route(peer_id, peer_id, peer_ip, peer_port)
        store_forward_manager.on_route_recovered(peer_id)


def initialize_p2p(p2p_port: int, room_name: str, nickname: str):
    """Initialize complete P2P chat system"""
    global p2p_host, chat_room, peer_discovery, terminal_interface
    global transport, routing_table, router, reliable_sender, reliable_receiver
    global store_forward_manager, store_forward_queue
    
    print("\n" + "─"*70)
    print("🚀 Starting DisasterConnect...")
    print("─"*70)
    
    # Step 1: Create P2P Host
    print("\n[1/4] 🔧 Initializing P2P network...")
    # Use the current working directory as the stable identity directory so
    # that peer_identity.json survives restarts on the same machine.
    identity_dir = os.getcwd()
    p2p_host = create_host(p2p_port, identity_dir=identity_dir)
    time.sleep(0.5)

    transport = HostTransport(p2p_host)
    routing_table = RoutingTable()
    router = Router(p2p_host.peer_id, transport, routing_table)
    router.start()

    def next_hop(destination):
        route = routing_table.get_route(destination)
        if not route:
            raise RuntimeError(f"No route to {destination}")
        return (route.ip, route.port)

    reliable_sender = ReliableSender(p2p_host.peer_id, transport, address_resolver=next_hop)
    store_forward_queue = StoreForwardQueue(f"disasterconnect-{p2p_host.peer_id}.sqlite")
    store_forward_manager = StoreForwardManager(
        queue=store_forward_queue,
        reliable_sender=reliable_sender,
        route_manager=routing_table,
    )
    
    # Step 2: Start Peer Discovery
    print("[2/4] 📡 Starting peer discovery...")
    peer_discovery = init_mdns(
        peer_id=p2p_host.peer_id,
        p2p_port=p2p_port,
        rendezvous=room_name,
        on_peer_found=on_peer_discovered
    )
    time.sleep(0.5)
    
    # Step 3: Join Chat Room
    print("[3/4] 💬 Joining chat room...")
    chat_room = join_chat_room(
        room_name, nickname, p2p_host.peer_id, p2p_host,
        delivery_manager=store_forward_manager,
    )

    def send_ack(ack, destination):
        route = routing_table.get_route(destination)
        if not route:
            raise RuntimeError(f"No route to {destination}")
        router._send_on_route(route, ack)

    reliable_receiver = ReliableReceiver(
        p2p_host.peer_id,
        transport,
        lambda envelope, address: chat_room._handle_incoming_message(envelope['payload']),
        auto_register=False,
        ack_sender=send_ack,
    )
    router.add_app_handler(reliable_receiver._on_transport_message)
    time.sleep(0.5)
    
    # Step 4: Start Terminal Interface
    print("[4/4] ⌨️  Starting terminal interface...")
    terminal_interface = start_terminal_interface(chat_room, nickname)
    time.sleep(0.5)
    
    # Success summary
    print("\n" + "═"*70)
    print("✅ SYSTEM READY!")
    print("═"*70)
    print(f"  📍 Room      : {room_name}")
    print(f"  👤 Nickname  : {nickname}")
    print(f"  🆔 Peer ID   : {p2p_host.peer_id}")
    print(f"  🔌 P2P Port  : {p2p_port}")
    print(f"  🌐 HTTP Port : (starting...)")
    print("═"*70)


def run_flask(http_port: int):
    """Start HTTP API server"""
    print(f"\n🌐 HTTP API Server: http://localhost:{http_port}")
    print(f"   └─ Connect your web interface here")
    print(f"\n💡 Tip: Other devices can join by running this app with the same room name")
    print(f"⚠️  Press Ctrl+C to stop\n")
    
    try:
        app.run(
            host='0.0.0.0',
            port=http_port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        print(f"\n❌ Flask server error: {e}")


# ==================== MAIN ENTRY POINT ====================

if __name__ == '__main__':
    try:
        # Welcome and get user input
        room_name, nickname = get_user_input()
        
        # Find available ports automatically
        print("\n🔍 Finding available ports...")
        p2p_port = find_free_port(5000)
        http_port = find_free_port(p2p_port + 1)
        
        print(f"✓ P2P Port: {p2p_port}")
        print(f"✓ HTTP Port: {http_port}")
        
        # Initialize P2P system
        initialize_p2p(p2p_port, room_name, nickname)
        
        # Start HTTP server (blocking call)
        run_flask(http_port)
        
    except KeyboardInterrupt:
        print("\n\n" + "="*70)
        print("🛑 Shutting down DisasterConnect...")
        print("="*70)
        
        if terminal_interface:
            terminal_interface.stop()
            print("✓ Terminal interface stopped")
        
        if peer_discovery:
            peer_discovery.stop()
            print("✓ Peer discovery stopped")
        
        if p2p_host:
            p2p_host.stop()
            print("✓ P2P host stopped")
        
        print("\n👋 Goodbye! Thanks for using DisasterConnect\n")
        sys.exit(0)
        
    except Exception as e:
        print(f"\n❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        print("\nPlease try restarting the application.\n")
        sys.exit(1)
