import json

import pytest

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft

from restaurant_contracts.memory_store import SQLiteMemoryStore

from bff.scripted import ScriptedWaiterAgent
from bff.local_waiter import (
    LocalWaiter,
    WaiterTurn,
    WaiterTurnLimitError,
    WaiterUnavailableError,
)

ANA = ActorContext(actor_id="ana", authenticated=True)


class FakeServiceError(Exception):
    """Stands in for an agent_framework service exception."""


FakeServiceError.__module__ = "agent_framework.exceptions"


def turn(message: str, previous=None, **overrides) -> WaiterTurn:
    fields = dict(
        conversation_id="conv_1",
        actor=ANA,
        presented_name="Ana",
        message=message,
        customer=CustomerSnapshot(presented_name="Ana"),
        order_draft=OrderDraft(),
        turn_count=0,
        persisted_order_preferences=(),
        session_json=None,
        correlation_id="corr_1",
    )
    if previous is not None:
        fields.update(
            customer=previous.customer,
            order_draft=previous.order_draft,
            turn_count=previous.turn_count,
            persisted_order_preferences=previous.persisted_order_preferences,
            session_json=previous.session_json,
        )
    fields.update(overrides)
    return WaiterTurn(**fields)


@pytest.fixture
def store(tmp_path) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(tmp_path / "memory.db")


def waiter(store, agent=None, max_turns=20) -> LocalWaiter:
    return LocalWaiter(
        agent or ScriptedWaiterAgent(), mode="scripted", max_turns=max_turns, memory_store=store
    )


async def test_the_door_name_wins_over_a_name_said_in_the_chat(store) -> None:
    agent = ScriptedWaiterAgent()
    result = await waiter(store, agent).take_turn(turn("Soy Pepe y venimos tres."))

    assert result.customer.presented_name == "Ana"
    assert result.customer.party_size == 3
    assert result.turn_count == 1
    assert '"presented_name": "Ana"' in agent.prompts[0]
    assert "no repitas el saludo" in agent.prompts[0]


async def test_memory_is_written_for_the_actor_only(store) -> None:
    await waiter(store).take_turn(
        turn("Prefiero agua con gas y soy alérgica a los frutos secos.")
    )

    memories = {(memory.kind.value, memory.value) for memory in store.list_memories("ana")}
    assert memories == {("preference", "agua con gas"), ("restriction", "frutos secos")}
    assert store.list_memories("luis") == []


async def test_history_and_order_guard_survive_between_turns(store) -> None:
    agent = ScriptedWaiterAgent()
    first = await waiter(store, agent).take_turn(turn("Quiero una tortilla de patatas."))
    second = await waiter(store, agent).take_turn(turn("Gracias.", first))

    session = json.loads(second.session_json)
    assert session["state"]["scripted_history"] == [
        "Quiero una tortilla de patatas.",
        "Gracias.",
    ]
    assert session["state"]["memory_identity"] == {"actor_id": "ana", "authenticated": True}
    assert [item.name for item in second.order_draft.items] == ["tortilla de patatas"]
    order_memories = [
        (memory.value, memory.occurrence_count) for memory in store.list_memories("ana")
    ]
    assert order_memories == [("Preferencia de pedido: tortilla de patatas", 1)]


async def test_stored_identity_is_replaced_by_the_caller(store) -> None:
    first = await waiter(store).take_turn(turn("Hola."))
    tampered = json.loads(first.session_json)
    tampered["state"]["memory_identity"] = {"actor_id": "luis", "authenticated": True}
    second = await waiter(store).take_turn(
        turn("Prefiero vino tinto.", first, session_json=json.dumps(tampered))
    )

    assert json.loads(second.session_json)["state"]["memory_identity"]["actor_id"] == "ana"
    assert store.list_memories("luis") == []


async def test_turn_limit_is_enforced(store) -> None:
    first = await waiter(store, max_turns=1).take_turn(turn("Hola."))
    with pytest.raises(WaiterTurnLimitError):
        await waiter(store, max_turns=1).take_turn(turn("Otra vez.", first))


async def test_service_failures_become_unavailable(store) -> None:
    agent = ScriptedWaiterAgent(failure=lambda message: FakeServiceError("boom"))
    with pytest.raises(WaiterUnavailableError):
        await waiter(store, agent).take_turn(turn("Hola."))


async def test_scripted_waiter_rejects_an_unknown_prompt() -> None:
    agent = ScriptedWaiterAgent()
    with pytest.raises(RuntimeError):
        await agent.run("hola", session=agent.create_session(session_id="s"), options={})


def test_agent_sessions_with_history_round_trip_as_json() -> None:
    from agent_framework import AgentSession, Message

    from bff.local_waiter import AgentSessionCodec

    session = AgentSession(session_id="conv_1")
    session.state["messages"] = [
        Message(role="user", contents=["Hola"]),
        Message(role="assistant", contents=["¿Qué os pongo?"]),
    ]
    session.state["memory_identity"] = {"actor_id": "ana", "authenticated": True}
    codec = AgentSessionCodec()

    restored = codec.load(codec.dump(session))

    assert restored.session_id == "conv_1"
    assert [message.text for message in restored.state["messages"]] == [
        "Hola",
        "¿Qué os pongo?",
    ]
    assert codec.load("not json") is None
    assert codec.load(None) is None


async def test_the_visit_context_survives_the_session_round_trip() -> None:
    from agent_framework import AgentSession

    from restaurant_agent.seating import VisitContextProvider

    from bff.local_waiter import AgentSessionCodec

    provider = VisitContextProvider()
    codec = AgentSessionCodec()
    session = AgentSession(session_id="conv_1")
    await provider.before_run(agent=None, session=session, context=None, state={})
    created = dict(session.state["visit_context"])

    restored = codec.load(codec.dump(session))
    await provider.before_run(agent=None, session=restored, context=None, state={})

    assert created["visit_id"].startswith("visit_")
    assert restored.state["visit_context"] == created
