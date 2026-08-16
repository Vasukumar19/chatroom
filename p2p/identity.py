"""Persistent peer identity for DisasterConnect.

Generates a peer_id once and stores it in a JSON file so that the same
logical identity is reused across application restarts.  IP address, port,
and TCP connection state are deliberately NOT stored here — they remain
dynamic.

Usage
-----
    from p2p.identity import load_or_create_identity

    peer_id = load_or_create_identity("/path/to/data/dir")
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

_IDENTITY_FILENAME = "peer_identity.json"


def load_or_create_identity(identity_dir: str) -> str:
    """Return a persistent peer_id, creating it if it does not yet exist.

    The identity is stored as ``peer_identity.json`` inside *identity_dir*.
    The directory is created if it does not exist.

    The write is done atomically (write to a temp file then rename) so that
    a concurrent crash cannot produce a half-written or empty file.

    Parameters
    ----------
    identity_dir:
        Directory in which ``peer_identity.json`` is stored.  Usually the
        same directory as the SQLite message queue (the application's working
        directory).

    Returns
    -------
    str
        An 8-character hex peer_id string that is stable across restarts.
    """
    os.makedirs(identity_dir, exist_ok=True)
    identity_path = os.path.join(identity_dir, _IDENTITY_FILENAME)

    # --- Try to load an existing identity first ---
    if os.path.isfile(identity_path):
        try:
            with open(identity_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            peer_id = data.get("peer_id", "").strip()
            if peer_id:
                return peer_id
            # File existed but contained no usable peer_id — fall through to
            # generate a fresh one (this covers malformed files).
        except (json.JSONDecodeError, OSError):
            # Malformed file — regenerate.
            pass

    # --- Generate a fresh identity ---
    peer_id = str(uuid.uuid4())[:8]
    _atomic_write(identity_path, {"peer_id": peer_id})
    return peer_id


def _atomic_write(path: str, data: dict) -> None:
    """Write *data* as JSON to *path* atomically."""
    directory = os.path.dirname(path) or "."
    # Write to a temp file in the same directory so that os.replace is atomic
    # on both POSIX and Windows (same filesystem, no cross-device move).
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
