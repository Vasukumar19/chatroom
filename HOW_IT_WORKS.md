# DisasterConnect — How It Works (End-to-End System Walkthrough)

This document provides a comprehensive technical walkthrough of the DisasterConnect runtime architecture, component lifecycle, multi-hop packet routing, reliability mechanics, anti-entropy partition reconciliation, and operational configuration.

---

## 1. High-Level Architecture Overview

DisasterConnect operates as a resilient, decentralized peer-to-peer network capable of multi-hop routing across real TCP socket connections without central coordination or pre-existing infrastructure.

```text
               +----------------------------------------------------+
               |                Application Layer                   |
               |       (ChatRoom / CLI Interface / Flask API)       |
               +----------------------------------------------------+
                                         │
                                         ▼
               +----------------------------------------------------+
               |               StoreForwardManager                  |
               |          (SQLite Persistent Queue & Replay)        |
               +----------------------------------------------------+
                                         │
                                         ▼
               +----------------------------------------------------+
               |                 ReliableSender                     |
               |   (ACK Correlation, Retries, RTT EMA, Retry Rate)   |
               +----------------------------------------------------+
                                         │
                                         ▼
               +----------------------------------------------------+
               |                PriorityTransport                   |
               |    (QoS Priority Queues, Congestion Eviction)      |
               +----------------------------------------------------+
                                         │
                                         ▼
               +----------------------------------------------------+
               |                     Router                         |
               |  (Packet Forwarding, Loop Protection, TTL Decrement)|
               +----------------------------------------------------+
                     │                    │                    │
                     ▼                    ▼                    ▼
             +---------------+    +---------------+    +---------------+
             | RoutingTable  |    | RouteLearner  |    |  SyncManager  |
             | (Hysteresis,  |    | (Distance-    |    | (Anti-Entropy |
             | Cost Metrics) |    | Vector Ads)   |    | Reconciliation|
             +---------------+    +---------------+    +---------------+
                                         │
                                         ▼
               +----------------------------------------------------+
               |              HostTransport (TCP)                   |
               |        + UDP Broadcast Peer Discovery              |
               +----------------------------------------------------+
```

---

## 2. Component Initialization & Runtime Startup (`main.py`)

When `python main.py` is executed, the runtime performs the following startup sequence in `initialize_p2p()`:

1. **Port Allocation & Socket Reservation:**
   `find_free_port()` scans for an available TCP port and maintains an OS reservation socket to prevent port-stealing race conditions before the real listener binds.
2. **P2P Host & Persistent Identity (`p2p/host.py`):**
   Loads or generates a persistent cryptographic peer identity (`peer_identity.json`) and binds a TCP server listening socket on `0.0.0.0:<p2p_port>`.
3. **Transport Layer (`p2p/transport.py`):**
   Creates `HostTransport` wrapping the active TCP host.
4. **QoS & Congestion Control (`p2p/qos.py`):**
   Wraps `HostTransport` in a `PriorityTransport` queue worker thread with bounded capacity (`max_queue_size=100`) and high-priority packet scheduling.
5. **Routing Engine & Data Model (`p2p/routing.py`, `p2p/router.py`):**
   Instantiates `RoutingTable` and starts `Router` with loop detection and TTL validation.
6. **Reliable Sender (`p2p/reliability.py`):**
   Instantiates `ReliableSender` wired to `RoutingTable` next-hop address resolution. Tracks per-peer RTT Exponential Moving Averages ($\alpha=0.2$) and 20-sample sliding-window retry rates.
7. **Store-and-Forward Persistence (`p2p/store_forward.py`, `p2p/store_forward_manager.py`):**
   Initializes SQLite database `disasterconnect-<peer_id>.sqlite` and wires `StoreForwardManager` to receive route recovery notifications from `RoutingTable`.
8. **Dynamic Route Learner & Peer Manager (`p2p/routemanager.py`, `p2p/peermanager.py`):**
   Starts `PeerManager` (direct neighbor tracking) and `RouteLearner` (distance-vector route advertisement engine). `RouteLearner` listens for advertisements and computes dynamic link costs using RTT, retries, and queue pressure.
9. **Partition Synchronization Manager (`p2p/sync.py`):**
   Instantiates `SyncManager` wired to route recovery callbacks to trigger bidirectional anti-entropy reconciliation when disconnected segments rejoin.
10. **Application Chat Room (`p2p/chatroom.py`):**
    Initializes `ChatRoom` with message deduplication (`seen_message_ids`), wiring `ReliableReceiver` to route incoming messages to the application layer and automatically dispatch ACK envelopes.
11. **User Interfaces:**
    Starts the terminal CLI chat loop and launches the Flask REST API on the reserved HTTP port.

---

## 3. Outgoing Message Lifecycle

When a user submits a message via CLI or `POST /send`:

```text
ChatRoom.publish("Hello")
   │
   ▼
StoreForwardManager.enqueue_and_send()
   │  ├─ Stores payload in SQLite (Status: QUEUED)
   │  └─ Hands envelope to ReliableSender
   ▼
ReliableSender.send_reliable(envelope, destination)
   │  ├─ Timestamps send for RTT measurement
   │  ├─ Resolves destination to next_hop via RoutingTable
   │  └─ Enqueues envelope to PriorityTransport (Priority: HIGH/MEDIUM/LOW)
   ▼
PriorityTransport worker thread
   │  ├─ Dequeues highest priority packet
   │  └─ Forwards to Router / HostTransport
   ▼
HostTransport.send(next_hop_socket, envelope)
   │
   ▼ (TCP Socket Transmission)
```

