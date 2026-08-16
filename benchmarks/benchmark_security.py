"""Measure cryptographic operations and secure end-to-end delivery separately."""
import math
import time

from p2p.protocol import create_envelope
from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.security import SecurityContext, generate_aes_key
from p2p.transport import MockTransport


def percentile(samples, pct):
    values = sorted(samples)
    return values[max(0, math.ceil(len(values) * pct / 100) - 1)] if values else 0.0


def summary(samples):
    return {name: percentile(samples, pct) for name, pct in [('p50', 50), ('p95', 95), ('p99', 99)]}


def contexts():
    key = generate_aes_key()
    sender, receiver = SecurityContext('A', key), SecurityContext('B', key)
    receiver.trust_peer('A', sender.public_key_bytes())
    return sender, receiver


def measure_operations(payload_size=4096, iterations=200):
    """Time AES-GCM and Ed25519 independently, excluding transport/routing."""
    sender, receiver = contexts()
    payload = {'data': 'x' * payload_size}
    envelope = create_envelope('data', 'A', payload, destination='B', message_id='operation-benchmark')
    encryption, signing, verification, decryption = [], [], [], []
    for _ in range(iterations):
        start = time.perf_counter()
        nonce, ciphertext = sender.encrypt_payload(payload)
        encryption.append(time.perf_counter() - start)
        start = time.perf_counter()
        signature = sender.sign_envelope(envelope, nonce, ciphertext)
        signing.append(time.perf_counter() - start)
        start = time.perf_counter()
        receiver.verify_envelope(envelope, nonce, ciphertext, signature)
        verification.append(time.perf_counter() - start)
        start = time.perf_counter()
        assert receiver.decrypt_payload(nonce, ciphertext) == payload
        decryption.append(time.perf_counter() - start)
    return {
        'Encrypt': summary(encryption), 'Decrypt': summary(decryption),
        'Sign': summary(signing), 'Verify': summary(verification),
    }


def measure_delivery(payload_size, *, encrypted, iterations=200):
    """Time reliable plaintext or encrypted delivery on the same mock transport."""
    samples, delivered = [], 0
    sender_security, receiver_security = contexts() if encrypted else (None, None)
    for index in range(iterations):
        transport = MockTransport()
        received = []
        ReliableReceiver('B', transport, lambda message, address: received.append(message), security=receiver_security)
        sender = ReliableSender('A', transport, timeout=0.02, max_retries=1, security=sender_security)
        start = time.perf_counter()
        ok = sender.send('B', {'data': 'x' * payload_size}, message_id=f'{payload_size}-{index}')
        samples.append(time.perf_counter() - start)
        delivered += bool(ok and len(received) == 1)
    result = summary(samples)
    result['delivery_rate'] = delivered / iterations if iterations else 0.0
    return result


def print_summary(name, result):
    print(f'{name:<20} p50={result["p50"] * 1000:.3f} ms  p95={result["p95"] * 1000:.3f} ms  p99={result["p99"] * 1000:.3f} ms')


def main():
    print('Cryptographic operations (4 KB payload; excludes routing and transport)')
    for name, result in measure_operations().items():
        print_summary(name, result)

    print('\nReliable end-to-end delivery (same in-memory transport)')
    print('Payload | Plain p50/p95/p99 | Encrypted p50/p95/p99 | Delivery | Security overhead')
    print('------- | ----------------- | --------------------- | -------- | -----------------')
    for size in (256, 1024, 4096, 16384, 65536):
        plain = measure_delivery(size, encrypted=False)
        encrypted = measure_delivery(size, encrypted=True)
        overhead = (encrypted['p50'] / plain['p50'] - 1) * 100 if plain['p50'] else 0.0
        print(
            f'{size:>6} B | '
            f'{plain["p50"] * 1000:.3f}/{plain["p95"] * 1000:.3f}/{plain["p99"] * 1000:.3f} ms | '
            f'{encrypted["p50"] * 1000:.3f}/{encrypted["p95"] * 1000:.3f}/{encrypted["p99"] * 1000:.3f} ms | '
            f'{encrypted["delivery_rate"] * 100:.1f}% | {overhead:.1f}%'
        )


if __name__ == '__main__':
    main()
