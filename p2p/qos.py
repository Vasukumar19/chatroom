import threading
import time
from typing import Any, Callable, Dict, Tuple, Optional

from p2p.transport import Transport
from p2p.log import get_logger

log = get_logger("p2p.qos")

class PriorityTransport(Transport):
    """
    QoS-aware transport wrapper with a bounded queue and Priority/Congestion Control.
    Uses Weighted Round Robin (WRR) to prevent starvation.
    Priorities: 0 (High/SOS), 1 (Normal), 2 (Low).
    """
    def __init__(self, inner_transport: Transport, max_queue_size: int = 100, send_delay: float = 0.0):
        self.inner_transport = inner_transport
        self.max_queue_size = max_queue_size
        self.send_delay = send_delay
        
        self.queues = {0: [], 1: [], 2: []}
        self.weights = {0: 5, 1: 2, 2: 1}
        self.current_class = 0
        self.current_weight = self.weights[0]
        
        self.queue_lock = threading.Lock()
        self.queue_not_empty = threading.Condition(self.queue_lock)
        
        self.running = False
        self.worker_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.inner_transport.start()
        if self.running:
            return
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="QoSScheduler")
        self.worker_thread.start()

    def stop(self) -> None:
        self.running = False
        with self.queue_lock:
            self.queue_not_empty.notify_all()
        if self.worker_thread:
            self.worker_thread.join()
        self.inner_transport.stop()

    def get_queue_pressure(self) -> float:
        """Return the current queue utilization ratio [0.0, 1.0]."""
        with self.queue_lock:
            total_size = sum(len(q) for q in self.queues.values())
        if self.max_queue_size <= 0:
            return 0.0
        return min(1.0, max(0.0, float(total_size) / float(self.max_queue_size)))

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        # Default to NORMAL (1) if missing or invalid
        priority = message.get("priority", 1)
        if priority not in self.queues:
            priority = 1
            
        with self.queue_lock:
            total_size = sum(len(q) for q in self.queues.values())
            if total_size >= self.max_queue_size:
                # Congestion: drop lowest priority item to make room
                dropped = False
                for p in (2, 1, 0):
                    # We can drop from a queue if it has items AND its priority is worse (higher number) 
                    # than or equal to the new message's priority.
                    if p >= priority and self.queues[p]:
                        dropped_msg = self.queues[p].pop() # Drop newest (tail-drop)
                        dropped = True
                        log.warning(f"congestion drop: discarded msg {dropped_msg[1].get('message_id')} from priority {p}", extra={"msg_id": dropped_msg[1].get('message_id'), "priority": p})
                        break
                
                if not dropped:
                    # The queue is full of messages strictly more important than this one.
                    log.warning(f"congestion drop: discarded incoming msg {message.get('message_id')} (priority {priority})", extra={"msg_id": message.get('message_id'), "priority": priority})
                    return

            self.queues[priority].append((address, message))
            self.queue_not_empty.notify()

    def register_handler(self, handler: Callable[[Dict[str, Any], Tuple[str, int]], None]) -> None:
        self.inner_transport.register_handler(handler)

    def _next_message(self):
        if not any(self.queues.values()):
            return None
            
        # WRR scheduling
        for _ in range(3):
            if self.queues[self.current_class] and self.current_weight > 0:
                self.current_weight -= 1
                return self.queues[self.current_class].pop(0)
            
            # Advance to next class
            self.current_class = (self.current_class + 1) % 3
            self.current_weight = self.weights[self.current_class]
            
        # Fallback if logic missed (shouldn't happen)
        for p in (0, 1, 2):
            if self.queues[p]:
                return self.queues[p].pop(0)
        return None

    def _worker_loop(self):
        while self.running:
            with self.queue_lock:
                while self.running and not any(self.queues.values()):
                    self.queue_not_empty.wait()
                if not self.running:
                    break
                
                item = self._next_message()
            
            if item:
                address, message = item
                try:
                    self.inner_transport.send(address, message)
                except Exception as e:
                    log.debug(f"PriorityTransport inner send failed: {e}")
            
            if self.send_delay > 0:
                time.sleep(self.send_delay)
