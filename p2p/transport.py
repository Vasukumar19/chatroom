"""Transport abstraction layer.

Defines a simple Transport interface and a MockTransport for testing.
Real UDP transport will be implemented incrementally in Phase 2 but tests
initially rely on MockTransport to avoid flaky network dependencies.
"""
from abc import ABC, abstractmethod
from typing import Callable, Tuple, Dict, Any, Optional
import socket
import threading
import json


class Transport(ABC):
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        raise NotImplementedError()

    @abstractmethod
    def register_handler(self, handler: Callable[[Dict[str, Any], Tuple[str, int]], None]) -> None:
        raise NotImplementedError()


class MockTransport(Transport):
    """In-memory transport useful for unit tests.

    Handlers are called synchronously in the same thread to keep tests deterministic.
    """
    def __init__(self):
        self.handlers = []
        self.started = False
        self.last_sent = None
        self.last_sent_addr = None
        self.sent_history = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        # record last sent for tests and simulate delivery by invoking handlers
        self.last_sent = message
        self.last_sent_addr = address
        self.sent_history.append((address, message))
        for h in list(self.handlers):
            try:
                h(message, address)
            except Exception:
                pass

    def simulate_incoming(self, message: Dict[str, Any], address: Tuple[str, int]) -> None:
        # Directly invoke handlers as if a message arrived from `address`
        for h in list(self.handlers):
            try:
                h(message, address)
            except Exception:
                pass

    def register_handler(self, handler: Callable[[Dict[str, Any], Tuple[str, int]], None]) -> None:
        self.handlers.append(handler)


class UDPTransport(Transport):
    """A hardened UDP transport wrapper.

    Features:
    - Optional broadcast support (`broadcast=True` sets `SO_BROADCAST`).
    - Configurable recv timeout.
    - Clean start/stop with thread join.
    - Explicit bind address/port.
    - JSON framing and robust error handling.
    """
    def __init__(self, bind_addr: str = '0.0.0.0', bind_port: int = 0, *, broadcast: bool = False, timeout: float = 1.0):
        self.bind_addr = bind_addr
        self.bind_port = bind_port
        self.broadcast = broadcast
        self.timeout = float(timeout)
        self.sock: Optional[socket.socket] = None
        self.recv_thread: Optional[threading.Thread] = None
        self.running = False
        self.handlers = []

    def start(self) -> None:
        if self.running:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if self.broadcast:
                try:
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                except Exception:
                    pass
            self.sock.bind((self.bind_addr, self.bind_port))
            self.sock.settimeout(self.timeout)
        except Exception:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
            self.sock = None
            raise

        self.running = True

        def _loop():
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(65536)
                    try:
                        msg = json.loads(data.decode('utf-8'))
                    except Exception:
                        # malformed JSON — ignore
                        continue
                    for h in list(self.handlers):
                        try:
                            h(msg, addr)
                        except Exception:
                            pass
                except socket.timeout:
                    continue
                except Exception:
                    if self.running:
                        # swallow and continue; handlers should be resilient
                        continue

        self.recv_thread = threading.Thread(target=_loop, daemon=True)
        self.recv_thread.start()

    def stop(self) -> None:
        self.running = False
        # close socket first to unblock recv
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        # join thread if running
        try:
            if self.recv_thread and self.recv_thread.is_alive():
                self.recv_thread.join(timeout=1.0)
        except Exception:
            pass

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        if not self.sock:
            raise RuntimeError('Transport not started')
        try:
            data = json.dumps(message).encode('utf-8')
            self.sock.sendto(data, address)
        except Exception:
            raise

    def register_handler(self, handler: Callable[[Dict[str, Any], Tuple[str, int]], None]) -> None:
        self.handlers.append(handler)
