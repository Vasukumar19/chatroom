💬 DisasterConnect - Local P2P Chat Application
The best local area network chat application - No internet required! Perfect for disaster scenarios, classrooms, or private networks.

✨ Features
🚀 Zero Configuration - Automatic port detection and peer discovery
💬 Real-time Chat - Instant messaging with multiple users
🔒 Local Network Only - No internet needed, completely private
📱 Multi-Device - Works across Windows, Mac, and Linux
⚡ Fast & Lightweight - Minimal resource usage
🎯 Simple Interface - Easy terminal-based chat
📋 Requirements
Python 3.7 or higher
Local network connection (WiFi or Ethernet)
🚀 Quick Start
1. Install Dependencies
bash
pip install flask flask-cors
2. Run the Application
bash
python main.py
3. Enter Your Details
The app will prompt you:

Enter chat room name: my-team
Enter your nickname: Alex
4. Start Chatting!
Just type your messages and press Enter. That's it!

📁 Project Structure
disaster-connect/
├── main.py                 # Main application entry point
├── cli_interface.py        # Terminal chat interface
├── p2p/
│   ├── host.py            # P2P networking core
│   ├── discovery.py       # Automatic peer discovery
│   └── chatroom.py        # Chat room management
└── README.md              # This file
🔧 How It Works
Automatic Discovery: Uses UDP broadcast to find peers on local network
P2P Connection: Direct TCP connections between peers
Message Sync: Real-time message broadcasting to all connected peers
No Central Server: Each peer is equal - fully decentralized
💡 Usage Tips
Starting a New Room
First person creates the room:

Enter chat room name: emergency-team
Enter your nickname: Leader
Joining Existing Room
Others join with the same room name:

Enter chat room name: emergency-team
Enter your nickname: Member1
Chatting
Type your message and press Enter
Messages appear instantly on all connected devices
See who's online with peer count
Type quit or press Ctrl+C to exit
🌐 HTTP API (Optional)
The app also provides a REST API for building web interfaces:

GET /messages - Get all messages
POST /send - Send a message
GET /peers - List connected peers
GET /health - System status
GET /room-info - Room information
Example: Build a web UI that connects to http://localhost:<http_port>

🐛 Troubleshooting
No Peers Found?
Check network: Make sure all devices are on the same WiFi/network
Check firewall: Temporarily disable firewall for testing
Use same room name: All users must use identical room names
Port Already in Use?
The app automatically finds free ports. If you still have issues:

Close other applications using network ports
Restart your computer
Messages Not Sending?
Wait a few seconds for peer discovery
Check if peers are connected (shown in startup info)
Ensure you're in the same room
🎓 For Students
This project demonstrates:

Networking: TCP/IP, UDP broadcasting, socket programming
Threading: Concurrent message handling
REST APIs: Flask web framework
P2P Architecture: Decentralized communication
Learning Points
Peer Discovery (p2p/discovery.py): How devices find each other
Message Broadcasting (p2p/host.py): Sending to multiple peers
Real-time Sync (p2p/chatroom.py): Message synchronization
User Interface (cli_interface.py): Terminal input handling
🔐 Security Note
This is designed for local networks only. Do not expose to the internet without:

Authentication
Encryption (TLS/SSL)
Rate limiting
Input validation
📝 Future Enhancements
Want to improve the project? Try adding:

Message History: Save messages to file
User Authentication: Password-protected rooms
Encryption: Secure message content
File Sharing: Share documents between peers
Web Interface: Modern browser-based UI
Emojis & Formatting: Rich text support
🤝 Contributing
This is a student project! Feel free to:

Report bugs
Suggest features
Submit improvements
Use for learning
📄 License
Free to use for educational purposes.

🆘 Support
Having issues? Check:

Python version: python --version (needs 3.7+)
Dependencies installed: pip list | grep flask
Network connectivity: Can you ping other devices?
Made for disaster scenarios and emergency communication - because connectivity shouldn't require the internet! 🚨

