"""Compare homogeneous vs heterogeneous simulated transport dispatch.

This intentionally reports mock-network overhead, not real Ethernet,
Bluetooth, or Wi-Fi Direct performance.
"""
import math
import statistics
import time

from p2p.router import Router
from p2p.routing import RoutingTable
from p2p.testing import HeterogeneousMeshNetwork


def percentile(samples, pct):
    values = sorted(samples)
    return values[max(0, math.ceil(len(values) * pct / 100) - 1)] if values else 0.0


def measure(link_types, iterations=200):
    samples = []
    delivered = 0
    for _ in range(iterations):
        network = HeterogeneousMeshNetwork()
        nodes = 'ABCD' if len(link_types) == 3 else 'AB'
        destination = nodes[-1]
        transports = {node: network.add_node(node) for node in nodes}
        for left, right, link_type in zip(nodes, nodes[1:], link_types):
            network.connect(left, right, link_type)
        routes = {node: RoutingTable() for node in nodes}
        for node, next_hop, link_type in zip(nodes, nodes[1:], link_types):
            routes[node].add_route(destination, next_hop, next_hop, 0, transport=link_type)
        routers = {node: Router(node, transports[node], routes[node]) for node in nodes}
        for router in routers.values():
            router.start()
        received = []
        routers[destination].add_app_handler(lambda message, address: received.append(message))
        start = time.perf_counter()
        routers['A'].send(destination, {'value': 'benchmark'})
        samples.append(time.perf_counter() - start)
        delivered += bool(received)
    total = sum(samples)
    return {
        'p50': percentile(samples, 50), 'p95': percentile(samples, 95), 'p99': percentile(samples, 99),
        'throughput': len(samples) / total if total else 0.0, 'delivery_rate': delivered / len(samples),
    }


def main():
    one_hop = measure(['ethernet'])
    homogeneous = measure(['ethernet'] * 3)
    heterogeneous = measure(['ethernet', 'bluetooth', 'wifi_direct'])
    for name, result in [('1-hop Ethernet', one_hop), ('4-hop homogeneous', homogeneous), ('4-hop heterogeneous', heterogeneous)]:
        print(name)
        for metric in ('p50', 'p95', 'p99'):
            print(f"  {metric}: {result[metric] * 1000:.3f} ms")
        print(f"  throughput: {result['throughput']:.1f} messages/s")
        print(f"  delivery rate: {result['delivery_rate'] * 100:.1f}%")
    print(f"p50 overhead: {(heterogeneous['p50'] / homogeneous['p50'] - 1) * 100:.1f}%")


if __name__ == '__main__':
    main()
