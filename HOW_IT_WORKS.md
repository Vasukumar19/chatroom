# DisasterConnect / DisasterNet – How it works

This document explains the project end‑to‑end so you can run, debug, and extend it. It covers backend, P2P layer, HTTP API, CLI, and the React frontend.

## What it is
- Local‑network peer‑to‑peer (P2P) chat. No internet required; devices must be on the same LAN/Wi‑Fi.
- Each device runs the same Python backend. Peers auto‑discover each other via UDP broadcast, then exchange messages over TCP.
- A minimal React frontend calls the backend HTTP API to view/send messages.

## High‑level architecture
1) **Backend entry** (`main.py`): asks for room + nickname, finds free ports, starts P2P host, discovery, chat room, CLI, then Flask API.  
2) **P2P stack** (`p2p/`):
   - `host.py`: TCP server + peer list; handles handshakes and message broadcast/delivery.
   - `discovery.py`: UDP broadcast/listen for peers (room‑scoped).
   - `chatroom.py`: Message history, deduplication, and in/out processing.
3) **CLI** (`cli_interface.py`): Terminal chat input/output (single line per send/receive).
4) **HTTP API** (Flask in `main.py`): Exposes messages, peers, status for the frontend.
5) **Frontend** (`frontend/src/App.js`): Polls `/messages` and POSTs `/send` to show/send chat.

## Ports and IDs
- **P2P TCP port**: auto‑picked (default search starts at 5000). Carries chat messages + handshakes.
- **UDP discovery port**: 37020 (with fallback range +9). Broadcasts presence to find peers.
- **HTTP port**: next free port after P2P (e.g., 5001).
- **Peer ID**: short UUID per device (generated in `host.py`).

## Detailed flow (per device)
1) Start `python main.py`.  
2) Prompt: enter `room_name` and `nickname`. Peers must share the same `room_name` to see each other.  
3) Ports are auto‑selected.  
4) `initialize_p2p`:
   - `create_host` → starts TCP listener and registers message handlers.
   - `init_mdns` (UDP broadcast/listen) → starts announcing and listening for peers in the same room.
   - `join_chat_room` → wires message handling and history.
   - `start_terminal_interface` → begins CLI input loop.
5) Flask starts on the HTTP port (0.0.0.0) for web/REST access.

## Peer discovery and connection
- Every 5s, each node broadcasts a JSON `peer_announcement` via UDP to 255.255.255.255 on port 37020 (and the fallback port it bound).
- On receiving an announcement with the same `rendezvous` (room), `on_peer_found` calls `connect_to_peer`.
- `connect_to_peer` opens a TCP connection and sends a **handshake**: `{"type":"handshake","peer_id":<id>,"peer_port":<p2p_port>}`.
- The receiver stores the sender’s IP/port and can now deliver/broadcast messages to it. Handshakes make peer lists symmetric.

## Message send/receive path
- CLI or HTTP `/send` calls `chat_room.publish(message)`.
- `chatroom.py` creates a `ChatMessage` (unique `MessageID`, timestamp), stores it locally, and calls `p2p_host.broadcast_message`.
- `host.py` sends the serialized message to every connected peer over TCP.
- Receiving side’s `host.py` hands off to the registered handler in `chatroom.py`, which:
  - Drops non‑chat or wrong‑room messages.
  - Deduplicates via `MessageID`.
  - Prints a single line to the CLI: `[SenderNick]: message`.
- The sender’s CLI prints exactly one line for its own send: `[nickname] message` (or “saved locally” if no peers).

## HTTP API (Flask)
- `GET /messages` → list of formatted strings.
- `GET /messages/raw` → full metadata objects.
- `POST /send` → `{ "message": "text" }` sends a chat message.
- `GET /peers` → current peer list + self_id.
- `GET /health` → basic status.
- `GET /room-info` / `GET /status` → room, nickname, peer counts, totals.

## Frontend (React)
- Polls `http://localhost:5001/messages` every 2s and renders bubbles.
- Sends with `POST http://localhost:5001/send` (JSON body `{ message }`).
- Files: `frontend/src/App.js`, `index.js`, styles in `App.css`/`index.css`.
- To run: `cd frontend && npm install && npm start` (uses port 3000; ensure backend is running and CORS is enabled in Flask).

## Controls, logging, and tuning
- Quiet by default. Enable verbose logs by setting env var `DC_VERBOSE=1` before running.
- Room isolation: only peers with the same room name will handshake and exchange messages.
- If UDP port 37020 is busy, discovery falls back to the next available up to +9 and will log the chosen port.

## Running on multiple laptops (same LAN)
1) On each laptop: pull the same code revision.  
2) Run `python main.py`, enter the same room name, choose nicknames.  
3) Allow Windows/macOS firewall prompts for the chosen P2P port and for Python inbound.  
4) Optionally start the React frontend pointing to the backend’s HTTP port (default 5001 on the same machine).  
5) Send messages; each should appear once on all peers.

