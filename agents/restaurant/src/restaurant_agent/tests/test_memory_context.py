from pathlib import Path

import pytest
from agent_framework import AgentResponse, AgentSession, Message, SessionContext

from restaurant_agent.contracts import (
    CustomerSnapshot,
    OrderDraft,
    OrderItemDraft,
    WaiterModelResult,
)
from restaurant_agent.memory.context import (
    DurableMemoryContextProvider,
    rank_habitual_order_preferences,
)
from restaurant_agent.memory.contracts import (
    MemoryCandidate,
    MemoryIntent,
    MemoryKind,
)
from restaurant_agent.memory.intent import MemoryIntentDecision
from restaurant_agent.memory.options import HabitualOrderQuestion
from restaurant_agent.memory.store import SQLiteMemoryStore


class FakeClassifier:
    async def run(
        self,
        messages: str,
        *,
        options: dict[str, object],
    ) -> AgentResponse[MemoryIntentDecision]:
        return AgentResponse(
            value=MemoryIntentDecision(
                memory_intent=MemoryIntent.REUSE_LATEST_ORDER
            )
        )


class FakeOptionMerger:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(
        self,
        messages: str,
        *,
        options: dict[str, object],
    ) -> AgentResponse[HabitualOrderQuestion]:
        self.prompts.append(messages)
        return AgentResponse(
            value=HabitualOrderQuestion(
                question=(
                    "¿Prefieres hoy Coca-Cola o agua con gas? "
                    "¿Quieres también pincho de tortilla?"
                )
            )
        )


@pytest.mark.asyncio
async def test_context_provider_injects_only_consented_authenticated_memory(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.grant_consent("customer-1", source="test")
    store.remember_memory(
        "customer-1",
        kind=MemoryKind.RESTRICTION,
        value="agua con gas",
        source_conversation_id="conv-old",
    )
    provider = DurableMemoryContextProvider(store)
    session = AgentSession(session_id="conv-new")
    session.state["memory_identity"] = {
        "actor_id": "customer-1",
        "authenticated": True,
    }
    context = SessionContext(input_messages=[])

    await provider.before_run(
        agent=object(),
        session=session,
        context=context,
        state={},
    )

    assert len(context.instructions) == 1
    assert "agua con gas" in context.instructions[0]
    assert "no confiable" in context.instructions[0]
    assert "requires_reconfirmation" in context.instructions[0]
    assert "`remembered_memories`" in context.instructions[0]

    store.revoke_consent("customer-1", source="test")
    context_after_revocation = SessionContext(input_messages=[])
    await provider.before_run(
        agent=object(),
        session=session,
        context=context_after_revocation,
        state={},
    )
    assert context_after_revocation.instructions == []


@pytest.mark.asyncio
async def test_context_identifies_habitual_order_preference_for_repeat_intent(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.grant_consent("customer-1", source="test")
    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="Preferencia de pedido: tortilla de patatas, agua con gas",
        source_conversation_id="conv-old",
    )
    provider = DurableMemoryContextProvider(
        store,
        intent_classifier=FakeClassifier(),
    )
    session = AgentSession(session_id="conv-new")
    session.state["memory_identity"] = {
        "actor_id": "customer-1",
        "authenticated": True,
    }
    context = SessionContext(
        input_messages=[Message("user", ["Ponme aquello que suelo pedir"])]
    )

    await provider.before_run(
        agent=object(),
        session=session,
        context=context,
        state={},
    )

    combined_instructions = "\n".join(context.instructions)
    assert (
        '"habitual_order_preference": "tortilla de patatas, agua con gas"'
        in combined_instructions
    )
    assert "«lo de siempre»" in combined_instructions
    assert "estado `unverified`" in combined_instructions
    assert "La aplicación ha interpretado semánticamente" in combined_instructions
    assert '"occurrence_count"' not in combined_instructions


def test_habitual_orders_are_ranked_by_frequency_then_recency(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.grant_consent("customer-1", source="test")
    water = "Preferencia de pedido: agua con gas"
    cola = "Preferencia de pedido: coca cola"

    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value=water,
        source_conversation_id="conv-1",
    )
    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value=water,
        source_conversation_id="conv-2",
    )
    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value=cola,
        source_conversation_id="conv-3",
    )

    assert rank_habitual_order_preferences(
        store.list_memories("customer-1")
    ) == [
        ("agua con gas", 2),
        ("coca cola", 1),
    ]

    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value=cola,
        source_conversation_id="conv-4",
    )

    assert rank_habitual_order_preferences(
        store.list_memories("customer-1")
    ) == [
        ("coca cola", 2),
        ("agua con gas", 2),
    ]


