import json
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
)
from restaurant_agent.memory.contracts import (
    MemoryCandidate,
    MemoryIntent,
    MemoryKind,
)
from restaurant_agent.memory.options import rank_habitual_order_options
from restaurant_agent.memory.store import SQLiteMemoryStore


@pytest.mark.asyncio
async def test_context_provider_injects_identified_memory_without_consent(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
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

    store.delete_all_memories("customer-1")
    context_after_clear = SessionContext(input_messages=[])
    await provider.before_run(
        agent=object(),
        session=session,
        context=context_after_clear,
        state={},
    )
    assert context_after_clear.instructions == []


@pytest.mark.asyncio
async def test_context_exposes_stable_options_without_interpreting_intent(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    memory = store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value="Preferencia de pedido: tortilla de patatas, agua con gas",
        source_conversation_id="conv-old",
    )
    provider = DurableMemoryContextProvider(store)
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
        '"summary": "tortilla de patatas, agua con gas"'
        in combined_instructions
    )
    assert f'"option_id": "{memory.preference_id}"' in combined_instructions
    assert "`unverified`" in combined_instructions
    assert "La aplicación ha interpretado semánticamente" not in combined_instructions
    assert "memory_intent" not in session.state
    assert session.state["habitual_order_options"] == [
        {
            "option_id": memory.preference_id,
            "summary": "tortilla de patatas, agua con gas",
        }
    ]
    assert '"occurrence_count"' not in combined_instructions


def test_habitual_orders_are_ranked_by_frequency_then_recency(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
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

    options = rank_habitual_order_options(store.list_memories("customer-1"))
    assert [option.summary for option in options] == ["agua con gas", "coca cola"]
    original_ids = {option.summary: option.option_id for option in options}

    store.remember_memory(
        "customer-1",
        kind=MemoryKind.PREFERENCE,
        value=cola,
        source_conversation_id="conv-4",
    )

    reordered = rank_habitual_order_options(store.list_memories("customer-1"))
    assert [option.summary for option in reordered] == ["coca cola", "agua con gas"]
    assert {option.summary: option.option_id for option in reordered} == original_ids


@pytest.mark.asyncio
async def test_context_exposes_overlapping_combinations_without_merging_them(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
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
    provider = DurableMemoryContextProvider(store)
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

    options = session.state["habitual_order_options"]
    assert {option["summary"] for option in options} == {
        "agua con gas, pincho de tortilla",
        "coca cola",
        "agua con gas, pincho de tortilla, coca cola",
    }
    assert len({option["option_id"] for option in options}) == 3
    assert "habitual_order_question" not in session.state
    assert "memory_intent" not in session.state
    assert "son excluyentes" in "\n".join(context.instructions)


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

    recreated = SQLiteMemoryStore(tmp_path / "memory.db")
    assert recreated.list_memories("Majo") == memories
    assert recreated.list_memories("Other") == []


@pytest.mark.asyncio
async def test_development_fallback_counts_unchanged_order_once_per_session(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    provider = DurableMemoryContextProvider(
        store,
        fallback_actor_id="Majo",
        persist_fallback_candidates=True,
    )
    session = AgentSession(session_id="conv-dev")
    for _ in range(2):
        # The hosted server restores the session from its JSON store each turn.
        session = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
        context = SessionContext(input_messages=[])
        state: dict[str, object] = {}
        await provider.before_run(
            agent=object(),
            session=session,
            context=context,
            state=state,
        )
        context._response = AgentResponse(
            value=WaiterModelResult(
                reply="Anotado.",
                customer=CustomerSnapshot(presented_name="Majo"),
                order_draft=OrderDraft(
                    items=[OrderItemDraft(name="tortilla de patatas")]
                ),
                pending_fields=[],
                memory_intent=MemoryIntent.NONE,
            )
        )
        await provider.after_run(
            agent=object(),
            session=session,
            context=context,
            state=state,
        )

    memories = store.list_memories("Majo")
    assert [(item.value, item.occurrence_count) for item in memories] == [
        ("Preferencia de pedido: tortilla de patatas", 1)
    ]


@pytest.mark.asyncio
async def test_clear_invalidates_habitual_order_state(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.remember_memory(
        "customer", kind=MemoryKind.PREFERENCE,
        value="Preferencia de pedido: agua", source_conversation_id="old",
    )
    provider = DurableMemoryContextProvider(store)
    session = AgentSession(session_id="current")
    session.state["memory_identity"] = {"actor_id": "customer", "authenticated": True}
    state: dict[str, object] = {}
    await provider.before_run(
        agent=object(), session=session,
        context=SessionContext(input_messages=[Message("user", ["Lo de siempre"])]),
        state=state,
    )
    assert session.state["habitual_order_options"][0]["summary"] == "agua"
    store.delete_all_memories("customer")
    context = SessionContext(input_messages=[])
    await provider.before_run(agent=object(), session=session, context=context, state=state)
    assert context.instructions == []
    for key in ("habitual_order_preference", "habitual_order_options", "memory_intent"):
        assert key not in session.state


@pytest.mark.asyncio
async def test_guest_never_inherits_fallback_identity_or_previous_turn_state(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    provider = DurableMemoryContextProvider(
        store, fallback_actor_id="Majo", persist_fallback_candidates=True,
    )
    session = AgentSession(session_id="current")
    session.state["memory_identity"] = {"actor_id": "guest", "authenticated": False}
    state: dict[str, object] = {"actor_id": "Majo", "using_fallback": True}
    context = SessionContext(input_messages=[])
    await provider.before_run(agent=object(), session=session, context=context, state=state)
    await provider.after_run(agent=object(), session=session, context=context, state=state)
    assert state == {}
    assert context.instructions == []
    assert store.list_memories("Majo") == []
    assert store.list_memories("guest") == []
