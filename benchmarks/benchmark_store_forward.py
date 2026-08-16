import math
import statistics
import tempfile
import time
from pathlib import Path

from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.store_forward import QueuedMessage, StoreForwardQueue
from p2p.store_forward_manager import StoreForwardManager
from p2p.transport import MockTransport


def percentile(samples, pct):
    if not samples:
        return 0.0
    values = sorted(samples)
    index = max(0, min(len(values) - 1, math.ceil((pct / 100.0) * len(values)) - 1))
    return values[index]


class DummyRouteManager:
    def __init__(self, available):
        self.available = available

    def route_available(self, destination):
        return self.available


def measure_enqueue(iterations=200):
    temp_dir = Path(tempfile.mkdtemp())
    queue = StoreForwardQueue(str(temp_dir / 'enqueue.sqlite'))
    samples = []
    try:
        for idx in range(iterations):
            message = QueuedMessage(
                message_id=f'bench:{idx}',
                source='A',
                destination='E',
                envelope={'payload': {'value': idx}},
                created_at='2026-01-01T00:00:00Z',
            )
            start = time.perf_counter()
            queue.enqueue(message)
            samples.append(time.perf_counter() - start)
        return {
            'p50': percentile(samples, 50),
            'p95': percentile(samples, 95),
            'p99': percentile(samples, 99),
            'mean': statistics.fmean(samples) if samples else 0.0,
        }
    finally:
        queue.close()


def measure_replay(iterations=200, warmup=20):
    temp_dir = Path(tempfile.mkdtemp())
    queue = StoreForwardQueue(str(temp_dir / 'replay.sqlite'))
    transport = MockTransport()
    ReliableReceiver('E', transport, lambda msg, addr: None)
    sender = ReliableSender('A', transport, timeout=0.02, max_retries=1)
    route = DummyRouteManager(True)
    manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

    for idx in range(iterations + warmup):
        queue.enqueue(QueuedMessage(
            message_id=f'replay:{idx}',
            source='A',
            destination='E',
            envelope={'type': 'data', 'message_id': f'replay:{idx}', 'source': 'A', 'destination': 'E', 'payload': {'value': idx}},
            created_at='2026-01-01T00:00:00Z',
        ))

    samples = []
    try:
        for _ in range(warmup):
            manager.replay('E')

        for _ in range(iterations):
            start = time.perf_counter()
            manager.replay('E')
            samples.append(time.perf_counter() - start)

        return {
            'p50': percentile(samples, 50),
            'p95': percentile(samples, 95),
            'p99': percentile(samples, 99),
            'min': min(samples) if samples else 0.0,
            'max': max(samples) if samples else 0.0,
            'mean': statistics.fmean(samples) if samples else 0.0,
            'count': len(samples),
        }
    finally:
        queue.close()


def main():
    enqueue = measure_enqueue()
    replay = measure_replay()

    print('DisasterConnect Store-and-Forward Baseline')
    print('=========================================')
    print('')
    print('SQLite enqueue')
    print(f"  p50: {enqueue['p50'] * 1000:.3f} ms")
    print(f"  p95: {enqueue['p95'] * 1000:.3f} ms")
    print(f"  p99: {enqueue['p99'] * 1000:.3f} ms")
    print('')
    print('Queue replay')
    print(f"  p50: {replay['p50'] * 1000:.3f} ms")
    print(f"  p95: {replay['p95'] * 1000:.3f} ms")
    print(f"  p99: {replay['p99'] * 1000:.3f} ms")
    print(f"  min: {replay['min'] * 1000:.3f} ms")
    print(f"  max: {replay['max'] * 1000:.3f} ms")
    print(f"  samples: {replay['count']}")


if __name__ == '__main__':
    main()
