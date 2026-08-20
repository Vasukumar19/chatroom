# Phase 8: Performance Baseline Report

> **Historical Document:** This document preserves the Phase 8 benchmark baseline recorded on in-memory mock transports. For current production architecture and test status, see [README.md](README.md) and [docs/architecture.md](docs/architecture.md).

**Date:** 2026-08-16
**Status:** ✅ Baseline Established
**Regression Tests:** 74/74 passing (at Phase 8 baseline)

---

## Overview

This is the official **Phase 8 performance baseline** for DisasterConnect. All benchmarks are measured on an in-memory mock network using deterministic test transports. These numbers represent the current implementation performance and should **not be interpreted as real-world network latency** (Bluetooth/Wi-Fi/Ethernet).

---

## Latency Benchmarks

### 1-Hop Reliable Delivery

| Percentile | Latency |
|-----------|---------|
| p50       | 0.020 ms |
| p95       | 0.027 ms |
| p99       | 0.116 ms |
| min       | 0.018 ms |
| max       | 0.118 ms |

**Note:** Single-hop delivery through the reliable sender/receiver with ACK correlation.

### 4-Hop Reliable Delivery

| Percentile | Latency |
|-----------|---------|
| p50       | 0.035 ms |
| p95       | 0.125 ms |
| p99       | 0.167 ms |
| min       | 0.031 ms |
| max       | 0.167 ms |

**Note:** Multi-hop delivery through a 4-node mesh (A → B → C → D) with routing and ACK propagation.

**Key Observation:** 4-hop latency is approximately **1.75× higher** than 1-hop (p50 0.035 ms vs 0.020 ms), despite involving 4 router traversals and ACK correlation. This reflects the in-memory simulation performance.

---

## Reliability Benchmarks

### ACK Loss Recovery

| Dropped ACKs | Retries | Delivery Rate |
|-------------|---------|--------------|
| 1           | 1.0     | 100.0%       |
| 2           | 2.0     | 100.0%       |
| 3           | 3.0     | 100.0%       |

**Finding:** The reliable transport correctly retries exactly N times for N dropped ACKs and achieves 100% delivery. No false positives or over-retrying observed.

---

## Persistence Benchmarks

### SQLite Enqueue Performance

| Percentile | Time |
|-----------|------|
| p50       | 1.412 ms |
| p95       | 2.009 ms |
| p99       | 2.463 ms |

**Note:** Single message enqueue to StoreForwardQueue (SQLite with index).

### Queue Replay Performance

| Percentile | Time |
|-----------|------|
| p50       | 0.025 ms |
| p95       | 0.045 ms |
| p99       | 0.102 ms |
| min       | 0.017 ms |
| max       | 0.136 ms |

**Finding:** Replay is **50–60× faster** than enqueue, indicating efficient batch recovery semantics.

---

## Recovery Benchmarks

### Route Recovery & Replay

| Percentile | Time |
|-----------|------|
| p50       | 2.493 ms |
| p95       | 3.094 ms |
| p99       | 3.387 ms |
| min       | 1.718 ms |
| max       | 3.480 ms |
| Success Rate | 100.0% |

**Note:** Full cycle: send → queue (route unavailable) → mark route recovered → replay. All messages successfully delivered.

---

## Regression Status

```
Total Tests: 74
Passed: 74 (100%)
Failed: 0
Errors: 0
```

**Categories:**
- Routing: ✅
- Reliability: ✅
- Store-and-Forward Queue: ✅
- Recovery & Replay: ✅
- Multi-Hop Simulation: ✅

---

## Implementation Notes

1. **Benchmark Environment:**
   - All measurements use in-memory MockTransport
   - MeshMockNetwork simulates multi-hop routing
   - No actual I/O, network calls, or threading delays beyond Python event loop

2. **Logging:**
   - Verbose output disabled for latency benchmarks to reduce noise
   - Test suite logging still fully enabled for debugging

3. **Message Identities:**
   - Message IDs preserved across queue/replay cycles
   - Deduplication validated by receiver
   - Recovery maintains original message identity

4. **Performance Characteristics:**
   - Enqueue (SQLite): ~1–2 ms p50
   - Replay (in-memory): ~0.025 ms p50
   - 4-hop latency: ~0.035 ms p50
   - Recovery cycle: ~2.5 ms p50

---

## Future Optimization Notes

- **Real-world latency:** When benchmarking actual transports (UDP/Bluetooth), expect 10–100× higher absolute latencies
- **Scaling:** Current queue performance assumes small message volumes; larger volumes may warrant batch optimizations
- **Deduplication:** Seen-message TTL cleanup is periodic (every 100 checks); could be optimized with background thread
- **ACK routing:** ACKs currently follow exact reverse path; could explore reverse-path optimization in future

---

## Conclusion

Phase 8 baseline successfully establishes performance metrics across all core subsystems:
- ✅ Latency (1-hop/4-hop)
- ✅ Reliability (ACK loss recovery)
- ✅ Persistence (enqueue/replay)
- ✅ Recovery (route restoration)

**Next Phase:** Optimization decisions should be data-driven and targeted only at measured bottlenecks, not speculative improvements.