## Troubleshooting quick checks
- No peers: verify same room name, same LAN, firewall allows inbound on the P2P port, and UDP broadcast is not blocked by the router/AP.  
- Messages one‑way: ensure all peers are on the updated build (handshake includes `peer_port`).  
- Port conflicts: restart or set base ports manually (e.g., run backend with a free starting port by editing `find_free_port` defaults or exporting a custom env if you add one).  
- Verbose diagnostics: `DC_VERBOSE=1 python main.py` shows discovery and send/broadcast details.

## Key files map
- `main.py` – entrypoint; wiring + Flask endpoints.
- `p2p/host.py` – TCP host, peers, handshakes, broadcast/send.
- `p2p/discovery.py` – UDP announce/listen, room filtering.
- `p2p/chatroom.py` – message model, history, dedupe, handler.
- `cli_interface.py` – terminal chat loop.
- `frontend/src/App.js` – React UI polling `/messages` and POSTing `/send`.

This should give you a complete mental model to run and extend the app. For deeper diagnostics, toggle `DC_VERBOSE`. For UI changes, adjust the React polling interval or switch to websockets if you later add them to the backend.

---

# Deeper, code-focused walkthrough (what runs and why)

Below is a concise, line-referenced narrative of how each core file behaves at runtime. It’s not every single line, but covers the important control flow and how parts interact.

## `main.py` (entrypoint, Flask API)
- Top: imports Flask + CORS, P2P modules (`host`, `discovery`, `chatroom`), CLI interface.
- Globals: `p2p_host`, `chat_room`, `peer_discovery`, `terminal_interface`.
- `find_free_port`: iterates from a start port to find an available TCP port.
- `get_user_input`: prompts for room name + nickname (ensures non-empty).
- Flask routes:
  - `/messages` → returns `chat_room.get_messages()` (string list) or `[]`.
  - `/messages/raw` → returns `chat_room.get_raw_messages()` (dict list).
  - `/send` (POST) → validates JSON, non-empty message, max length, requires `chat_room`; calls `chat_room.publish(message)`; returns success or saved-locally note.
  - `/peers` → reports `self_id`, peer list (id + address), peer_count.
  - `/health` → status, peer_id/room/message_count/connected_peers.
  - `/room-info` / `/status` → room/nickname/peer_id/peer_count/total_messages.
- Error handlers: 404/500 JSON responses.
- `on_peer_discovered`: callback invoked by discovery; calls `p2p_host.connect_to_peer`.
- `initialize_p2p`:
  1) `create_host` → start TCP server and accept thread.
  2) `init_mdns` → start UDP broadcast + listen threads for discovery.
  3) `join_chat_room` → create ChatRoom, register message handler.
  4) `start_terminal_interface` → start CLI input thread.
- `run_flask`: starts Flask with host 0.0.0.0, chosen HTTP port, threaded.
- `__main__` block: prompts user, finds free P2P/HTTP ports, runs initialize, then blocks in `run_flask`. KeyboardInterrupt triggers clean shutdown of CLI, discovery, host.

## `p2p/host.py` (TCP host, peers, handshakes, broadcast)
- `P2PHost.__init__`: assigns port, generates `peer_id` (short UUID), sets up peer maps and TCP listening socket with SO_REUSEADDR.
- `start`: binds to `0.0.0.0:port`, starts listening, launches `_listen_for_connections` thread; returns `peer_id`.
- `_listen_for_connections`: accepts inbound TCP, sets timeout, spins `_handle_peer_connection` per client (thread).
- `_handle_peer_connection`:
  - Reads JSON message.
  - Resets failure count for sender if known.
  - If `type == handshake`: extracts `peer_id` and `peer_port`, stores `(addr, port)` in peer list (adds missing peers), enabling symmetric links.
  - Calls every registered message handler with the parsed message.
- `connect_to_peer(peer_ip, peer_port, peer_id)`:
  - Adds peer to list if absent; sets failure counter.
  - Sends handshake (`type: handshake, peer_id: self.peer_id, peer_port: self.port`) so the remote can add us.
- `_send_to_peer`: TCP connect + send JSON; retries tracked; removes peer after 3 consecutive failures.
- `broadcast_message`: sends a JSON payload (with `peer_id` injected) to all peers; tracks failures similarly.
- `add_message_handler`: stores callbacks (ChatRoom registers here).
- `get_peers` / `get_peer_count`: thread-safe snapshots.
- `stop`: closes listening socket.

## `p2p/discovery.py` (UDP broadcast + listen)
- Uses UDP broadcast port 37020 (with fallback up to +9).
- `start(rendezvous)`:
  - Records room name (`rendezvous`), marks running.
  - Binds a UDP socket to the first available port in the range.
  - Starts `_broadcast_presence` thread (daemon).
  - Starts `_listen_for_peers` thread if binding succeeded.
