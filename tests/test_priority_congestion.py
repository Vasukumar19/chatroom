import time
import pytest
from p2p.testing import MockTransport
from p2p.qos import PriorityTransport
from p2p.protocol import create_envelope

class TrackedMockTransport(MockTransport):
    """A mock transport that tracks the order of sent messages."""
    def __init__(self):
        super().__init__()
        self.sent_order = []
        
    def send(self, address, message):
        self.sent_order.append(message)
        super().send(address, message)


def test_priority_congestion_sos_forwarded_first():
    """
    Test that under congestion, a High priority (SOS) message 
    is forwarded before the existing backlog of Normal messages.
    """
    inner = TrackedMockTransport()
    
    # Wrap with PriorityTransport. 
    # Use a large send_delay to easily simulate congestion in a test.
    qos_transport = PriorityTransport(inner, max_queue_size=10, send_delay=0.1)
    
    # We want to enqueue quickly before the worker thread can drain them
    with qos_transport.queue_lock:
        # Pause the worker from pulling
        pass
    
    # Enqueue a burst of Normal messages (priority 1)
    for i in range(5):
        msg = create_envelope('data', source='A', payload={'text': f'normal {i}'}, priority=1)
        # Manually bypassing send to simulate burst during lock, or we can just call send
        # Actually, calling send will try to acquire lock, so let's just let the worker run.
        # But wait, if send_delay is 0.1, enqueuing 5 messages takes microsec, so they will definitely queue up.
    
    qos_transport.start()
    
    # Enqueue burst
    for i in range(5):
        msg = create_envelope('data', source='A', payload={'text': f'normal {i}'}, priority=1)
        qos_transport.send(('127.0.0.1', 8000), msg)
        
    # Enqueue SOS message (priority 0)
    sos_msg = create_envelope('data', source='A', payload={'text': 'SOS'}, priority=0)
    qos_transport.send(('127.0.0.1', 8000), sos_msg)
    
    # Wait for the queue to drain
    time.sleep(1.0)
    qos_transport.stop()
    
    sent = inner.sent_order
    assert len(sent) == 6
    
    # The worker thread grabs one item immediately when start() runs or queue gets first item.
    # So the *first* sent item will probably be 'normal 0' (it was enqueued and popped before the SOS arrived).
    # But the SOS message MUST be processed before the REST of the normal backlog.
    
    sos_indices = [i for i, m in enumerate(sent) if m['payload'].get('text') == 'SOS']
    assert len(sos_indices) == 1
    
    # It shouldn't be at the very end of the backlog
    # Usually it will be at index 1 or 2, well before the last normal messages.
    assert sos_indices[0] < 5, "SOS message was not prioritized!"

def test_priority_congestion_drops_low_priority_under_pressure():
    inner = TrackedMockTransport()
    # Capacity of 3 messages
    qos_transport = PriorityTransport(inner, max_queue_size=3, send_delay=1.0)
    
    qos_transport.start()
    
    # 1. Fill the queue with Low priority (priority 2)
    # The first one is immediately popped by the worker, leaving 0 in queue.
    # So we send 4 total to fill the queue (1 popped + 3 queued)
    for i in range(4):
        msg = create_envelope('data', source='A', payload={'text': f'low {i}'}, priority=2)
        qos_transport.send(('127.0.0.1', 8000), msg)
        
    # Queue currently has 3 Low messages. 
    # 2. Send an SOS (priority 0). This should drop a Low message to make room!
    sos_msg = create_envelope('data', source='A', payload={'text': 'SOS'}, priority=0)
    qos_transport.send(('127.0.0.1', 8000), sos_msg)
    
    # Wait for completion (needs 5 seconds total if 5 messages, but we only have 4 valid)
    # Actually just check the queue state before waiting
    with qos_transport.queue_lock:
        # Check what's in the queues right now
        high_q = qos_transport.queues[0]
        low_q = qos_transport.queues[2]
        assert len(high_q) == 1
        assert len(low_q) == 2  # One was dropped!
        
    qos_transport.stop()