- **ACK Reception:** When the destination responds with an ACK envelope, `ReliableSender` records the sample RTT, updates its EMA, and notifies `StoreForwardManager` to mark the message `DELIVERED`.
- **Offline Destination:** If no route exists, `StoreForwardManager` keeps the message in SQLite (`QUEUED`). When `RoutingTable` reports route availability, it automatically dequeues and transmits the message.

---

## 4. Multi-Hop Forwarding Example ($A \rightarrow B \rightarrow C \rightarrow D$)

Consider a linear mesh topology where node $A$ wishes to communicate with node $D$:

```text
Node A (19100) ──TCP── Node B (19101) ──TCP── Node C (19102) ──TCP── Node D (19103)
```

1. **Route Learning:**
   - Node $D$ advertises itself to $C$ with $\text{hops}=1, \text{cost}=100$.
   - Node $C$ computes its link cost to $D$ ($100 + \text{penalties}$) and advertises $D$ to $B$ with $\text{hops}=2, \text{cost}=200$.
   - Node $B$ advertises $D$ to $A$ with $\text{hops}=3, \text{cost}=300$.
   - Node $A$ installs a route: $\text{destination}=D, \text{next\_hop}=B, \text{hops}=3, \text{metric}=300$.
2. **Forwarding Packet from $A$ to $D$:**
   - Node $A$ sets $\text{TTL}=16, \text{hop\_count}=0, \text{destination}=D$ and sends to $B$.
   - Node $B$'s `Router` inspects the destination. Since $D \neq B$, $B$ decrements $\text{TTL} \rightarrow 15$, increments $\text{hop\_count} \rightarrow 1$, queries its `RoutingTable` for $D$ ($\text{next\_hop}=C$), and transmits to $C$.
   - Node $C$'s `Router` decrements $\text{TTL} \rightarrow 14$, increments $\text{hop\_count} \rightarrow 2$, queries `RoutingTable` ($\text{next\_hop}=D$), and transmits to $D$.
   - Node $D$ recognizes itself as the destination, delivers the payload to `ChatRoom`, and sends an ACK along the reverse route.

---

## 5. Dynamic Weighted Routing & Anti-Flapping Hysteresis

### Link Cost Calculation
For any neighbor $N$, the link cost is computed dynamically:
$$\text{link\_cost}(N) = 100 + \text{int}\left(\frac{\text{RTT}_{\text{EMA}}}{10}\right) + \text{int}(150 \times \text{retry\_rate}) + \text{int}(100 \times \text{queue\_pressure})$$

### Anti-Flapping Hysteresis
To prevent route oscillation caused by network jitter:
- If a route via $N_{\text{curr}}$ is currently active with cost $C_{\text{curr}}$, an alternate next-hop $N_{\text{cand}}$ with cost $C_{\text{cand}}$ is only selected if:
  $$C_{\text{cand}} < C_{\text{curr}} - \max(25, \text{int}(0.15 \times C_{\text{curr}}))$$
- If the current route fails or is marked `DEAD`, the router switches immediately to the best available candidate without hysteresis penalty.

---

## 6. Partition Healing & Anti-Entropy Synchronization

When network partitions heal:

```text
Partitioned:   [ Node A ── Node B ]      ||      [ Node C ── Node D ]
                      │                                 │
                 (Chat MSG-1)                      (Chat MSG-2)
                      │                                 │
Healed:        [ Node A ── Node B ══════════════ Node C ── Node D ]
```

1. **Reconnection Trigger:** `RoutingTable` detects route recovery between $B$ and $C$ and triggers `SyncManager.trigger_sync()`.
2. **Sync Request:** Node $B$ sends a `sync_request` envelope to $C$ containing the list of message IDs stored in its local `StoreForwardQueue`.
3. **Delta Calculation:** Node $C$ compares $B$'s message IDs against its own local store and identifies missing envelopes.
4. **Sync Response:** Node $C$ transmits a `sync_response` containing the missing envelopes to $B$.
5. **Ingestion & Application Delivery:** Node $B$ validates each envelope's TTL, checks for duplicates, stores the messages in its local archive, and delivers them to the application layer. Node $B$ similarly reciprocates missing envelopes back to $C$.

---

## 7. Logging & Observability

DisasterConnect uses structured JSON logging (`p2p/log.py`). Configure logging verbosity using the `DC_LOG_LEVEL` environment variable:

```powershell
# Set log level in PowerShell
$env:DC_LOG_LEVEL = "INFO"      # Options: DEBUG, INFO, WARNING, ERROR
python main.py
```

Sample structured log output:
```json
{"time": "2026-08-20T14:28:47.158Z", "level": "INFO", "module": "p2p.routemanager", "msg": "multi-hop route installed: cbeb73d0 via ecbc3c49 (127.0.0.1:19101) cost=201 hops=2"}
```
