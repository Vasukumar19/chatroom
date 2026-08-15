"""Message protocol: envelope model, serialization, validation."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import uuid
from typing import Any, Dict, Optional


SUPPORTED_TYPES = {
    'chat_message',
    'handshake',
    'peer_announcement',
    'ack',
    'data',
    'sync_request',
    'sync_response'
}

# Heartbeat types used by Phase 4
SUPPORTED_TYPES.update({'heartbeat', 'heartbeat_ack'})
# Route advertisement for Phase 5.5
SUPPORTED_TYPES.update({'route_advertisement'})


@dataclass
class Envelope:
    message_id: str
    type: str
    source: str
    destination: Optional[str]
    sequence: Optional[int]
    timestamp: str
    ttl: int
    priority: int
    payload: Dict[str, Any]
    protocol_version: str = "1"


def _now_iso() -> str:
    # Use timezone-aware UTC timestamps and keep 'Z' suffix for compatibility
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def create_envelope(
    msg_type: str,
    source: str,
    payload: Dict[str, Any],
    destination: Optional[str] = None,
    sequence: Optional[int] = None,
    ttl: int = 8,
    priority: int = 0,
    protocol_version: str = "1",
) -> Dict[str, Any]:
    if msg_type not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported message type: {msg_type}")
    if not isinstance(ttl, int) or ttl < 0:
        raise ValueError("TTL must be a non-negative integer")
    if not isinstance(priority, int) or priority < 0:
        raise ValueError("Priority must be non-negative integer")

    env = Envelope(
        message_id=str(uuid.uuid4()),
        type=msg_type,
        source=source,
        destination=destination,
        sequence=sequence,
        timestamp=_now_iso(),
        ttl=ttl,
        priority=priority,
        payload=payload,
        protocol_version=protocol_version,
    )
    return asdict(env)


def serialize_envelope(env: Dict[str, Any]) -> str:
    return json.dumps(env)


def deserialize_envelope(raw: str) -> Dict[str, Any]:
    data = json.loads(raw)
    validate_envelope(data)
    return data


def validate_envelope(env: Dict[str, Any]) -> bool:
    """Validate the envelope; raises ValueError on invalid data.

    Backwards compatibility: if `protocol_version` is missing, accept but
    perform lightweight checks to ensure required fields exist.
    """
    if not isinstance(env, dict):
        raise ValueError("Envelope must be a dict")

    # Required keys for versioned envelope
    required = {'message_id', 'type', 'source', 'timestamp', 'ttl', 'payload'}
    if not required.issubset(set(env.keys())):
        # Backwards compatibility: allow older ad-hoc messages that at least
        # have 'type' and 'data' or 'payload'
        if 'type' not in env:
            raise ValueError('Missing required field: type')
        if 'data' not in env and 'payload' not in env:
            raise ValueError('Missing payload/data in message')
        return True

    msg_type = env.get('type')
    if msg_type not in SUPPORTED_TYPES:
        raise ValueError(f'Unsupported message type: {msg_type}')

    ttl = env.get('ttl')
    if not isinstance(ttl, int) or ttl < 0:
        raise ValueError('Invalid TTL')

    priority = env.get('priority', 0)
    if not isinstance(priority, int) or priority < 0:
        raise ValueError('Invalid priority')

    # message_id must be present
    if not env.get('message_id'):
        raise ValueError('Missing message_id')

    return True


def wrap_legacy_message(message: Dict[str, Any], source: str, room: Optional[str] = None) -> Dict[str, Any]:
    """Wrap an existing legacy message dict into a versioned envelope.

    This helps migration: older messages that used top-level 'type' and 'data'
    can be wrapped into the new envelope without changing transport.
    """
    # If already looks like a versioned envelope (has message_id and protocol_version),
    # validate and return as-is.
    if isinstance(message, dict) and message.get('message_id') and message.get('protocol_version'):
        validate_envelope(message)
        return message

    msg_type = message.get('type')
    payload = message.get('data') if 'data' in message else message.get('payload', {})

    env = create_envelope(
        msg_type=msg_type,
        source=source,
        payload=payload or {},
        destination=None,
        ttl=message.get('ttl', 8),
        priority=message.get('priority', 0),
    )
    # preserve legacy room field if present
    if room:
        env['payload']['room'] = room
    return env
