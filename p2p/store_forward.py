from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class QueueFullError(RuntimeError):
    """Raised when a queue exceeds its configured capacity."""


@dataclass
class QueuedMessage:
    message_id: str
    source: str
    destination: str
    envelope: Dict[str, Any]
    created_at: str
    expires_at: Optional[str] = None
    retry_count: int = 0
    priority: int = 0
    state: str = "QUEUED"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_timestamp(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value.endswith("Z"):
        return value
    return value


class StoreForwardQueue:
    """Persistence-only queue for undelivered outbound messages."""

    def __init__(self, db_path: Optional[str] = None, *, max_messages: Optional[int] = None):
        self.db_path = db_path or ":memory:"
        self.max_messages = max_messages
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                envelope TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                last_attempt_at TEXT,
                delivered_at TEXT,
                error TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_pending
            ON messages(state, priority, created_at)
            """
        )
        self._conn.commit()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> QueuedMessage:
        return QueuedMessage(
            message_id=row["message_id"],
            source=row["source"],
            destination=row["destination"],
            envelope=json.loads(row["envelope"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            retry_count=row["retry_count"],
            priority=row["priority"],
            state=row["state"],
        )

    def enqueue(self, message: QueuedMessage) -> None:
        if self.max_messages is not None and self.pending_count() >= self.max_messages:
            raise QueueFullError(f"Queue capacity reached: {self.max_messages}")

        payload = json.dumps(message.envelope)
        try:
            self._conn.execute(
                """
                INSERT INTO messages (
                    message_id, source, destination, envelope,
                    created_at, expires_at, retry_count, priority, state,
                    last_attempt_at, delivered_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    message.message_id,
                    message.source,
                    message.destination,
                    payload,
                    _normalize_timestamp(message.created_at) or _utc_now_iso(),
                    _normalize_timestamp(message.expires_at),
                    int(message.retry_count),
                    int(message.priority),
                    message.state or "QUEUED",
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Duplicate message_id: {message.message_id}") from exc

    def get_pending(self, destination: Optional[str] = None, limit: int = 100) -> List[QueuedMessage]:
        query = "SELECT * FROM messages WHERE state IN ('QUEUED', 'REPLAYING')"
        params: List[Any] = []
        if destination is not None:
            query += " AND destination = ?"
            params.append(destination)
        query += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        params.append(int(limit))

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_message(row) for row in rows]

    def mark_replaying(self, message_id: str) -> None:
        self._conn.execute(
            "UPDATE messages SET state = 'REPLAYING', last_attempt_at = ? WHERE message_id = ?",
            (_utc_now_iso(), message_id),
        )
        self._conn.commit()

    def mark_delivered(self, message_id: str) -> None:
        self._conn.execute(
            "UPDATE messages SET state = 'DELIVERED', delivered_at = ?, error = NULL WHERE message_id = ?",
            (_utc_now_iso(), message_id),
        )
        self._conn.commit()

    def mark_failed(self, message_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE messages SET state = 'FAILED', error = ?, last_attempt_at = ? WHERE message_id = ?",
            (error, _utc_now_iso(), message_id),
        )
        self._conn.commit()

    def mark_expired(self, message_id: str) -> None:
        self._conn.execute(
            "UPDATE messages SET state = 'EXPIRED', error = 'expired', last_attempt_at = ? WHERE message_id = ?",
            (_utc_now_iso(), message_id),
        )
        self._conn.commit()

    def expire_messages(self) -> int:
        now = _utc_now_iso()
        rows = self._conn.execute(
            "SELECT message_id FROM messages WHERE state IN ('QUEUED', 'REPLAYING') AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            self.mark_expired(row["message_id"])
        return len(rows)

    def pending_count(self, destination: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) FROM messages WHERE state IN ('QUEUED', 'REPLAYING')"
        params: List[Any] = []
        if destination is not None:
            query += " AND destination = ?"
            params.append(destination)
        total = self._conn.execute(query, params).fetchone()[0]
        return int(total)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
