# Phases 9–10: simulated heterogeneous links and end-to-end security

Phase 9 introduces a small `MultiTransport` adapter. A route may now include
an optional `transport` name (`ethernet`, `bluetooth`, or `wifi_direct`), and
the router dispatches through that adapter. Existing routes with no transport
name continue to use the configured default adapter.

`HeterogeneousMeshNetwork` is a deterministic test-only network. It checks an
explicit topology such as A—Ethernet—B—Bluetooth—C—Wi-Fi Direct—D. It does not
model actual radio characteristics, and its benchmark must never be reported
as a real Bluetooth/Wi-Fi Direct measurement.

Phase 10 adds `SecurityContext`:

- AES-256-GCM encrypts the application payload with a fresh 96-bit nonce.
- Ed25519 signs ciphertext plus immutable routing metadata (`source`,
  `destination`, `message_id`, and type).
- Hops can mutate TTL and hop count without breaking end-to-end protection.
- Trusted public keys and a shared AES key are provisioned out of band.
- `SecurityContext.open()` rejects a replayed `message_id`; the reliability
  integration lets a retransmission be acknowledged while still delivering it
  only once to the application.

This is not a PKI or a key-exchange protocol. Keys must not be committed to the
repository, and deployment should use a secure provisioning mechanism.

## Reproducible experiments

After installing `requirements.txt`, run:

```powershell
pytest -q
python benchmarks/benchmark_transports.py
python benchmarks/benchmark_security.py
```

The transport benchmark prints p50/p95/p99, throughput, and delivery rate for
1-hop Ethernet plus 4-hop homogeneous and heterogeneous paths. The security
benchmark separately prints p50/p95/p99 for AES-GCM encryption/decryption and
Ed25519 signing/verification, then compares plaintext and encrypted reliable
delivery at 256 B, 1 KB, 4 KB, 16 KB, and 64 KB. Keep the generated output
with the hardware/OS/Python version and iteration count; do not claim an
improvement or supply a percentage until the commands have been run on the
target machine.
