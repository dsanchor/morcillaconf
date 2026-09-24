import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from restaurant_agent.memory.contracts import ConsentStatus, MemoryKind
from restaurant_agent.memory.store import ConsentRequiredError, SQLiteMemoryStore


def create_store(path: Path, *, max_memories: int = 20) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(path, max_memories=max_memories)


def test_memory_requires_consent(tmp_path: Path) -> None:
    store = create_store(tmp_path / "memory.db")

    with pytest.raises(ConsentRequiredError):
        store.remember_memory(
            "customer-1",
            kind=MemoryKind.PREFERENCE,
            value="agua con gas",
            source_conversation_id="conv-1",
        )

    assert store.get_consent("customer-1") is None
    assert store.list_memories("customer-1") == []


def test_preferences_and_restrictions_survive_recreation_and_are_isolated(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.db"
    first_store = create_store(database_path)
    consent = first_store.grant_consent("customer-1", source="test")
    first_store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="agua con gas",
        source_conversation_id="conv-1",
    )
    first_store.remember_memory(
        "customer-1",
        kind=MemoryKind.RESTRICTION,
        value="alergia a frutos secos",
        source_conversation_id="conv-1",
    )

    recreated_store = create_store(database_path)
    memories = recreated_store.list_memories("customer-1")

    assert consent.includes_sensitive_restrictions is True
    assert {(item.kind, item.value) for item in memories} == {
        (MemoryKind.PREFERENCE, "agua con gas"),
        (MemoryKind.RESTRICTION, "alergia a frutos secos"),
    }
    assert all(item.requires_reconfirmation for item in memories)
    assert recreated_store.list_memories("customer-2") == []


def test_correction_deletion_and_revocation_are_persistent(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.db"
    store = create_store(database_path)
    store.grant_consent("customer-1", source="test")
    memory = store.remember_memory(
        "customer-1",
        kind=MemoryKind.RESTRICTION,
        value="alergia a frutos secos",
        source_conversation_id="conv-1",
    )

    corrected = store.correct_memory(
        "customer-1",
        preference_id=memory.preference_id,
        value="alergia al marisco",
    )
    assert corrected.kind is MemoryKind.RESTRICTION
    assert corrected.value == "alergia al marisco"

    store.delete_memory("customer-1", preference_id=memory.preference_id)
    assert store.list_memories("customer-1") == []

    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="mesa tranquila",
        source_conversation_id="conv-1",
    )
    revoked = store.revoke_consent("customer-1", source="test")

    assert revoked.status is ConsentStatus.REVOKED
    assert store.list_memories("customer-1") == []
    with pytest.raises(ConsentRequiredError):
        store.remember_memory(
            "customer-1",
            kind=MemoryKind.PREFERENCE,
            value="cerca de la ventana",
            source_conversation_id="conv-2",
        )

    recreated_store = create_store(database_path)
    assert recreated_store.snapshot("customer-1").memories == []
    assert recreated_store.get_consent("customer-1").status is ConsentStatus.REVOKED


def test_store_keeps_only_the_configured_number_of_memories(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path / "memory.db", max_memories=2)
    store.grant_consent("customer-1", source="test")

    for value in ("primera", "segunda", "tercera"):
        store.remember_memory(
            "customer-1",
            kind=MemoryKind.PREFERENCE,
            value=value,
            source_conversation_id="conv-1",
        )

    assert len(store.list_memories("customer-1")) == 2


def test_memory_limit_is_applied_independently_by_category(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path / "memory.db", max_memories=2)
    store.grant_consent("customer-1", source="test")

    for kind in MemoryKind:
        for index in range(3):
            store.remember_memory(
                "customer-1",
                kind=kind,
                value=f"{kind.value} {index}",
                source_conversation_id="conv-1",
            )

    memories = store.list_memories("customer-1")
    assert sum(item.kind is MemoryKind.PREFERENCE for item in memories) == 2
    assert sum(item.kind is MemoryKind.RESTRICTION for item in memories) == 2


def test_latest_order_preference_replaces_previous_summary(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path / "memory.db")
    store.grant_consent("customer-1", source="test")
    first = store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="Preferencia de pedido: tortilla, agua",
        source_conversation_id="conv-1",
    )
    latest = store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="Preferencia de pedido: ensalada, café",
        source_conversation_id="conv-2",
    )

    order_memories = [
        memory
        for memory in store.list_memories("customer-1")
        if memory.value.startswith("Preferencia de pedido: ")
    ]
    assert len(order_memories) == 1
    assert order_memories[0].preference_id == first.preference_id
    assert latest.preference_id == first.preference_id
    assert order_memories[0].value == "Preferencia de pedido: ensalada, café"


def test_recreation_compacts_existing_order_summaries(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.db"
    store = create_store(database_path)
    store.grant_consent("customer-1", source="test")
    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="Preferencia de pedido: tortilla, agua",
        source_conversation_id="conv-1",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO durable_memories(
                preference_id, actor_id, kind, normalized_value, value,
                source_conversation_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pref_legacy_latest",
                "customer-1",
                MemoryKind.PREFERENCE.value,
                "preferencia de pedido: ensalada, café",
                "Preferencia de pedido: ensalada, café",
                "conv-2",
                "2099-01-01T00:00:00+00:00",
                "2099-01-01T00:00:00+00:00",
            ),
        )

    recreated_store = create_store(database_path)
    order_memories = [
        memory
        for memory in recreated_store.list_memories("customer-1")
        if memory.value.startswith("Preferencia de pedido: ")
    ]

    assert [memory.value for memory in order_memories] == [
        "Preferencia de pedido: ensalada, café"
    ]


def test_concurrent_writes_do_not_corrupt_memory(tmp_path: Path) -> None:
    store = create_store(tmp_path / "memory.db")
    store.grant_consent("customer-1", source="test")

    def remember(index: int) -> None:
        store.remember_memory(
            "customer-1",
            kind=(
                MemoryKind.PREFERENCE
                if index % 2 == 0
                else MemoryKind.RESTRICTION
            ),
            value=f"memoria {index}",
            source_conversation_id=f"conv-{index}",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(remember, range(10)))

    assert len(store.list_memories("customer-1")) == 10
