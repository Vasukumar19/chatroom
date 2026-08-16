import math
import statistics
import time

from p2p.reliability import ReliableReceiver, ReliableSender
from p2p.router import Router
from p2p.routing import RoutingTable
from p2p.testing import MeshMockNetwork, RouterAwareReliableSender
from p2p.transport import MockTransport


def percentile(samples, pct):
    if not samples:
        return 0.0
    values = sorted(samples)
    index = max(0, min(len(values) - 1, math.ceil((pct / 100.0) * len(values)) - 1))
    return values[index]


def measure_one_hop(iterations=200):
    samples = []
    for idx in range(iterations):
        transport = MockTransport()
        received = []
        ReliableReceiver('B', transport, lambda msg, addr: received.append(msg['payload']), auto_register=True)
        sender = ReliableSender('A', transport, timeout=0.02, max_retries=1)

        start = time.perf_counter()
        ok = sender.send('B', {'value': idx})
        elapsed = time.perf_counter() - start
        if not ok:
            raise RuntimeError('one-hop benchmark unexpectedly failed')
        samples.append(elapsed)

    return {
        'p50': percentile(samples, 50),
        'p95': percentile(samples, 95),
        'p99': percentile(samples, 99),
        'min': min(samples) if samples else 0.0,
        'max': max(samples) if samples else 0.0,
        'mean': statistics.fmean(samples) if samples else 0.0,
    }


def measure_four_hop(iterations=25):
    samples = []
    for idx in range(iterations):
        mesh = MeshMockNetwork(verbose=False)
        transport_a = mesh.add_node('A')
        transport_b = mesh.add_node('B')
        transport_c = mesh.add_node('C')
        transport_d = mesh.add_node('D')

        route_a = RoutingTable()
        route_b = RoutingTable()
        route_c = RoutingTable()
        route_d = RoutingTable()

        route_a.add_route('D', 'B', 'B', 10001)
        route_b.add_route('D', 'C', 'C', 10002)
        route_b.add_route('A', 'A', 'A', 10000)
        route_c.add_route('D', 'D', 'D', 10003)
        route_c.add_route('A', 'B', 'B', 10001)
        route_d.add_route('A', 'C', 'C', 10002)

        router_a = Router('A', transport_a, route_a)
        router_b = Router('B', transport_b, route_b)
        router_c = Router('C', transport_c, route_c)
        router_d = Router('D', transport_d, route_d)
        router_a.start(); router_b.start(); router_c.start(); router_d.start()

        received = []

        def app_handler(msg, addr):
            received.append(msg['payload'])

        receiver = ReliableReceiver('D', transport_d, app_handler, auto_register=False)
        router_d.add_app_handler(receiver._on_transport_message)

        sender = RouterAwareReliableSender('A', router_a, transport_a, timeout=0.05, max_retries=1)
        transport_a.register_handler(sender._on_transport_message)

        start = time.perf_counter()
        ok = sender.send('D', {'value': idx})
        elapsed = time.perf_counter() - start
        if not ok:
            raise RuntimeError('four-hop benchmark unexpectedly failed')
        samples.append(elapsed)

    return {
        'p50': percentile(samples, 50),
        'p95': percentile(samples, 95),
        'p99': percentile(samples, 99),
        'min': min(samples) if samples else 0.0,
        'max': max(samples) if samples else 0.0,
        'mean': statistics.fmean(samples) if samples else 0.0,
    }


def main():
    one_hop = measure_one_hop()
    four_hop = measure_four_hop()

    print('DisasterConnect Performance Baseline')
    print('====================================')
    print('')
    print('1-hop latency')
    print(f"  p50: {one_hop['p50'] * 1000:.3f} ms")
    print(f"  p95: {one_hop['p95'] * 1000:.3f} ms")
    print(f"  p99: {one_hop['p99'] * 1000:.3f} ms")
    print(f"  min: {one_hop['min'] * 1000:.3f} ms")
    print(f"  max: {one_hop['max'] * 1000:.3f} ms")
    print('')
    print('4-hop latency')
    print(f"  p50: {four_hop['p50'] * 1000:.3f} ms")
    print(f"  p95: {four_hop['p95'] * 1000:.3f} ms")
    print(f"  p99: {four_hop['p99'] * 1000:.3f} ms")
    print(f"  min: {four_hop['min'] * 1000:.3f} ms")
    print(f"  max: {four_hop['max'] * 1000:.3f} ms")


if __name__ == '__main__':
    main()
