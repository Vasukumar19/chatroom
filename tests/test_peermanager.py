import time
from p2p.peermanager import PeerManager


def test_peermanager_discovery_and_timeout():
    pm = PeerManager('me', peer_timeout=1)
    discovered = []
    lost = []

    pm.on_peer_discovered = lambda pid, ip, port: discovered.append((pid, ip, port))
    pm.on_peer_lost = lambda pid: lost.append(pid)

    pm.start()
    pm.update_peer('p1', '127.0.0.1', 5000)
    time.sleep(0.1)
    assert len(discovered) == 1
    assert 'p1' in pm.get_peers()

    # Wait for timeout (peer_timeout=1)
    time.sleep(1.5)
    assert 'p1' not in pm.get_peers()
    assert 'p1' in lost
    pm.stop()
