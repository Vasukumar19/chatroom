import time
import pytest

from p2p.transport import MockTransport, UDPTransport
from p2p.peermanager import PeerManager
from p2p.heartbeat import HeartbeatManager, ALIVE, SUSPECT, DEAD


def test_heartbeat_sent_and_received_via_mock():
    tA = MockTransport()
    tB = MockTransport()
    tA.start()
    tB.start()

    pA = PeerManager('A')
    pB = PeerManager('B')

    # Ensure A knows B so it will send heartbeats to B
    pA.update_peer('B', '127.0.0.1', 10001)

    hbA = HeartbeatManager('A', pA, tA, interval=0.1)
    hbB = HeartbeatManager('B', pB, tB, interval=0.1)

    # cross-register handlers to simulate network
    tA.register_handler(hbB._on_transport_message)
    tB.register_handler(hbA._on_transport_message)

    hbA.start()
    hbB.start()

    # trigger a send
    hbA.send_heartbeats()

    # B should have learned about A due to receiving heartbeat
    time.sleep(0.05)
    peers_b = pB.get_peers()
    assert 'A' in peers_b

    hbA.stop()
    hbB.stop()
    tA.stop()
    tB.stop()


def test_peer_transitions_to_suspect_and_dead():
    t = MockTransport()
    t.start()
    p = PeerManager('X')
    # add peer Y so manager will track misses
    p.update_peer('Y', '127.0.0.1', 11111)

    hb = HeartbeatManager('X', p, t, interval=0.01, suspect_threshold=1, dead_threshold=2)

    # call check_timeouts repeatedly to simulate missed heartbeats
    hb._check_timeouts()
    assert p.peers['Y'].status == SUSPECT
    hb._check_timeouts()
    assert p.peers['Y'].status == DEAD


def test_peer_recovers_after_missed_then_seen():
    t = MockTransport()
    t.start()
    p = PeerManager('M')
    p.update_peer('N', '127.0.0.1', 22222)
    hb = HeartbeatManager('M', p, t, interval=0.01, suspect_threshold=1, dead_threshold=2)

    hb._check_timeouts()
    assert p.peers['N'].status == SUSPECT
    hb._check_timeouts()
    assert p.peers['N'].status == DEAD

    # simulate incoming heartbeat from N
    env = {'type': 'heartbeat', 'source': 'N', 'message_id': 'x', 'timestamp': 't', 'ttl': 1, 'payload': {}}
    hb._on_transport_message(env, ('127.0.0.1', 22222))
    # recovered to ALIVE
    assert p.peers['N'].status == ALIVE


def test_udp_integration_heartbeat():
    t1 = UDPTransport(bind_addr='127.0.0.1', bind_port=0, timeout=0.5)
    t2 = UDPTransport(bind_addr='127.0.0.1', bind_port=0, timeout=0.5)
    t1.start()
    t2.start()

    p1 = PeerManager('A')
    p2 = PeerManager('B')

    p1_port = t1.sock.getsockname()[1]
    p2_port = t2.sock.getsockname()[1]

    # make peers know each other
    p1.update_peer('B', '127.0.0.1', p2_port)
    p2.update_peer('A', '127.0.0.1', p1_port)

    hb1 = HeartbeatManager('A', p1, t1, interval=0.2, suspect_threshold=1, dead_threshold=3)
    hb2 = HeartbeatManager('B', p2, t2, interval=0.2, suspect_threshold=1, dead_threshold=3)

    hb1.start()
    hb2.start()

    # allow a few intervals for heartbeats and acks to flow
    time.sleep(1.0)

    assert p1.peers['B'].status == 'ALIVE' or p1.peers['B'].status == 'ONLINE' or p1.peers['B'].status == ALIVE
    assert p2.peers['A'].status == 'ALIVE' or p2.peers['A'].status == 'ONLINE' or p2.peers['A'].status == ALIVE

    hb1.stop()
    hb2.stop()
    t1.stop()
    t2.stop()
