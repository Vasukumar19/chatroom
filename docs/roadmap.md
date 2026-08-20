# DisasterConnect Roadmap & Milestone Status

---

## Completed & Verified Milestones

1. **Protocol & Security Foundation:**
   - Versioned protocol envelopes with TTL, immutable routing headers, and duplicate suppression.
   - AES-256-GCM payload encryption with Ed25519 digital signatures (`p2p/security.py`).
2. **Reliability & Persistence Layer:**
   - ACK correlation with bounded retransmissions and deduplication (`ReliableSender`, `ReliableReceiver`).
   - SQLite-backed store-and-forward queue with automatic replay on route recovery (`StoreForwardManager`).
3. **Priority 0 — Real-TCP Multi-Hop Forwarding & Route Learning:**
   - Real-TCP packet forwarding across intermediate hops (`Router`).
   - Distance-vector route advertisements (`RouteLearner`) with correct TCP listening-port endpoint resolution.
4. **Priority 1 — Continuous Integration:**
   - Cross-platform CI configuration (`.github/workflows/ci.yml`) for Linux and Windows on Python 3.12.
5. **Priority 2 — Observability & Logging:**
   - Structured JSON logging (`p2p/log.py`) with configurable severity filtering via `DC_LOG_LEVEL`.
6. **Priority 3 — QoS Priority Queuing & Congestion Control:**
   - Multi-tier priority scheduling (`PriorityTransport`) with emergency/SOS packet prioritization and low-priority drop under saturation.
7. **Priority 4 — Network Partition Healing & Anti-Entropy Synchronization:**
   - Bidirectional anti-entropy reconciliation (`SyncManager`) exchanging missing envelopes (`sync_request` / `sync_response`) upon partition recovery.
8. **Priority 5 — Dynamic Weighted Routing Based on Real Link Quality:**
   - RTT Exponential Moving Average ($\alpha=0.2$) penalty.
   - 20-sample sliding-window retry-rate penalty.
   - Queue pressure congestion penalty.
   - Anti-flapping route hysteresis threshold ($\Delta > \max(25, 0.15 \times C_{\text{curr}})$).
   - Hop count and TTL separation from dynamic metric cost.

---

## Optional Future Enhancements (Post-Project Scope)

The following items are potential research directions and extensions, not requirements for the current core project:

- **Split Horizon with Poison Reverse:** Preventing transient two-node routing loops in arbitrary cyclic topologies.
- **Multipath Load Balancing:** Splitting non-emergency traffic across equal-cost multi-hop paths.
- **Native Radio Drivers:** Implementing native OS Bluetooth (RFCOMM/BLE) and Wi-Fi Direct (P2P) hardware drivers.
- **Throughput & Bandwidth Probing:** Active bandwidth measurement for high-throughput stream prioritization.
