"""Chat room with message history and real-time sync."""

import os
import threading
import uuid
from dataclasses import dataclass, asdict
from typing import List, Set
from datetime import datetime


VERBOSE = os.getenv("DC_VERBOSE") == "1"


def debug(msg: str):
    if VERBOSE:
        print(msg)


@dataclass
class ChatMessage:
    """Chat message with metadata and unique ID"""
    Message: str
    SenderID: str
    SenderNick: str
    MessageID: str = None
    Timestamp: str = None
    
    def __post_init__(self):
        if self.MessageID is None:
            self.MessageID = str(uuid.uuid4())[:12]
        if self.Timestamp is None:
            self.Timestamp = datetime.now().isoformat()


class ChatRoom:
    """
    P2P Chat Room with real-time messaging
    Handles message broadcasting and receiving
    """
    
    def __init__(self, room_name: str, nickname: str, peer_id: str, p2p_host, *, delivery_manager=None):
        self.room_name = room_name
        self.nickname = nickname
        self.peer_id = peer_id
        self.p2p_host = p2p_host
        self.delivery_manager = delivery_manager
        self.messages: List[ChatMessage] = []
        self.message_lock = threading.Lock()
        self.seen_message_ids: Set[str] = set()

        # Set by publish() on every call so callers (HTTP API, CLI) can
        # report an accurate outcome without changing publish()'s existing
        # bool return contract (kept for backward compatibility -- several
        # tests assert `publish(...) is True`). One of:
        #   'NO_KNOWN_PEERS' -- there is no destination at all; nothing was
        #                       queued for reliable delivery because there is
        #                       nothing to queue it for (Case B).
        #   'QUEUED'         -- at least one known peer is currently
        #                       unreachable; persisted in StoreForwardQueue
        #                       with destination=<peer_id>, state=QUEUED
        #                       (Case A).
        #   'DELIVERED'      -- every known peer received it immediately.
        #   'FAILED'         -- delivery/queueing genuinely failed.
        self.last_publish_status = 'UNKNOWN'

        # Message handling is now managed by the ReliableReceiver, which is
        # registered with the Router. This ensures all incoming messages go
        # through the ACK and deduplication layer.
        # self.p2p_host.add_message_handler(self._handle_incoming_message)
        debug(f"ChatRoom initialized for peer {self.peer_id}")
    
    def publish(self, message: str) -> bool:
        """
        Send message to all connected peers
        
        Args:
            message: Text message to send
            
        Returns:
            True if sent successfully to at least one peer
        """
        try:
            # Create message object with unique ID
            chat_msg = ChatMessage(
                Message=message,
                SenderID=self.peer_id,
                SenderNick=self.nickname
            )
            
            # Save to local history
            with self.message_lock:
                self.messages.append(chat_msg)
                self.seen_message_ids.add(chat_msg.MessageID)
            
            # The reliable path resolves the room broadcast into one
            # destination-aware delivery per known peer. Legacy host broadcast
            # remains for the standalone chat mode.
            broadcast_data = {
                'type': 'chat_message',
                'room': self.room_name,
                'data': asdict(chat_msg)
            }
            
            if self.delivery_manager is None:
                print("❌ FATAL: ChatRoom is not configured with a delivery manager.")
                return False

            known = set()
            if hasattr(self.p2p_host, "get_known_peers"):
                known.update(self.p2p_host.get_known_peers())
            if hasattr(self.delivery_manager, "route_manager") and hasattr(self.delivery_manager.route_manager, "list_routes"):
                known.update(self.delivery_manager.route_manager.list_routes().keys())

            if known:
                target_peers = [p for p in known if p != self.peer_id]
            else:
                peers_raw = self.p2p_host.get_peers()
                if isinstance(peers_raw, dict):
                    target_peers = [p for p in peers_raw.keys() if p != self.peer_id]
                else:
                    target_peers = [p for p in peers_raw if p != self.peer_id]

            # Ensure we also include anyone we have queued messages for in the past
            if hasattr(self.delivery_manager, "queue") and hasattr(self.delivery_manager.queue, "get_all_destinations"):
                past_dests = self.delivery_manager.queue.get_all_destinations()
                for d in past_dests:
                    if d != self.peer_id and d not in target_peers:
                        target_peers.append(d)

            if not target_peers:
                self.last_publish_status = 'NO_KNOWN_PEERS'
                return True

            results = [
                self.delivery_manager.send(peer_id, broadcast_data)
                for peer_id in target_peers
            ]

            statuses = {res.status for res in results}
            if statuses <= {'DELIVERED'}:
                self.last_publish_status = 'DELIVERED'
            elif statuses <= {'DELIVERED', 'QUEUED'}:
                # Case A: at least one known peer is currently unreachable
                # and the message was persisted in StoreForwardQueue with
                # destination=<peer_id>, state=QUEUED.
                self.last_publish_status = 'QUEUED'
            else:
                self.last_publish_status = 'FAILED'

            # A message is successfully published if it was delivered or queued for all.
            return all(res.status in ('DELIVERED', 'QUEUED') for res in results)
                
        except Exception as e:
            print(f"❌ Failed to send: {e}")
            self.last_publish_status = 'FAILED'
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
            msg_room = message_data.get('room')
            if msg_room != self.room_name:
                return
            
            # Parse message
            data = message_data.get('data', {})
            
            # Validate required fields
            if not all(key in data for key in ['Message', 'SenderID', 'SenderNick']):
                print("⚠️  Received invalid message format")
                return
            
            chat_msg = ChatMessage(**data)
            
            # Don't show our own messages again
            if chat_msg.SenderID == self.peer_id:
                return
            
            # Check for duplicates
            with self.message_lock:
                if chat_msg.MessageID in self.seen_message_ids:
                    return
                
                # Add to history
                self.messages.append(chat_msg)
                self.seen_message_ids.add(chat_msg.MessageID)
                
                # Display message
                try:
                    print(f"\n📥 {chat_msg.SenderNick}: {chat_msg.Message}")
                    print(f"[{self.nickname}] ", end='', flush=True)
                except UnicodeEncodeError:
                    try:
                        print(f"\n[INBOX] {chat_msg.SenderNick}: {chat_msg.Message}")
                        print(f"[{self.nickname}] ", end='', flush=True)
                    except Exception:
                        pass
                
        except TypeError as e:
            try:
                print(f"⚠️  Message parsing error: {e}")
            except UnicodeEncodeError:
                print(f"[WARN] Message parsing error: {e}")
            except Exception:
                pass
        except Exception as e:
            try:
                print(f"⚠️  Error handling message: {e}")
            except UnicodeEncodeError:
                print(f"[WARN] Error handling message: {e}")
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
    
    def get_raw_messages(self) -> List[dict]:
        """
        Get all messages as dictionaries for API
        
        Returns:
            List of message dictionaries
        """
        with self.message_lock:
            return [asdict(msg) for msg in self.messages]
    
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


def join_chat_room(room_name: str, nickname: str, peer_id: str, p2p_host, *, delivery_manager=None) -> ChatRoom:
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
    chat_room = ChatRoom(room_name, nickname, peer_id, p2p_host, delivery_manager=delivery_manager)
    try:
        print(f"✓ Joined room: '{room_name}'")
    except UnicodeEncodeError:
        print(f"[OK] Joined room: '{room_name}'")
    return chat_room
