import sqlite3
from pathlib import Path

import pytest

from restaurant_agent.memory.contracts import MemoryKind
from restaurant_agent.memory.store import MemoryNotFoundError, SQLiteMemoryStore


def legacy_database(path: Path, *, legacy_preferences: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE memory_consents (
                actor_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                source TEXT NOT NULL, recorded_at TEXT NOT NULL
            );
            INSERT INTO memory_consents VALUES
                ('customer', 'granted', 'test', '2026-01-01T00:00:00+00:00'),
                ('forgotten', 'revoked', 'test', '2026-01-01T00:00:00+00:00');
            CREATE TABLE durable_memories (
                preference_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
                kind TEXT NOT NULL, normalized_value TEXT NOT NULL,
                value TEXT NOT NULL, source_conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(actor_id) REFERENCES memory_consents(actor_id) ON DELETE CASCADE,
                UNIQUE(actor_id, kind, normalized_value)
            );
            CREATE TABLE completed_order_history (
                order_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
                items_json TEXT NOT NULL, completed_at TEXT NOT NULL,
                FOREIGN KEY(actor_id) REFERENCES memory_consents(actor_id) ON DELETE CASCADE
            );
            CREATE INDEX idx_memories_actor_updated
                ON durable_memories(actor_id, updated_at DESC);
            INSERT INTO durable_memories VALUES (
                'pref_existing', 'customer', 'restriction', 'sin gluten', 'Sin gluten',
                'conv_previous', '2026-01-01T00:00:00+00:00',
                '2026-01-02T00:00:00+00:00', 7
            );
            INSERT INTO completed_order_history VALUES (
                'order_existing', 'customer', '[{"name":"agua","quantity":1}]',
                '2026-01-02T00:00:00+00:00'
            );
        """)
        if legacy_preferences:
            connection.executescript("""
                CREATE TABLE preference_memories (
                    preference_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
                    normalized_value TEXT NOT NULL, value TEXT NOT NULL,
                    source_conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(actor_id) REFERENCES memory_consents(actor_id) ON DELETE CASCADE
                );
                INSERT INTO preference_memories VALUES (
                    'pref_legacy', 'customer', 'agua con gas', 'agua con gas',
                    'conv_old', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                );
            """)


@pytest.mark.parametrize("legacy_preferences", [False, True])
def test_migration_preserves_data_and_removes_consent_dependency(
    tmp_path: Path, legacy_preferences: bool,
) -> None:
    path = tmp_path / "memory.db"
    legacy_database(path, legacy_preferences=legacy_preferences)
    store = SQLiteMemoryStore(path)
    snapshot = store.snapshot("customer")
    memory = next(item for item in snapshot.memories if item.preference_id == "pref_existing")
    assert memory.occurrence_count == 7
    assert memory.value == "Sin gluten"
    assert memory.kind is MemoryKind.RESTRICTION
    assert memory.source_conversation_id == "conv_previous"
    assert memory.created_at.isoformat() == "2026-01-01T00:00:00+00:00"
    assert memory.updated_at.isoformat() == "2026-01-02T00:00:00+00:00"
    assert memory.requires_reconfirmation
    assert len(snapshot.memories) == (2 if legacy_preferences else 1)
    assert snapshot.order_history[0].order_id == "order_existing"
    assert snapshot.order_history[0].items[0].name == "agua"
    assert store.snapshot("forgotten").memories == []
    assert store.snapshot("forgotten").order_history == []
    assert SQLiteMemoryStore(path).snapshot("customer") == snapshot
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "memory_consents" not in tables
        assert "preference_memories" not in tables
        assert connection.execute("PRAGMA foreign_key_list(durable_memories)").fetchall() == []
        assert connection.execute("PRAGMA foreign_key_list(completed_order_history)").fetchall() == []
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    for actor in ("new-customer", "forgotten"):
        record = store.remember_memory(
            actor, kind=MemoryKind.PREFERENCE, value="agua",
            source_conversation_id="conv_new",
        )
        assert store.list_memories(actor) == [record]
    with pytest.raises(MemoryNotFoundError):
        store.correct_memory("new-customer", preference_id="pref_existing", value="otra")
    with pytest.raises(MemoryNotFoundError):
        store.delete_memory("new-customer", preference_id="pref_existing")


def test_failed_migration_rolls_back_schema_and_data(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    legacy_database(path, legacy_preferences=True)
    with sqlite3.connect(path) as connection:
        before = list(connection.iterdump())

    class FailingMigration(SQLiteMemoryStore):
        def _trim_memories(self, connection, actor_id, kind) -> None:
            raise RuntimeError("simulated migration failure")

    with pytest.raises(RuntimeError, match="simulated migration failure"):
        FailingMigration(path)
    with sqlite3.connect(path) as connection:
        assert list(connection.iterdump()) == before
    assert len(SQLiteMemoryStore(path).list_memories("customer")) == 2
