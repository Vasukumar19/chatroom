# MeshChat (DisasterConnect) - Baseline Architecture

This document captures the current baseline architecture before evolving DisasterConnect
into MeshChat. It is intentionally brief and factual to serve as a starting point.

## Current State (baseline)
- Python-based CLI and Flask HTTP API
- P2P components in `p2p/` using UDP broadcast for discovery and TCP for messaging
- `p2p/host.py` — basic TCP peer host and broadcast helper
- `p2p/discovery.py` — UDP broadcast discovery service
- `p2p/chatroom.py` — in-memory chatroom and message handling

## High-level components
- CLI interface: `cli_interface.py`
- HTTP API: `main.py` (Flask)
- Networking: `p2p/` (host, discovery, chatroom)

## Known limitations
- No formal protocol model or schema (message formats are ad-hoc dicts)
- No transport abstraction
- No tests currently exist
- No persistent storage
- No git repository found at time of inspection

## Next steps (see roadmap)
- Establish formal message envelope
- Add transport abstraction and tests
- Introduce peer manager and routing
