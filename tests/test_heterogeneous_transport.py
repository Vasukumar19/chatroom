from p2p.router import Router
from p2p.routing import RoutingTable
from p2p.security import SecurityContext, generate_aes_key
from p2p.testing import HeterogeneousMeshNetwork, RouterAwareReliableReceiver, RouterAwareReliableSender


def test_router_forwards_across_ethernet_bluetooth_and_wifi_direct():
    network = HeterogeneousMeshNetwork()
    transports = {node: network.add_node(node) for node in 'ABCD'}
    network.connect('A', 'B', 'ethernet')
    network.connect('B', 'C', 'bluetooth')
    network.connect('C', 'D', 'wifi_direct')

    routes = {node: RoutingTable() for node in 'ABCD'}
    routes['A'].add_route('D', 'B', 'B', 0, transport='ethernet')
    routes['B'].add_route('D', 'C', 'C', 0, transport='bluetooth')
    routes['C'].add_route('D', 'D', 'D', 0, transport='wifi_direct')

    routers = {node: Router(node, transports[node], routes[node]) for node in 'ABCD'}
    for router in routers.values():
        router.start()
    delivered = []
    routers['D'].add_app_handler(lambda message, address: delivered.append(message))

    routers['A'].send('D', {'text': 'mixed path'})

    assert [entry['transport'] for entry in network.deliveries] == ['ethernet', 'bluetooth', 'wifi_direct']
    assert len(delivered) == 1
    assert delivered[0]['payload'] == {'text': 'mixed path'}
    assert delivered[0]['hop_count'] == 3


def test_mixed_links_encrypt_retry_and_deliver_once():
    network = HeterogeneousMeshNetwork()
    transports = {node: network.add_node(node) for node in 'ABCD'}
    network.connect('A', 'B', 'ethernet')
    network.connect('B', 'C', 'bluetooth')
    network.connect('C', 'D', 'wifi_direct')
    routes = {node: RoutingTable() for node in 'ABCD'}
    routes['A'].add_route('D', 'B', 'B', 0, transport='ethernet')
    routes['B'].add_route('D', 'C', 'C', 0, transport='bluetooth')
    routes['B'].add_route('A', 'A', 'A', 0, transport='ethernet')
    routes['C'].add_route('D', 'D', 'D', 0, transport='wifi_direct')
    routes['C'].add_route('A', 'B', 'B', 0, transport='bluetooth')
    routes['D'].add_route('A', 'C', 'C', 0, transport='wifi_direct')
    routers = {node: Router(node, transports[node], routes[node]) for node in 'ABCD'}
    for router in routers.values():
        router.start()

    key = generate_aes_key()
    sender_security, receiver_security = SecurityContext('A', key), SecurityContext('D', key)
    receiver_security.trust_peer('A', sender_security.public_key_bytes())
    delivered = []
    receiver = RouterAwareReliableReceiver('D', routers['D'], transports['D'], lambda msg, addr: delivered.append(msg['payload']), security=receiver_security)
    routers['D'].add_app_handler(receiver._on_transport_message)
    sender = RouterAwareReliableSender('A', routers['A'], transports['A'], timeout=0.01, max_retries=1, security=sender_security)
    transports['A'].register_handler(sender._on_transport_message)

    # Drop the first return ACK at D; the retry must retain one application delivery.
    wifi_at_d = transports['D'].transports['wifi_direct']
    original_send = wifi_at_d.send
    dropped = {'value': False}

    def drop_first_ack(address, message):
        if message.get('type') == 'ack' and not dropped['value']:
            dropped['value'] = True
            return
        original_send(address, message)

    wifi_at_d.send = drop_first_ack

    assert sender.send('D', {'text': 'protected'}) is True
    assert sender.retry_count == 1
    assert delivered == [{'text': 'protected'}]
    assert [event['transport'] for event in network.deliveries] == [
        'ethernet', 'bluetooth', 'wifi_direct',  # initial encrypted DATA
        'ethernet', 'bluetooth', 'wifi_direct',  # retry
        'wifi_direct', 'bluetooth', 'ethernet',  # routed ACK
    ]
