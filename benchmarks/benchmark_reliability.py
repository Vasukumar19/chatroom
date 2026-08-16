import math
import statistics
import time

from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.transport import MockTransport


def percentile(samples, pct):
    if not samples:
        return 0.0
    values = sorted(samples)
    index = max(0, min(len(values) - 1, math.ceil((pct / 100.0) * len(values)) - 1))
    return values[index]


class DroppingTransport(MockTransport):
    def __init__(self, drop_ack_count=0):
        super().__init__()
        self.drop_ack_count = int(drop_ack_count)
        self.acks_dropped = 0

    def send(self, address, message):
        if message.get('type') == 'ack' and self.acks_dropped < self.drop_ack_count:
            self.acks_dropped += 1
            return
        super().send(address, message)


def run_case(drop_count, iterations=30):
    retry_samples = []
    delivered = 0
    for _ in range(iterations):
        transport = DroppingTransport(drop_count)
        receiver = ReliableReceiver('B', transport, lambda msg, addr: None)
        sender = ReliableSender('A', transport, timeout=0.02, max_retries=3)
        ok = sender.send('B', {'value': 'retry'})
        retry_samples.append(sender.retry_count)
        delivered += 1 if ok else 0
    return {
        'drop_count': drop_count,
        'retries': retry_samples,
        'delivery_rate': delivered / iterations if iterations else 0.0,
        'p50': percentile(retry_samples, 50),
        'p95': percentile(retry_samples, 95),
        'p99': percentile(retry_samples, 99),
        'mean': statistics.fmean(retry_samples) if retry_samples else 0.0,
    }


def main():
    cases = [
        ('1 dropped ACK', run_case(1)),
        ('2 dropped ACKs', run_case(2)),
        ('3 dropped ACKs', run_case(3)),
    ]

    print('DisasterConnect Reliability Baseline')
    print('===================================')
    for label, result in cases:
        print(f'')
        print(label)
        print(f"  retries p50/p95/p99: {result['p50']:.1f} / {result['p95']:.1f} / {result['p99']:.1f}")
        print(f"  delivery rate: {result['delivery_rate'] * 100:.1f}%")


if __name__ == '__main__':
    main()
