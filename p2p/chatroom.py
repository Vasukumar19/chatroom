"""Chat room with message history and real-time sync"""

import threading
from dataclasses import dataclass, asdict
from typing import List
from datetime import datetime


@dataclass
class ChatMessage:
    """Chat message with metadata"""
    Message: str
    SenderID: str
    SenderNick: str
    Timestamp: str = None
    
    def __post_init__(self):
        if self.Timestamp is None:
            self.Timestamp = datetime.now().isoformat()


class ChatRoom:
    """
    P2P Chat Room with real-time messaging
    Handles message broadcasting and receiving
    """
    
    def __init__(self, room_name: str, nickname: str, peer_id: str, p2p_host):
        self.room_name = room_name
        self.nickname = nickname
        self.peer_id = peer_id
        self.p2p_host = p2p_host
        self.messages: List[ChatMessage] = []
        self.message_lock = threading.Lock()
        
        # Register to receive messages
        self.p2p_host.add_message_handler(self._handle_incoming_message)
    
    def publish(self, message: str) -> bool:
        """
        Send message to all connected peers
        
        Args:
            message: Text message to send
            
        Returns:
            True if sent successfully
        """
        try:
            # Create message object
            chat_msg = ChatMessage(
                Message=message,
                SenderID=self.peer_id,
                SenderNick=self.nickname
            )
            
            # Save to local history
            with self.message_lock:
                self.messages.append(chat_msg)
            
            # Broadcast to peers
            broadcast_data = {
                'type': 'chat_message',
                'room': self.room_name,
                'data': asdict(chat_msg)
            }
            
            success_count = self.p2p_host.broadcast_message(broadcast_data)
            
            # Show send confirmation
            if success_count > 0:
                print(f"📤 You: {message}")
                return True
            else:
                # No peers yet, but message saved
                print(f"📝 You: {message}")
                return False
                
        except Exception as e:
            print(f"❌ Failed to send: {e}")
            return False
    
    def _handle_incoming_message(self, message_data: dict):
        """
        Handle incoming message from peer
        
        Args:
            message_data: Dictionary with message data
        """
        try:
            # Filter by message type
            if message_data.get('type') != 'chat_message':
                return
            
            # Filter by room
            if message_data.get('room') != self.room_name:
                return
            
            # Parse message
            data = message_data.get('data', {})
            chat_msg = ChatMessage(**data)
            
            # Don't show our own messages again
            if chat_msg.SenderID == self.peer_id:
                return
            
            # Add to history (with duplicate check)
            with self.message_lock:
                is_duplicate = any(
                    m.SenderID == chat_msg.SenderID and 
                    m.Message == chat_msg.Message and 
                    m.Timestamp == chat_msg.Timestamp 
                    for m in self.messages
                )
                
                if not is_duplicate:
                    self.messages.append(chat_msg)
                    print(f"\n📥 {chat_msg.SenderNick}: {chat_msg.Message}")
                    print(f"[{self.nickname}] ", end='', flush=True)
                    
        except Exception:
            pass
    
    def get_messages(self) -> List[str]:
        """
        Get all messages as formatted strings
        
        Returns:
            List of message strings with timestamps
        """
        with self.message_lock:
            return [
                f"[{msg.Timestamp}] {msg.SenderNick}: {msg.Message}"
                for msg in self.messages
            ]
    
    def get_message_count(self) -> int:
        """Get total message count"""
        with self.message_lock:
            return len(self.messages)
    
    def get_peer_count(self) -> int:
        """Get number of connected peers"""
        try:
            return self.p2p_host.get_peer_count()
        except Exception:
            return 0
    
    def get_room_info(self) -> dict:
        """
        Get complete room information
        
        Returns:
            Dictionary with room stats
        """
        with self.message_lock:
            return {
                'room_name': self.room_name,
                'nickname': self.nickname,
                'peer_id': self.peer_id,
                'message_count': len(self.messages),
                'peer_count': self.get_peer_count()
            }


def join_chat_room(room_name: str, nickname: str, peer_id: str, p2p_host) -> ChatRoom:
    """
    Join or create a chat room
    
    Args:
        room_name: Name of the room
        nickname: User's display name
        peer_id: Unique peer identifier
        p2p_host: P2P host instance
        
    Returns:
        ChatRoom instance
    """
    chat_room = ChatRoom(room_name, nickname, peer_id, p2p_host)
    print(f"✓ Joined room: '{room_name}'")
    return chat_room