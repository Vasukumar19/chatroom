"""Small, end-to-end envelope security layer for DisasterConnect.

The implementation uses established primitives from ``cryptography``:
AES-GCM protects payload confidentiality/integrity and Ed25519 signs the
authenticated envelope.  Key distribution is deliberately outside this
student-project scope: peers are provisioned with one shared AES key and a
trusted map of Ed25519 public keys.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from copy import deepcopy
from typing import Dict, Mapping, Optional

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecurityError(ValueError):
    """Raised when an envelope cannot be authenticated, decrypted, or replayed."""


def generate_aes_key() -> bytes:
    """Generate a 256-bit AES-GCM key for out-of-band peer provisioning."""
    return AESGCM.generate_key(bit_length=256)


class SecurityContext:
    """Protect and open end-to-end message envelopes.

    ``trusted_public_keys`` maps node IDs to raw Ed25519 public-key bytes.
    The same symmetric key must be provisioned at the communicating endpoints.
    Intermediate routers only forward the encrypted ``payload`` unchanged.
    """

    def __init__(
        self,
        node_id: str,
        encryption_key: bytes,
        *,
        private_key: Optional[Ed25519PrivateKey] = None,
        trusted_public_keys: Optional[Mapping[str, bytes]] = None,
    ):
        if len(encryption_key) not in (16, 24, 32):
            raise ValueError('AES-GCM key must be 128, 192, or 256 bits')
        self.node_id = str(node_id)
        self._cipher = AESGCM(encryption_key)
        self._private_key = private_key or Ed25519PrivateKey.generate()
        self._trusted_public_keys: Dict[str, Ed25519PublicKey] = {}
        self._seen_message_ids = set()
        self._lock = threading.Lock()
        for peer, public_key in (trusted_public_keys or {}).items():
            self.trust_peer(peer, public_key)

    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def trust_peer(self, node_id: str, public_key: bytes) -> None:
        self._trusted_public_keys[str(node_id)] = Ed25519PublicKey.from_public_bytes(public_key)

    @staticmethod
    def _signed_bytes(envelope: dict, nonce: bytes, ciphertext: bytes) -> bytes:
        # TTL and hop count are deliberately excluded: routers may mutate them.
        signed = {
            'destination': envelope.get('destination'),
            'message_id': envelope.get('message_id'),
            'nonce': base64.b64encode(nonce).decode('ascii'),
            'source': envelope.get('source'),
            'type': envelope.get('type'),
            'ciphertext': base64.b64encode(ciphertext).decode('ascii'),
        }
        return json.dumps(signed, sort_keys=True, separators=(',', ':')).encode('utf-8')

    def protect(self, envelope: dict) -> dict:
        """Encrypt its payload, then sign immutable routing metadata + ciphertext."""
        if envelope.get('source') != self.node_id:
            raise SecurityError('Cannot sign an envelope for another source node')
        protected = deepcopy(envelope)
        nonce, ciphertext = self.encrypt_payload(protected.get('payload', {}))
        signature = self.sign_envelope(protected, nonce, ciphertext)
        protected['payload'] = {
            'security': {
                'algorithm': 'AES-256-GCM+Ed25519',
                'nonce': base64.b64encode(nonce).decode('ascii'),
                'ciphertext': base64.b64encode(ciphertext).decode('ascii'),
                'signature': base64.b64encode(signature).decode('ascii'),
            }
        }
        return protected

    def encrypt_payload(self, payload: dict):
        """Encrypt a JSON application payload; exposed for operation benchmarks."""
        plaintext = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        nonce = os.urandom(12)  # Fresh AES-GCM nonce per encryption.
        return nonce, self._cipher.encrypt(nonce, plaintext, None)

    def decrypt_payload(self, nonce: bytes, ciphertext: bytes) -> dict:
        """Decrypt a payload produced by :meth:`encrypt_payload`."""
        return json.loads(self._cipher.decrypt(nonce, ciphertext, None).decode('utf-8'))

    def sign_envelope(self, envelope: dict, nonce: bytes, ciphertext: bytes) -> bytes:
        """Sign immutable routing metadata and ciphertext with this node identity."""
        if envelope.get('source') != self.node_id:
            raise SecurityError('Cannot sign an envelope for another source node')
        return self._private_key.sign(self._signed_bytes(envelope, nonce, ciphertext))

    def verify_envelope(self, envelope: dict, nonce: bytes, ciphertext: bytes, signature: bytes) -> None:
        """Verify the claimed source identity; raises SecurityError on failure."""
        try:
            self._trusted_public_keys[envelope['source']].verify(signature, self._signed_bytes(envelope, nonce, ciphertext))
        except (KeyError, InvalidSignature) as exc:
            raise SecurityError('Secure envelope signature is invalid or untrusted') from exc

    def open(self, envelope: dict, *, reject_replay: bool = True) -> dict:
        """Verify the sender, decrypt the payload, and reject duplicate message IDs."""
        try:
            secure = envelope['payload']['security']
            nonce = base64.b64decode(secure['nonce'], validate=True)
            ciphertext = base64.b64decode(secure['ciphertext'], validate=True)
            signature = base64.b64decode(secure['signature'], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise SecurityError('Malformed or untrusted secure envelope') from exc
        try:
            self.verify_envelope(envelope, nonce, ciphertext, signature)
            payload = self.decrypt_payload(nonce, ciphertext)
        except (InvalidSignature, InvalidTag, ValueError, json.JSONDecodeError) as exc:
            raise SecurityError('Secure envelope verification or decryption failed') from exc

        message_id = envelope.get('message_id')
        if reject_replay:
            with self._lock:
                if message_id in self._seen_message_ids:
                    raise SecurityError('Replay detected')
                self._seen_message_ids.add(message_id)
        opened = deepcopy(envelope)
        opened['payload'] = payload
        return opened
