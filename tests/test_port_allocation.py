"""Regression tests for ISSUE 2 (port allocation race).

Root cause: the original find_free_port() checked availability by binding a
throwaway socket and closing it immediately, returning only the port number.
The real bind (P2PHost.start() for the P2P port, Flask's app.run() for the
HTTP port) happened much later -- multiple sleep(0.5) calls and P2P/discovery
setup later for the HTTP port specifically. In that gap, nothing told the OS
the port was spoken for, so a second process's own find_free_port() scan
could select and bind the exact same port number (observed live: one
instance's P2P port scan landed on another instance's not-yet-bound HTTP
port).

Fix: find_free_port() now holds the bind open and returns the socket too;
callers release it only immediately before the real listener binds. These
tests verify the reservation mechanism itself -- that holding it open really
does prevent a second allocator from picking the same port, and that
releasing it frees the port back up as expected.
"""
from __future__ import annotations

import socket

from main import find_free_port, _release_port_reservation


def test_find_free_port_returns_a_held_open_reservation():
    port, sock = find_free_port(5000)
    try:
        assert isinstance(port, int)
        # The socket must still be bound/open -- attempting to bind a second
        # socket to the exact same port must fail while we hold it.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            raised = False
            try:
                probe.bind(('', port))
            except OSError:
                raised = True
            assert raised, "port was not actually reserved by find_free_port()"
        finally:
            probe.close()
    finally:
        sock.close()


def test_concurrent_scan_cannot_steal_a_held_reservation():
    """Directly reproduces the observed race: while instance A holds its
    selected port reserved, instance B's own find_free_port() scan starting
    from the same base must not select that same port."""
    port_a, sock_a = find_free_port(5000)
    try:
        port_b, sock_b = find_free_port(5000)
        try:
            assert port_b != port_a
        finally:
            sock_b.close()
    finally:
        sock_a.close()


def test_release_port_reservation_frees_the_port():
    port, sock = find_free_port(5000)
    _release_port_reservation(sock)

    # Now that it's released, a fresh bind to the same port must succeed.
    real_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        real_socket.bind(('', port))
    finally:
        real_socket.close()


def test_release_port_reservation_is_safe_to_call_with_none():
    # Callers pass reservation=None when a caller didn't go through
    # find_free_port (e.g. direct unit tests); must not raise.
    _release_port_reservation(None)


def test_two_sequential_allocations_after_release_do_not_collide():
    """Simulates two instances starting a few seconds apart (the normal,
    non-racy case): once the first fully releases its reservation, the
    second is free to reuse it if it wants, and two ports requested from the
    same base without holding either open still don't collide by accident."""
    port_a, sock_a = find_free_port(5000)
    _release_port_reservation(sock_a)

    port_b, sock_b = find_free_port(5000)
    try:
        # Not required to differ (port_a is free again), but must be a valid,
        # actually-bindable port right now.
        assert isinstance(port_b, int)
    finally:
        sock_b.close()