@pytest.mark.asyncio
async def test_context_merges_overlapping_habitual_orders_into_one_question(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.grant_consent("customer-1", source="test")
    for value in (
        "Preferencia de pedido: agua con gas, pincho de tortilla",
        "Preferencia de pedido: coca cola",
        (
            "Preferencia de pedido: agua con gas, pincho de tortilla, "
            "coca cola"
        ),
    ):
        store.remember_memory(
            "customer-1",
            kind=MemoryKind.PREFERENCE,
            value=value,
            source_conversation_id="conv-old",
        )
    merger = FakeOptionMerger()
    provider = DurableMemoryContextProvider(
        store,
        intent_classifier=FakeClassifier(),
        option_merger=merger,
    )
    session = AgentSession(session_id="conv-new")
    session.state["memory_identity"] = {
        "actor_id": "customer-1",
        "authenticated": True,
    }
    context = SessionContext(
        input_messages=[Message("user", ["Ponme lo de siempre"])]
    )

    await provider.before_run(
        agent=object(),
        session=session,
        context=context,
        state={},
    )

    question = (
        "¿Prefieres hoy Coca-Cola o agua con gas? "
        "¿Quieres también pincho de tortilla?"
    )
    assert merger.prompts
    assert session.state["habitual_order_question"] == question
    assert question in "\n".join(context.instructions)


@pytest.mark.asyncio
async def test_context_provider_ignores_guest_identity(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    provider = DurableMemoryContextProvider(store)
    session = AgentSession(session_id="conv-new")
    session.state["memory_identity"] = {
        "actor_id": "guest-1",
        "authenticated": False,
    }
    context = SessionContext(input_messages=[])

    await provider.before_run(
        agent=object(),
        session=session,
        context=context,
        state={},
    )

    assert context.instructions == []


@pytest.mark.asyncio
async def test_development_fallback_identity_persists_structured_candidates(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.grant_consent("Majo", source="development-environment")
    provider = DurableMemoryContextProvider(
        store,
        fallback_actor_id="Majo",
        persist_fallback_candidates=True,
    )
    session = AgentSession(session_id="conv-dev")
    context = SessionContext(input_messages=[])
    state: dict[str, object] = {}

    await provider.before_run(
        agent=object(),
        session=session,
        context=context,
        state=state,
    )
    assert len(context.instructions) == 1
    assert '"presented_name": "Majo"' in context.instructions[0]
    assert "no vuelvas a preguntarlo" in context.instructions[0]
    assert "saluda por ese nombre" in context.instructions[0]
    context._response = AgentResponse(
        value=WaiterModelResult(
            reply="Lo recordaré como información no vinculante.",
            customer=CustomerSnapshot(
                presented_name="Majo",
                party_size=2,
                restrictions=["alergia a frutos secos"],
            ),
            order_draft=OrderDraft(
                items=[OrderItemDraft(name="tortilla de patatas")]
            ),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
            memory_candidates=[
                MemoryCandidate(
                    kind=MemoryKind.RESTRICTION,
                    value="alergia a frutos secos",
                )
            ],
        )
    )
    await provider.after_run(
        agent=object(),
        session=session,
        context=context,
        state=state,
    )

    memories = store.list_memories("Majo")
    assert {(item.kind, item.value) for item in memories} == {
        (MemoryKind.RESTRICTION, "alergia a frutos secos"),
        (MemoryKind.PREFERENCE, "Preferencia de pedido: tortilla de patatas"),
    }
    assert all(item.requires_reconfirmation for item in memories)
    assert {
        (item.kind, item.value) for item in context.response.value.remembered_memories
    } == {
        (MemoryKind.RESTRICTION, "alergia a frutos secos"),
        (MemoryKind.PREFERENCE, "Preferencia de pedido: tortilla de patatas"),
    }
