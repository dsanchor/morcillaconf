"""SQLite adapter for automatic, identity-scoped durable memory."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Protocol
from uuid import uuid4

from restaurant_agent.memory.contracts import (
    CompletedOrderHistory,
    DurableMemoryRecord,
    MemoryKind,
    MemorySnapshot,
)


class DurableMemoryError(RuntimeError):
    """Base error for durable memory operations."""


class MemoryNotFoundError(DurableMemoryError):
    """Raised when a memory does not belong to the requested identity."""


class MemoryConflictError(DurableMemoryError):
    """Raised when a correction would duplicate another preference."""


class DurableMemoryRepository(Protocol):
    """Persistence boundary used by the conversation and context layers."""

    def remember_memory(
        self,
        actor_id: str,
        *,
        kind: MemoryKind,
        value: str,
        source_conversation_id: str,
    ) -> DurableMemoryRecord: ...

    def list_memories(self, actor_id: str) -> list[DurableMemoryRecord]: ...

    def correct_memory(
        self,
        actor_id: str,
        *,
        preference_id: str,
        value: str,
    ) -> DurableMemoryRecord: ...

    def delete_memory(self, actor_id: str, *, preference_id: str) -> None: ...

    def delete_memories(
        self,
        actor_id: str,
        *,
        preference_ids: list[str],
    ) -> int: ...

    def delete_all_memories(self, actor_id: str) -> int: ...

    def snapshot(self, actor_id: str) -> MemorySnapshot: ...


class SQLiteMemoryStore:
    """Persist bounded, non-binding memories in SQLite."""

    def __init__(
        self,
        database_path: Path,
        *,
        max_memories: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_memories < 1:
            raise ValueError("max_memories must be at least 1")
        self._database_path = database_path.expanduser().resolve()
        self._max_memories = max_memories
        self._clock = clock or (lambda: datetime.now(UTC))
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def remember_memory(
        self,
        actor_id: str,
        *,
        kind: MemoryKind,
        value: str,
        source_conversation_id: str,
    ) -> DurableMemoryRecord:
        actor = self._normalize_required(actor_id, "actor_id")
        memory_kind = MemoryKind(kind)
        preference_value = self._normalize_required(value, "value", max_length=200)
        source_id = self._normalize_required(
            source_conversation_id,
            "source_conversation_id",
        )
        normalized_value = preference_value.casefold()
        now = self._clock()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT preference_id, created_at, occurrence_count
                FROM durable_memories
                WHERE actor_id = ? AND kind = ? AND normalized_value = ?
                """,
                (actor, memory_kind.value, normalized_value),
            ).fetchone()
            if existing:
                preference_id = existing["preference_id"]
                created_at = datetime.fromisoformat(existing["created_at"])
                occurrence_count = existing["occurrence_count"] + 1
                connection.execute(
                    """
                    UPDATE durable_memories
                    SET normalized_value = ?, value = ?,
                        source_conversation_id = ?, updated_at = ?,
                        occurrence_count = ?
                    WHERE preference_id = ?
                    """,
                    (
                        normalized_value,
                        preference_value,
                        source_id,
                        now.isoformat(),
                        occurrence_count,
                        preference_id,
                    ),
                )
            else:
                preference_id = f"pref_{uuid4().hex}"
                created_at = now
                occurrence_count = 1
                connection.execute(
                    """
                    INSERT INTO durable_memories(
                        preference_id,
                        actor_id,
                        kind,
                        normalized_value,
                        value,
                        source_conversation_id,
                        created_at,
                        updated_at,
                        occurrence_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preference_id,
                        actor,
                        memory_kind.value,
                        normalized_value,
                        preference_value,
                        source_id,
                        now.isoformat(),
                        now.isoformat(),
                        occurrence_count,
                    ),
                )
            self._trim_memories(connection, actor, memory_kind)

        return DurableMemoryRecord(
            preference_id=preference_id,
            actor_id=actor,
            kind=memory_kind,
            value=preference_value,
            occurrence_count=occurrence_count,
            source_conversation_id=source_id,
            created_at=created_at,
            updated_at=now,
        )

    def list_memories(self, actor_id: str) -> list[DurableMemoryRecord]:
        actor = self._normalize_required(actor_id, "actor_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT preference_id, actor_id, kind, value, occurrence_count,
                       source_conversation_id, created_at, updated_at
                FROM durable_memories
                WHERE actor_id = ?
                ORDER BY updated_at DESC, preference_id ASC
                """,
                (actor,),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def correct_memory(
        self,
        actor_id: str,
        *,
        preference_id: str,
        value: str,
    ) -> DurableMemoryRecord:
        actor = self._normalize_required(actor_id, "actor_id")
        selected_id = self._normalize_required(preference_id, "preference_id")
        preference_value = self._normalize_required(value, "value", max_length=200)
        now = self._clock()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT kind, occurrence_count, source_conversation_id, created_at
                FROM durable_memories
                WHERE preference_id = ? AND actor_id = ?
                """,
                (selected_id, actor),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(f"Unknown preference: {selected_id}")
            try:
                connection.execute(
                    """
                    UPDATE durable_memories
                    SET normalized_value = ?, value = ?, updated_at = ?
                    WHERE preference_id = ? AND actor_id = ?
                    """,
                    (
                        preference_value.casefold(),
                        preference_value,
                        now.isoformat(),
                        selected_id,
                        actor,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise MemoryConflictError(
                    "Another preference already has that value"
                ) from exc

        return DurableMemoryRecord(
            preference_id=selected_id,
            actor_id=actor,
            kind=MemoryKind(row["kind"]),
            value=preference_value,
            occurrence_count=row["occurrence_count"],
            source_conversation_id=row["source_conversation_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=now,
        )

    def delete_memory(self, actor_id: str, *, preference_id: str) -> None:
        self.delete_memories(
            actor_id,
            preference_ids=[preference_id],
        )

    def delete_memories(
        self,
        actor_id: str,
        *,
        preference_ids: list[str],
    ) -> int:
        actor = self._normalize_required(actor_id, "actor_id")
        selected_ids = list(
            dict.fromkeys(
                self._normalize_required(item, "preference_id")
                for item in preference_ids
            )
        )
        if not selected_ids:
            raise ValueError("preference_ids cannot be empty")
        placeholders = ", ".join("?" for _ in selected_ids)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"""
                SELECT preference_id
                FROM durable_memories
                WHERE actor_id = ? AND preference_id IN ({placeholders})
                """,
                (actor, *selected_ids),
            ).fetchall()
            found_ids = {row["preference_id"] for row in rows}
            missing_ids = [
                memory_id
                for memory_id in selected_ids
                if memory_id not in found_ids
            ]
            if missing_ids:
                missing = ", ".join(missing_ids)
                raise MemoryNotFoundError(f"Unknown preferences: {missing}")
            cursor = connection.execute(
                f"""
                DELETE FROM durable_memories
                WHERE actor_id = ? AND preference_id IN ({placeholders})
                """,
                (actor, *selected_ids),
            )
        return cursor.rowcount

    def delete_all_memories(self, actor_id: str) -> int:
        actor = self._normalize_required(actor_id, "actor_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM durable_memories WHERE actor_id = ?",
                (actor,),
            )
        return cursor.rowcount

    def snapshot(self, actor_id: str) -> MemorySnapshot:
        actor = self._normalize_required(actor_id, "actor_id")
        return MemorySnapshot(
            memories=self.list_memories(actor),
            order_history=self._list_completed_orders(actor),
        )

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            memory_schema = """(
                    preference_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('preference', 'restriction')),
                    normalized_value TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL DEFAULT 1
                        CHECK(occurrence_count >= 1),
                    UNIQUE(actor_id, kind, normalized_value)
                )"""
            history_schema = """(
                    order_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )"""
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS durable_memories {memory_schema}"
            )
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS completed_order_history {history_schema}"
            )
            memory_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(durable_memories)"
                ).fetchall()
            }
            if "occurrence_count" not in memory_columns:
                connection.execute(
                    """
                    ALTER TABLE durable_memories
                    ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1
                    CHECK(occurrence_count >= 1)
                    """
                )
            # Rebuild legacy tables without the consent foreign key, preserving data.
            for table, schema, columns in (
                (
                    "durable_memories",
                    memory_schema,
                    "preference_id, actor_id, kind, normalized_value, value, "
                    "source_conversation_id, created_at, updated_at, occurrence_count",
                ),
                (
                    "completed_order_history",
                    history_schema,
                    "order_id, actor_id, items_json, completed_at",
                ),
            ):
                foreign_keys = connection.execute(
                    f"PRAGMA foreign_key_list({table})"
                ).fetchall()
                if any(row["table"] == "memory_consents" for row in foreign_keys):
                    connection.execute(f"CREATE TABLE {table}_automatic {schema}")
                    connection.execute(
                        f"INSERT INTO {table}_automatic ({columns}) "
                        f"SELECT {columns} FROM {table}"
                    )
                    connection.execute(f"DROP TABLE {table}")
                    connection.execute(f"ALTER TABLE {table}_automatic RENAME TO {table}")
            legacy_table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'preference_memories'
                """
            ).fetchone()
            if legacy_table:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO durable_memories(
                        preference_id,
                        actor_id,
                        kind,
                        normalized_value,
                        value,
                        source_conversation_id,
                        created_at,
                        updated_at
                    )
                    SELECT preference_id,
                           actor_id,
                           'preference',
                           normalized_value,
                           value,
                           source_conversation_id,
                           created_at,
                           updated_at
                    FROM preference_memories
                    """
                )
                connection.execute("DROP TABLE preference_memories")
            connection.execute("DROP TABLE IF EXISTS memory_consents")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_actor_updated "
                "ON durable_memories(actor_id, updated_at DESC)"
            )
            actors = connection.execute(
                "SELECT DISTINCT actor_id FROM durable_memories"
            ).fetchall()
            for row in actors:
                actor_id = row["actor_id"]
                for memory_kind in MemoryKind:
                    self._trim_memories(connection, actor_id, memory_kind)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _trim_memories(
        self,
        connection: sqlite3.Connection,
        actor_id: str,
        kind: MemoryKind,
    ) -> None:
        connection.execute(
            """
            DELETE FROM durable_memories
            WHERE preference_id IN (
                SELECT preference_id
                FROM durable_memories
                WHERE actor_id = ? AND kind = ?
                ORDER BY updated_at DESC, preference_id ASC
                LIMIT -1 OFFSET ?
            )
            """,
            (actor_id, kind.value, self._max_memories),
        )

    def _list_completed_orders(self, actor_id: str) -> list[CompletedOrderHistory]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT order_id, actor_id, items_json, completed_at
                FROM completed_order_history
                WHERE actor_id = ?
                ORDER BY completed_at DESC
                """,
                (actor_id,),
            ).fetchall()
        return [
            CompletedOrderHistory(
                order_id=row["order_id"],
                actor_id=row["actor_id"],
                items=json.loads(row["items_json"]),
                completed_at=datetime.fromisoformat(row["completed_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _normalize_required(
        value: str,
        field_name: str,
        *,
        max_length: int = 200,
    ) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty")
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} cannot exceed {max_length} characters")
        return normalized

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> DurableMemoryRecord:
        return DurableMemoryRecord(
            preference_id=row["preference_id"],
            actor_id=row["actor_id"],
            kind=MemoryKind(row["kind"]),
            value=row["value"],
            occurrence_count=row["occurrence_count"],
            source_conversation_id=row["source_conversation_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
