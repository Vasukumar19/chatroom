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


def run_recovery_case(iterations=100, warmup=10):
    samples = []
    for _ in range(iterations + warmup):
        temp_dir = Path(tempfile.mkdtemp())
        queue = StoreForwardQueue(str(temp_dir / 'recovery.sqlite'))
        transport = MockTransport()
        ReliableReceiver('E', transport, lambda msg, addr: None)
        sender = ReliableSender('A', transport, timeout=0.02, max_retries=1)
        route = DummyRouteManager(False)
        manager = StoreForwardManager(queue=queue, reliable_sender=sender, route_manager=route)

        queue.enqueue(QueuedMessage(
            message_id='recovery:bench',
            source='A',
            destination='E',
            envelope={'type': 'data', 'message_id': 'recovery:bench', 'source': 'A', 'destination': 'E', 'payload': {'value': 'recovery'}},
            created_at='2026-01-01T00:00:00Z',
        ))

        if _ >= warmup:
            recovery_start = time.perf_counter()
            route.available = True
            result = manager.replay('E')
            recovery_elapsed = time.perf_counter() - recovery_start
            samples.append({'recovery_ms': recovery_elapsed * 1000.0, 'delivered': bool(result and result[0].status == 'DELIVERED')})

        queue.close()

    recovery_values = [s['recovery_ms'] for s in samples]
    delivered_count = sum(1 for s in samples if s['delivered'])

    return {
        'recovery_p50': percentile(recovery_values, 50),
        'recovery_p95': percentile(recovery_values, 95),
        'recovery_p99': percentile(recovery_values, 99),
        'recovery_min': min(recovery_values) if recovery_values else 0.0,
        'recovery_max': max(recovery_values) if recovery_values else 0.0,
        'delivered_rate': delivered_count / len(samples) if samples else 0.0,
        'mean_recovery': statistics.fmean(recovery_values) if recovery_values else 0.0,
    }


def main():
    result = run_recovery_case()
    print('DisasterConnect Recovery Baseline')
    print('=================================')
    print('')
    print('Recovery replay')
    print(f"  p50: {result['recovery_p50']:.3f} ms")
    print(f"  p95: {result['recovery_p95']:.3f} ms")
    print(f"  p99: {result['recovery_p99']:.3f} ms")
    print(f"  min: {result['recovery_min']:.3f} ms")
    print(f"  max: {result['recovery_max']:.3f} ms")
    print('')
    print(f"  successful delivery rate: {result['delivered_rate'] * 100:.1f}%")


if __name__ == '__main__':
    main()
