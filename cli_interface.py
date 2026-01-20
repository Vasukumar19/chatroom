"""Terminal interface for real-time P2P messaging"""

import threading
from typing import Optional


class TerminalInterface:
    """Clean terminal interface for chatting"""
    
    def __init__(self, chat_room, nickname: str):
        self.chat_room = chat_room
        self.nickname = nickname
        self.running = True
    
    def start(self):
        """Start terminal input in background thread"""
        terminal_thread = threading.Thread(
            target=self._input_loop,
            daemon=True
        )
        terminal_thread.start()
    
    def _input_loop(self):
        """Main chat input loop"""
        print("\n" + "═"*70)
        print("💬 CHAT ACTIVE - Start typing your messages!")
        print("═"*70)
        print("Commands:")
        print("  • Type your message and press Enter to send")
        print("  • Type 'quit' or 'exit' to leave chat")
        print("  • Press Ctrl+C to shutdown")
        print("═"*70 + "\n")
        
        while self.running:
            try:
                # Get user input
                message = input(f"[{self.nickname}] ").strip()
                
                # Check for exit commands
                if message.lower() in ['quit', 'exit', 'q']:
                    print("\n👋 Leaving chat...\n")
                    self.running = False
                    break
                
                # Skip empty messages
                if not message:
                    continue
                
                # Send message
                try:
                    success = self.chat_room.publish(message)
                    
                    if not success:
                        print("⚠️  No peers connected - message saved locally")
                        
                except Exception as e:
                    print(f"❌ Send error: {e}")
                
            except KeyboardInterrupt:
                print("\n\n🛑 Shutting down...")
                self.running = False
                break
                
            except EOFError:
                # Handle input stream closing
                self.running = False
                break
                
            except Exception as e:
                print(f"⚠️  Input error: {e}")
                continue
    
    def stop(self):
        """Stop the terminal interface"""
        self.running = False


def start_terminal_interface(chat_room, nickname: str) -> Optional[TerminalInterface]:
    """
    Initialize and start terminal interface
    
    Args:
        chat_room: ChatRoom instance
        nickname: User's display name
        
    Returns:
        TerminalInterface instance
    """
    terminal = TerminalInterface(chat_room, nickname)
    terminal.start()
    return terminal