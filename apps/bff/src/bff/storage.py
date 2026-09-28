"""SQLite persistence of the BFF: sessions, visits, conversations and events.

Durable memory stays in the waiter's own store. Every write runs inside one
``BEGIN IMMEDIATE`` transaction so state, events and command results are
persisted together before anything is published.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from restaurant_contracts.application import (
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    ChatMessage,
    CommandResult,
    StreamEvent,
)
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS demo_sessions (
    token_hash TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    presented_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visits (
    visit_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    presented_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_actor ON visits(actor_id, position DESC);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    visit_id TEXT NOT NULL UNIQUE REFERENCES visits(visit_id),
    actor_id TEXT NOT NULL,
    presented_name TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT 0,
    process_status TEXT NOT NULL CHECK(process_status IN ('idle', 'processing')),
    pending_event_id TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    customer_json TEXT NOT NULL,
    order_draft_json TEXT NOT NULL,
    persisted_order_preferences_json TEXT NOT NULL,
    agent_session_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    position INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    UNIQUE(conversation_id, position)
);
CREATE TABLE IF NOT EXISTS stream_events (
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    cursor INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL,
    PRIMARY KEY(conversation_id, cursor)
);
CREATE TABLE IF NOT EXISTS command_results (
    actor_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    conversation_id TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(actor_id, event_id)
);
CREATE TABLE IF NOT EXISTS memory_aliases (
    actor_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    PRIMARY KEY(actor_id, alias),
    UNIQUE(actor_id, memory_id)
);
CREATE TABLE IF NOT EXISTS memory_alias_counters (
    actor_id TEXT PRIMARY KEY,
    last_number INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class SessionRow:
    actor_id: str
    presented_name: str
    expires_at: datetime


@dataclass(frozen=True)
class VisitRow:
    visit_id: str
    actor_id: str
    presented_name: str


@dataclass
class ConversationRow:
    conversation_id: str
    visit_id: str
    actor_id: str
    presented_name: str
    created_at: datetime
    updated_at: datetime
    cursor: int = 0
    process_status: str = "idle"
    pending_event_id: str | None = None
    turn_count: int = 0
    customer: CustomerSnapshot = field(default_factory=CustomerSnapshot)
    order_draft: OrderDraft = field(default_factory=OrderDraft)
    persisted_order_preferences: list[str] = field(default_factory=list)
    agent_session_json: str | None = None


@dataclass(frozen=True)
class StoredResult:
    fingerprint: str
    correlation_id: str
    conversation_id: str | None
    result: CommandResult


class Transaction:
    """Operations on one open connection; the caller owns the transaction."""

    def __init__(self, connection: sqlite3.Connection, retention: int) -> None:
        self._connection = connection
        self._retention = retention

    # Sessions

    def insert_session(
        self,
        *,
        token_hash: str,
        actor_id: str,
        presented_name: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO demo_sessions VALUES (?, ?, ?, ?, ?)",
            (
                token_hash,
                actor_id,
                presented_name,
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )

    def get_session(self, token_hash: str) -> SessionRow | None:
        row = self._connection.execute(
            "SELECT actor_id, presented_name, expires_at FROM demo_sessions "
            "WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        return SessionRow(
            actor_id=row["actor_id"],
            presented_name=row["presented_name"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    # Visits

    def insert_visit(
        self, *, visit_id: str, actor_id: str, presented_name: str, created_at: datetime
    ) -> None:
        position = self._connection.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM visits"
        ).fetchone()[0]
        self._connection.execute(
            "INSERT INTO visits VALUES (?, ?, ?, ?, ?)",
            (visit_id, actor_id, presented_name, created_at.isoformat(), position),
        )

    def get_visit(self, visit_id: str) -> VisitRow | None:
        row = self._connection.execute(
            "SELECT visit_id, actor_id, presented_name FROM visits WHERE visit_id = ?",
            (visit_id,),
        ).fetchone()
        return VisitRow(**dict(row)) if row else None

    def latest_visit_id(self, actor_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT visit_id FROM visits WHERE actor_id = ? "
            "ORDER BY position DESC LIMIT 1",
            (actor_id,),
        ).fetchone()
        return row["visit_id"] if row else None

    # Conversations

    def insert_conversation(self, conversation: ConversationRow) -> None:
        self._connection.execute(
            """
            INSERT INTO conversations(
                conversation_id, visit_id, actor_id, presented_name, cursor,
                process_status, pending_event_id, turn_count, customer_json,
                order_draft_json, persisted_order_preferences_json,
                agent_session_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation.conversation_id,
                conversation.visit_id,
                conversation.actor_id,
                conversation.presented_name,
                *self._mutable_columns(conversation),
                conversation.created_at.isoformat(),
                conversation.updated_at.isoformat(),
            ),
        )

    def update_conversation(self, conversation: ConversationRow) -> None:
        self._connection.execute(
            """
            UPDATE conversations SET
                cursor = ?, process_status = ?, pending_event_id = ?,
                turn_count = ?, customer_json = ?, order_draft_json = ?,
                persisted_order_preferences_json = ?, agent_session_json = ?,
                updated_at = ?
            WHERE conversation_id = ?
            """,
            (
                *self._mutable_columns(conversation),
                conversation.updated_at.isoformat(),
                conversation.conversation_id,
            ),
        )

    def get_conversation(self, conversation_id: str) -> ConversationRow | None:
        row = self._connection.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return self._conversation_from_row(row) if row else None

    def get_conversation_by_visit(self, visit_id: str) -> ConversationRow | None:
        row = self._connection.execute(
            "SELECT * FROM conversations WHERE visit_id = ?", (visit_id,)
        ).fetchone()
        return self._conversation_from_row(row) if row else None

    def processing_conversations(self) -> list[ConversationRow]:
        rows = self._connection.execute(
            "SELECT * FROM conversations WHERE process_status = 'processing'"
        ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    # Messages

    def add_message(self, conversation_id: str, message: ChatMessage) -> None:
        position = self._connection.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM messages "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        self._connection.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            (
                message.message_id,
                conversation_id,
                position,
                message.model_dump_json(),
            ),
        )

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        rows = self._connection.execute(
            "SELECT message_json FROM messages WHERE conversation_id = ? "
            "ORDER BY position",
            (conversation_id,),
        ).fetchall()
        return [ChatMessage.model_validate_json(row["message_json"]) for row in rows]

    # Stream events

    def append_event(
        self, conversation: ConversationRow, build: Callable[[int], StreamEvent]
    ) -> int:
        """Persist the next event of the conversation and advance its cursor."""

        cursor = conversation.cursor + 1
        event = build(cursor)
        self._connection.execute(
            "INSERT INTO stream_events VALUES (?, ?, ?, ?)",
            (
                conversation.conversation_id,
                cursor,
                event.event_id,
                STREAM_EVENT_ADAPTER.dump_json(event).decode(),
            ),
        )
        self._connection.execute(
            "UPDATE conversations SET cursor = ? WHERE conversation_id = ?",
            (cursor, conversation.conversation_id),
        )
        self._connection.execute(
            "DELETE FROM stream_events WHERE conversation_id = ? AND cursor <= ?",
            (conversation.conversation_id, cursor - self._retention),
        )
        conversation.cursor = cursor
        return cursor

    def events_after(
        self, conversation_id: str, cursor: int, *, limit: int = 100
    ) -> list[tuple[int, str]]:
        rows = self._connection.execute(
            "SELECT cursor, event_json FROM stream_events "
            "WHERE conversation_id = ? AND cursor > ? ORDER BY cursor LIMIT ?",
            (conversation_id, cursor, limit),
        ).fetchall()
        return [(row["cursor"], row["event_json"]) for row in rows]

    def oldest_cursor(self, conversation_id: str) -> int | None:
        return self._connection.execute(
            "SELECT MIN(cursor) FROM stream_events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]

    # Command results

    def get_result(self, actor_id: str, event_id: str) -> StoredResult | None:
        row = self._connection.execute(
            "SELECT fingerprint, correlation_id, conversation_id, result_json "
            "FROM command_results WHERE actor_id = ? AND event_id = ?",
            (actor_id, event_id),
        ).fetchone()
        if row is None:
            return None
        return StoredResult(
            fingerprint=row["fingerprint"],
            correlation_id=row["correlation_id"],
            conversation_id=row["conversation_id"],
            result=COMMAND_RESULT_ADAPTER.validate_json(row["result_json"]),
        )

    def save_result(
        self,
        *,
        actor_id: str,
        fingerprint: str,
        conversation_id: str | None,
        result: CommandResult,
        now: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO command_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(actor_id, event_id) DO UPDATE SET
                result_json = excluded.result_json,
                conversation_id = excluded.conversation_id,
                updated_at = excluded.updated_at
            """,
            (
                actor_id,
                result.event_id,
                fingerprint,
                result.correlation_id,
                conversation_id,
                COMMAND_RESULT_ADAPTER.dump_json(result).decode(),
                now.isoformat(),
                now.isoformat(),
            ),
        )

    def update_result(self, actor_id: str, result: CommandResult, now: datetime) -> None:
        self._connection.execute(
            "UPDATE command_results SET result_json = ?, updated_at = ? "
            "WHERE actor_id = ? AND event_id = ?",
            (
                COMMAND_RESULT_ADAPTER.dump_json(result).decode(),
                now.isoformat(),
                actor_id,
                result.event_id,
            ),
        )

    # Short, typeable memory ids

    def memory_aliases(self, actor_id: str, memory_ids: list[str]) -> dict[str, str]:
        """Return alias by memory id, assigning never-reused aliases to new ones."""

        rows = self._connection.execute(
            "SELECT alias, memory_id FROM memory_aliases WHERE actor_id = ?",
            (actor_id,),
        ).fetchall()
        aliases = {row["memory_id"]: row["alias"] for row in rows}
        missing = [memory_id for memory_id in memory_ids if memory_id not in aliases]
        if not missing:
            return aliases
        counter = self._connection.execute(
            "SELECT last_number FROM memory_alias_counters WHERE actor_id = ?",
            (actor_id,),
        ).fetchone()
        number = counter["last_number"] if counter else 0
        for memory_id in missing:
            number += 1
            alias = f"m{number}"
            self._connection.execute(
                "INSERT INTO memory_aliases VALUES (?, ?, ?)",
                (actor_id, alias, memory_id),
            )
            aliases[memory_id] = alias
        self._connection.execute(
            """
            INSERT INTO memory_alias_counters VALUES (?, ?)
            ON CONFLICT(actor_id) DO UPDATE SET last_number = excluded.last_number
            """,
            (actor_id, number),
        )
        return aliases

    def memory_for_alias(self, actor_id: str, alias: str) -> str | None:
        row = self._connection.execute(
            "SELECT memory_id FROM memory_aliases WHERE actor_id = ? AND alias = ?",
            (actor_id, alias.casefold()),
        ).fetchone()
        return row["memory_id"] if row else None

    @staticmethod
    def _mutable_columns(conversation: ConversationRow) -> tuple[object, ...]:
        return (
            conversation.cursor,
            conversation.process_status,
            conversation.pending_event_id,
            conversation.turn_count,
            conversation.customer.model_dump_json(),
            conversation.order_draft.model_dump_json(),
            json.dumps(conversation.persisted_order_preferences, ensure_ascii=False),
            conversation.agent_session_json,
        )

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> ConversationRow:
        return ConversationRow(
            conversation_id=row["conversation_id"],
            visit_id=row["visit_id"],
            actor_id=row["actor_id"],
            presented_name=row["presented_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            cursor=row["cursor"],
            process_status=row["process_status"],
            pending_event_id=row["pending_event_id"],
            turn_count=row["turn_count"],
            customer=CustomerSnapshot.model_validate_json(row["customer_json"]),
            order_draft=OrderDraft.model_validate_json(row["order_draft_json"]),
            persisted_order_preferences=json.loads(
                row["persisted_order_preferences_json"]
            ),
            agent_session_json=row["agent_session_json"],
        )


class Database:
    """SQLite file owned by one BFF process."""

    def __init__(self, path: Path, *, event_retention: int = 500) -> None:
        if event_retention < 1:
            raise ValueError("event_retention must be positive")
        self._path = path.expanduser().resolve()
        self._retention = event_retention
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            if connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
                connection.execute(
                    "INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,)
                )

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def write(self) -> Iterator[Transaction]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield Transaction(connection, self._retention)
            except BaseException:
                connection.rollback()
                raise
            connection.commit()

    @contextmanager
    def read(self) -> Iterator[Transaction]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                yield Transaction(connection, self._retention)
            finally:
                connection.rollback()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        finally:
            connection.close()
