from pathlib import Path
from types import SimpleNamespace

import pytest

from restaurant_agent.contracts import (
    CustomerSnapshot,
    MemoryIntent,
    OrderDraft,
    OrderItemDraft,
    WaiterModelResult,
)
from restaurant_agent.conversation import ConversationManager, GuestMemoryError
from restaurant_agent.memory.contracts import MemoryCandidate, MemoryKind
from restaurant_agent.memory.store import SQLiteMemoryStore


class FakeAgent:
    def __init__(self, *results: WaiterModelResult) -> None:
        self.results = list(results)
        self.prompts: list[str] = []

    def create_session(self, *, session_id: str | None = None) -> object:
        return SimpleNamespace(state={})

    async def run(
        self,
        messages: str,
        *,
        session: object,
        options: dict[str, object],
    ) -> object:
        self.prompts.append(messages)
        return SimpleNamespace(value=self.results.pop(0))


def waiter_result(
    *,
    restrictions: list[str] | None = None,
    memory_candidates: list[MemoryCandidate] | None = None,
    order_draft: OrderDraft | None = None,
) -> WaiterModelResult:
    return WaiterModelResult(
        reply="Anotado para esta conversación.",
        customer=CustomerSnapshot(
            presented_name="Majo",
            party_size=2,
            preferences=["agua con gas"],
            restrictions=restrictions or [],
        ),
        order_draft=order_draft or OrderDraft(),
        pending_fields=[],
        memory_intent=MemoryIntent.NONE,
        memory_candidates=memory_candidates or [],
    )


@pytest.mark.asyncio
async def test_memory_is_written_automatically_from_the_first_turn(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    candidate = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        value="agua con gas",
    )
    manager = ConversationManager(
        FakeAgent(
            waiter_result(memory_candidates=[candidate]),
            waiter_result(memory_candidates=[candidate]),
        ),
        memory_store=store,
    )
    conversation_id = manager.start_conversation(
        actor_id="customer-1",
        authenticated=True,
    )

    await manager.send_message(
        conversation_id=conversation_id,
        actor_id="customer-1",
        message="Prefiero agua con gas.",
    )
    assert store.list_memories("customer-1")[0].value == "agua con gas"
    await manager.send_message(
        conversation_id=conversation_id,
        actor_id="customer-1",
        message="Prefiero agua con gas.",
    )

    memories = store.list_memories("customer-1")
    assert [(item.kind, item.value) for item in memories] == [
        (MemoryKind.PREFERENCE, "agua con gas")
    ]
    assert memories[0].occurrence_count == 2


