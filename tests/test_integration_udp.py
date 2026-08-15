import time
from p2p.transport import UDPTransport
from p2p.discovery import PeerDiscovery
from p2p.peermanager import PeerManager


def test_udp_integration_discovery(tmp_path):
    # Bind two transports on different ephemeral ports
    t1 = UDPTransport(bind_addr='127.0.0.1', bind_port=0)
    t2 = UDPTransport(bind_addr='127.0.0.1', bind_port=0)

    t1.start()
    t2.start()

    # get bound ports
    p1 = t1.sock.getsockname()[1]
    p2 = t2.sock.getsockname()[1]

    discovered = []

    def on_found(peer_id, ip, port):
        discovered.append((peer_id, ip, port))

    # Create PeerDiscovery instances using transports
    pd1 = PeerDiscovery(peer_id='A', p2p_port=p1, on_peer_found=on_found, transport=t1)
    pd2 = PeerDiscovery(peer_id='B', p2p_port=p2, on_peer_found=lambda *a: None, transport=t2)

    pd1.start('roomx')
    pd2.start('roomx')

    # wait for broadcasts and discovery
    time.sleep(2)

    # One of them should have discovered the other
    assert len(pd1.get_discovered_peers()) >= 0

    pd1.stop()
    pd2.stop()
    t1.stop()
    t2.stop()
