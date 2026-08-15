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

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        # Simulate delivery by invoking handlers with the provided 'address'
        for h in list(self.handlers):
            try:
                h(message, address)
            except Exception:
                pass

    def register_handler(self, handler: Callable[[Dict[str, Any], Tuple[str, int]], None]) -> None:
        self.handlers.append(handler)


class UDPTransport(Transport):
    """A minimal UDP transport wrapper (basic, not yet used by app).

    This class provides send/receive over UDP for future integration. It keeps
    receive loop in a background thread and calls registered handlers.
    """
    def __init__(self, bind_addr: str = '0.0.0.0', bind_port: int = 0):
        self.bind_addr = bind_addr
        self.bind_port = bind_port
        self.sock: Optional[socket.socket] = None
        self.recv_thread: Optional[threading.Thread] = None
        self.running = False
        self.handlers = []

    def start(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.bind_addr, self.bind_port))
        self.sock.settimeout(1.0)
        self.running = True

        def _loop():
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(65536)
                    try:
                        msg = json.loads(data.decode('utf-8'))
                    except Exception:
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
                        pass

        self.recv_thread = threading.Thread(target=_loop, daemon=True)
        self.recv_thread.start()

    def stop(self) -> None:
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def send(self, address: Tuple[str, int], message: Dict[str, Any]) -> None:
        if not self.sock:
            raise RuntimeError('Transport not started')
        data = json.dumps(message).encode('utf-8')
        self.sock.sendto(data, address)

    def register_handler(self, handler: Callable[[Dict[str, Any], Tuple[str, int]], None]) -> None:
        self.handlers.append(handler)