@pytest.mark.asyncio
async def test_order_draft_is_summarized_as_long_term_preference(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    manager = ConversationManager(
        FakeAgent(
            waiter_result(
                order_draft=OrderDraft(
                    items=[
                        OrderItemDraft(name="tortilla de patatas", quantity=2),
                        OrderItemDraft(name="agua con gas"),
                    ]
                )
            )
        ),
        memory_store=store,
    )
    conversation_id = manager.start_conversation(
        actor_id="customer-1",
        authenticated=True,
    )
    await manager.send_message(
        conversation_id=conversation_id,
        actor_id="customer-1",
        message="Quiero dos tortillas de patatas y un agua con gas.",
    )

    memories = store.list_memories("customer-1")
    assert [(item.kind, item.value) for item in memories] == [
        (
            MemoryKind.PREFERENCE,
            "Preferencia de pedido: tortilla de patatas, agua con gas",
        )
    ]


@pytest.mark.asyncio
async def test_restriction_is_stored_separately_and_is_non_binding(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    manager = ConversationManager(
        FakeAgent(
            waiter_result(
                restrictions=["alergia a frutos secos"],
                memory_candidates=[
                    MemoryCandidate(
                        kind=MemoryKind.PREFERENCE,
                        value="agua con gas",
                    ),
                    MemoryCandidate(
                        kind=MemoryKind.RESTRICTION,
                        value="alergia a frutos secos",
                    ),
                ],
            )
        ),
        memory_store=store,
    )
    conversation_id = manager.start_conversation(
        actor_id="customer-1",
        authenticated=True,
    )
    response = await manager.send_message(
        conversation_id=conversation_id,
        actor_id="customer-1",
        message="Prefiero agua con gas y tengo alergia a frutos secos.",
    )

    memories = store.list_memories("customer-1")
    assert {item.kind for item in memories} == {
        MemoryKind.PREFERENCE,
        MemoryKind.RESTRICTION,
    }
    assert all(item.requires_reconfirmation for item in memories)
    assert any(
        item.kind is MemoryKind.RESTRICTION
        for item in response.remembered_memories
    )


def test_guest_cannot_create_durable_profile(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    manager = ConversationManager(FakeAgent(), memory_store=store)
    conversation_id = manager.start_conversation(actor_id="guest-1")

    with pytest.raises(GuestMemoryError):
        manager.memory_snapshot(
            conversation_id=conversation_id,
            actor_id="guest-1",
        )

    assert store.list_memories("guest-1") == []


def test_new_manager_recovers_only_same_identity_memories(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.db"
    first_store = SQLiteMemoryStore(database_path)
    first_store.remember_memory(
        "customer-1",
        kind=MemoryKind.RESTRICTION,
        value="alergia a frutos secos",
        source_conversation_id="conv-old",
    )

    recreated_store = SQLiteMemoryStore(database_path)
    same_identity = ConversationManager(FakeAgent(), memory_store=recreated_store)
    other_identity = ConversationManager(FakeAgent(), memory_store=recreated_store)

    same_id = same_identity.start_conversation(
        actor_id="customer-1",
        authenticated=True,
    )
    other_id = other_identity.start_conversation(
        actor_id="customer-2",
        authenticated=True,
    )

    same_snapshot = same_identity.memory_snapshot(
        conversation_id=same_id,
        actor_id="customer-1",
    )
    other_snapshot = other_identity.memory_snapshot(
        conversation_id=other_id,
        actor_id="customer-2",
    )
    assert same_snapshot.memories[0].kind is MemoryKind.RESTRICTION
    assert other_snapshot.memories == []


@pytest.mark.asyncio
async def test_clear_is_seen_by_other_active_conversations(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="agua con gas",
        source_conversation_id="conv-old",
    )
    agent = FakeAgent(
        WaiterModelResult(
            reply="Continuamos.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=2),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        ),
        WaiterModelResult(
            reply="No conservo recuerdos.",
            customer=CustomerSnapshot(presented_name="Majo", party_size=2),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        ),
    )
    manager = ConversationManager(agent, memory_store=store)
    first_id = manager.start_conversation(
        actor_id="customer-1",
        authenticated=True,
    )
    second_id = manager.start_conversation(
        actor_id="customer-1",
        authenticated=True,
    )

    await manager.send_message(
        conversation_id=second_id,
        actor_id="customer-1",
        message="Continuemos.",
    )
    manager.clear_memories(
        conversation_id=first_id,
        actor_id="customer-1",
    )
    response = await manager.send_message(
        conversation_id=second_id,
        actor_id="customer-1",
        message="¿Qué recuerdas?",
    )

    assert response.remembered_memories == []
    assert all("agua con gas" not in prompt for prompt in agent.prompts)


@pytest.mark.asyncio
async def test_guest_turn_does_not_persist_candidates(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    manager = ConversationManager(
        FakeAgent(waiter_result(memory_candidates=[
            MemoryCandidate(kind=MemoryKind.PREFERENCE, value="agua con gas")
        ])),
        memory_store=store,
    )
    conversation_id = manager.start_conversation(actor_id="guest")
    response = await manager.send_message(
        conversation_id=conversation_id, actor_id="guest", message="Prefiero agua."
    )
    assert response.remembered_memories == []
    assert store.list_memories("guest") == []


@pytest.mark.asyncio
async def test_clear_allows_new_memories_in_later_turns(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    manager = ConversationManager(
        FakeAgent(*[
            waiter_result(memory_candidates=[
                MemoryCandidate(kind=MemoryKind.PREFERENCE, value=value)
            ])
            for value in ("agua con gas", "agua sin gas")
        ]),
        memory_store=store,
    )
    conversation_id = manager.start_conversation(actor_id="customer", authenticated=True)
    await manager.send_message(
        conversation_id=conversation_id, actor_id="customer", message="Agua con gas."
    )
    assert manager.clear_memories(
        conversation_id=conversation_id, actor_id="customer"
    ).memories == []
    response = await manager.send_message(
        conversation_id=conversation_id, actor_id="customer", message="Agua sin gas."
    )
    assert [item.value for item in response.remembered_memories] == ["agua sin gas"]
