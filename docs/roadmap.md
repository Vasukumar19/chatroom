# DisasterConnect roadmap

## Completed

1. Versioned protocol and validation.
2. Transport abstraction with UDP and deterministic mock transports.
3. Peer management, discovery, adaptive routing, and route hardening.
4. Reliable ACK/retry delivery, duplicate protection, store-and-forward, and recovery replay.
5. Performance baseline and reproducible benchmark scripts.
6. Heterogeneous simulated Ethernet, Bluetooth-style, and Wi-Fi Direct-style transport paths.
7. End-to-end payload protection using AES-GCM and Ed25519, trusted peers, replay detection, and encrypted store-and-forward replay.

## Current focus: polish and evidence

- Keep the regression suite green and benchmark results reproducible.
- Validate real transport adapters separately before making hardware-latency claims.
- Improve documentation, demos, and interview-ready architecture narratives.

No additional feature phase is planned. The next meaningful work is presentation quality and real-hardware validation, not a larger framework.