- `_broadcast_presence`: every 5s sends JSON `{type: "peer_announcement", peer_id, p2p_port, rendezvous}` to 255.255.255.255 on both the default and actual bound port.
- `_listen_for_peers`:
  - Receives UDP packets with timeout.
  - Parses JSON; drops own announcements; drops other rooms.
  - Extracts `peer_id`, `peer_port`, `peer_ip = addr[0]`.
  - Deduplicates via `discovered_peers` set.
  - On a new peer, calls `on_peer_found(peer_id, peer_ip, peer_port)` (provided by `main.py`), which triggers a TCP handshake via `host.connect_to_peer`.
- `stop`: closes sockets.

## `p2p/chatroom.py` (message model, history, dedupe, handler)
- `ChatMessage` dataclass: fields Message, SenderID, SenderNick, auto MessageID (uuid[:12]), Timestamp (ISO).
- `ChatRoom.__init__`: stores room/nickname/peer_id, message list + locks + `seen_message_ids`; registers `_handle_incoming_message` as a handler with `p2p_host`.
- `publish(message: str)`:
  - Builds `ChatMessage`, appends to local history, tracks `MessageID`.
  - Broadcasts via `p2p_host.broadcast_message` with envelope `{type: "chat_message", room, data: <chat dict>}`.
  - Returns True if at least one peer send succeeded; False if none (but message still stored locally).
- `_handle_incoming_message`:
  - Only handles `type == chat_message` and matching `room`.
  - Validates required fields; builds ChatMessage.
  - Drops self-sent messages (SenderID == self.peer_id).
  - Dedupes via `seen_message_ids`.
  - On accept: append to history, then print once to CLI: `📥 SenderNick: Message` and re-print prompt prefix.
- Accessors: `get_messages` (formatted strings), `get_raw_messages` (dicts), `get_message_count`, `get_peer_count` (from host), `get_room_info`.

## `cli_interface.py` (terminal chat loop)
- Starts a daemon thread reading user input.
- Commands: type message → sends; `quit/exit/q` → stop; Ctrl+C → stop.
- On send, prints exactly one line `[nickname] message` (or notes saved locally if no peers).
- Incoming messages are printed by ChatRoom handler, not here.

## Flask HTTP API (declared in `main.py`)
- `/messages` / `/messages/raw` / `/send` / `/peers` / `/health` / `/room-info` / `/status`.
- All CORS-enabled so the React app on port 3000 can call them.

## Frontend `frontend/src/App.js`
- React component:
  - `useEffect`: every 2s `GET /messages` and stores array in state.
  - `sendMessage`: `POST /send` with `{ message }`, clears input.
  - Renders message bubbles, an input box, and a send button; logs errors to console.
- `index.js`: React bootstrap; `reportWebVitals` unused by logic.

## Typical end-to-end runtime (annotated)
1) User runs `python main.py` → prompts → chooses room/nickname.  
2) Ports picked: P2P (e.g., 5000), HTTP (e.g., 5001).  
3) `create_host` → TCP listener + accept loop.  
4) `init_mdns` → UDP broadcast/listen threads; announce every 5s.  
5) `join_chat_room` → handler registered; history initialized.  
6) `start_terminal_interface` → input loop; user can type messages.  
7) Flask starts → frontend can poll `/messages` and POST `/send`.  
8) When another peer starts with the same room:  
   - It hears the UDP announcement, calls `connect_to_peer`, sends handshake.  
   - Receiver stores peer, sends its own handshake.  
   - Both now have each other in their peer maps.  
9) Sending a message (CLI or `/send`):  
   - ChatRoom stores locally, broadcasts via host to all peers.  
   - Each peer’s ChatRoom handler dedupes and prints once.  
10) Shutdown (Ctrl+C): stops CLI, discovery (sockets closed), host socket closed; process exits.

## Why each piece matters
- UDP discovery: removes manual IP entry; room filter isolates groups.  
- Handshake with `peer_port`: makes peer lists symmetric, avoiding one-way delivery.  
- MessageID dedupe: prevents loops and duplicate prints.  
- Threaded design: discovery, TCP accept, CLI input, and Flask API run concurrently.  
- HTTP API: enables a simple web UI without touching the P2P code.  
- `DC_VERBOSE=1`: lets you turn on deep logs only when diagnosing issues.

## Minimal changes you can make safely
- Adjust polling interval in `frontend/src/App.js` if you want faster updates.  
- Change room name prompt default in `main.py` to a preset for demos.  
- Add a `POST /join` to programmatically set room/nickname (currently via prompt).  
- Add a TTL cleanup for `seen_message_ids` if you expect extremely long sessions.
