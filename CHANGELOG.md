# Changelog

All notable changes to the DisasterConnect project are documented in this file.

## Completed Milestones

- **Priority 5 (Dynamic Weighted Routing):** Dynamic integer metric routing with RTT EMA ($\alpha=0.2$) penalty, 20-sample sliding-window retry-rate penalty, queue pressure penalty, anti-flapping hysteresis ($\Delta > \max(25, 0.15 \times C_{\text{curr}})$), and hop count/TTL separation.
- **Priority 4 (Partition Synchronization):** Anti-entropy delta exchange protocol (`sync_request` / `sync_response`) reconciling missing message history across healed network partitions with deduplication and local delivery.
- **Priority 3 (QoS & Congestion Control):** Multi-tier priority queues (`PriorityTransport`), prioritizing emergency/SOS packets and dropping low-priority traffic under queue pressure.
- **Priority 2 (Structured Observability):** Machine-readable JSON logging subsystem (`p2p/log.py`) with configurable `DC_LOG_LEVEL`.
- **Priority 1 (Continuous Integration):** Cross-platform GitHub Actions CI workflow for Linux and Windows (`.github/workflows/ci.yml`).
- **Priority 0 (Real-TCP Multi-Hop Routing):** Real-TCP packet forwarding engine (`Router`), distance-vector route advertisements (`RouteLearner`), and listening port next-hop resolution.
- **Phases 1–10 (Foundation):** Transport abstraction, SQLite persistent store-and-forward queue, ACK correlation and bounded retries, AES-256-GCM payload encryption, and Ed25519 digital signatures.
- **Phase 0:** Initial P2P local chat baseline.
