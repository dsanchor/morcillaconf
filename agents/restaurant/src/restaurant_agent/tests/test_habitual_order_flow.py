"""The real waiter memory path with scripted model responses, never Foundry."""

import json
from pathlib import Path

import pytest
from agent_framework import AgentSession

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import WaiterTurnFailure, WaiterTurnRequest

from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.contracts import DraftItemStatus
from restaurant_agent.conversation import ConversationManager, InvalidAgentResponseError
from restaurant_agent.memory.contracts import MemoryIntent, MemoryKind
from restaurant_agent.memory.options import HabitualOrderSelectionError
from restaurant_agent.memory.store import SQLiteMemoryStore
from restaurant_agent.remote import RemoteWaiterService
from scripted_model import ScriptedModel, waiter_result


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_model_deployment_name="scripted-model",
        memory_database_path=tmp_path / "memory.db",
        enable_dev_fake_identity=False,
        seating_mcp_url=None,
        azure_search_endpoint=None,
        knowledge_base_name=None,
    )


def remember(store: SQLiteMemoryStore, summary: str, actor_id: str = "ana"):
    return store.remember_memory(
        actor_id,
        kind=MemoryKind.PREFERENCE,
        value=f"Preferencia de pedido: {summary}",
        source_conversation_id="previous-visit",
    )


def repeat(reply: str, option_id: str | None = None) -> dict:
    result = waiter_result(reply)
    result.update(
        memory_intent="reuse_latest_order",
        habitual_order_option_id=option_id,
        order_draft={"items": [{"name": "producto inventado", "quantity": 9}]},
    )
    return {"result": result}


@pytest.mark.asyncio
async def test_waiter_clarifies_then_selects_after_json_restore_without_auxiliary_calls(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    store = SQLiteMemoryStore(settings.memory_database_path)
    remember(store, "tortilla, agua con gas")
    both = remember(store, "tortilla, agua con gas, coca cola")
    store.remember_memory(
        "ana", kind=MemoryKind.RESTRICTION, value="alergia a frutos secos",
        source_conversation_id="previous-visit",
    )
    question = "¿Quieres tortilla con agua con gas o también con Coca-Cola?"
    proposal = "Propongo tortilla y ambas bebidas, sin confirmar. ¿Sigue tu alergia?"
    model = ScriptedModel([repeat(question), repeat(proposal, both.preference_id)])
    agent = create_waiter_agent(settings, memory_store=store, client=model)
    async with agent:
        manager = ConversationManager(agent, memory_store=store)
        conversation_id = manager.start_conversation(actor_id="ana", authenticated=True)
        first = await manager.send_message(
            conversation_id=conversation_id, actor_id="ana", message="Ponme lo de siempre",
        )
        assert first.reply == question
        assert first.order_draft.items == []
        assert all(memory.occurrence_count == 1 for memory in store.list_memories("ana"))

        exported = manager.export_conversation(conversation_id=conversation_id, actor_id="ana")
        restored = AgentSession.from_dict(
            json.loads(json.dumps(exported.agent_session.to_dict()))
        )
        manager = ConversationManager(agent, memory_store=store)
        manager.restore_conversation(
            conversation_id=conversation_id, actor_id="ana", authenticated=True,
            state=exported.state, agent_session=restored,
            persisted_order_preferences=exported.persisted_order_preferences,
        )
        second = await manager.send_message(
            conversation_id=conversation_id, actor_id="ana",
            message="Con las dos bebidas y tortilla",
        )

    assert second.reply == proposal
    assert second.memory_intent is MemoryIntent.REUSE_LATEST_ORDER
    assert [item.name for item in second.order_draft.items] == [
        "tortilla", "agua con gas", "coca cola",
    ]
    assert all(
        item.quantity == 1 and item.status is DraftItemStatus.UNVERIFIED
        for item in second.order_draft.items
    )
    assert second.customer.restrictions == []
    assert second.kitchen is second.bar is second.cashier is None
    assert len(model.calls) == 2
    assert model.script == []
    assert all(both.preference_id in call["instructions"] for call in model.calls)
    assert "habitual_order_option_id" not in second.model_dump()
    assert not any("inventado" in memory.value for memory in store.list_memories("ana"))


@pytest.mark.asyncio
async def test_deleted_option_cannot_fall_back_to_the_remaining_unique_option(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    store = SQLiteMemoryStore(settings.memory_database_path)
    deleted = remember(store, "tortilla, agua con gas")
    remaining = remember(store, "coca cola")
    model = ScriptedModel([
        repeat("¿Qué pedido habitual quieres hoy?"),
        repeat("Propongo el de tortilla.", deleted.preference_id),
    ])
    agent = create_waiter_agent(settings, memory_store=store, client=model)
    async with agent:
        manager = ConversationManager(agent, memory_store=store)
        conversation_id = manager.start_conversation(actor_id="ana", authenticated=True)
        await manager.send_message(
            conversation_id=conversation_id, actor_id="ana", message="Lo habitual",
        )
        store.delete_memory("ana", preference_id=deleted.preference_id)

        with pytest.raises(InvalidAgentResponseError):
            await manager.send_message(
                conversation_id=conversation_id, actor_id="ana", message="El de tortilla",
            )

        exported = manager.export_conversation(conversation_id=conversation_id, actor_id="ana")
        assert exported.state.turn_count == 1
        assert exported.state.order_draft.items == []
    assert store.list_memories("ana") == [remaining]
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [True, False])
async def test_remote_rejects_foreign_option_with_a_public_invalid_response(
    tmp_path: Path, authenticated: bool,
) -> None:
    settings = settings_for(tmp_path)
    store = SQLiteMemoryStore(settings.memory_database_path)
    own = remember(store, "agua con gas")
    foreign = remember(store, "tortilla", actor_id="another-customer")
    model = ScriptedModel([repeat("Propongo tortilla.", foreign.preference_id)])
    service = RemoteWaiterService(
        settings, memory_store=store,
        agent_factory=lambda settings, memory_store: create_waiter_agent(
            settings, memory_store=memory_store, client=model,
        ),
    )

    response = await service.take_turn(
        WaiterTurnRequest(
            conversation_id="conv-ana",
            actor=ActorContext(actor_id="ana", authenticated=authenticated),
            presented_name="Ana", message="Lo de siempre",
            customer=CustomerSnapshot(presented_name="Ana"),
            order_draft=OrderDraft(), turn_count=0, correlation_id="corr-ana",
        )
    )

    assert isinstance(response, WaiterTurnFailure)
    assert response.code == "invalid_response"
    assert store.list_memories("ana") == [own]
    assert store.list_memories("another-customer") == [foreign]
    assert len(model.calls) == 1
    assert foreign.preference_id not in model.calls[0]["instructions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_development_memory_validates_before_persisting_the_draft(
    tmp_path: Path, valid: bool,
) -> None:
    settings = settings_for(tmp_path).model_copy(
        update={"enable_dev_fake_identity": True, "dev_fake_actor_id": "ana"}
    )
    store = SQLiteMemoryStore(settings.memory_database_path)
    memory = remember(store, "tortilla, agua con gas")
    model = ScriptedModel([
        repeat("Borrador sin verificar ni confirmar.", memory.preference_id if valid else "invalid")
    ])
    agent = create_waiter_agent(settings, memory_store=store, client=model)
    async with agent:
        if valid:
            response = await agent.run("Lo de siempre", session=agent.create_session())
            assert [item.name for item in response.value.order_draft.items] == [
                "tortilla", "agua con gas",
            ]
        else:
            with pytest.raises(HabitualOrderSelectionError):
                await agent.run("Lo de siempre", session=agent.create_session())

    memories = store.list_memories("ana")
    assert [item.value for item in memories] == [memory.value]
    assert memories[0].occurrence_count == (2 if valid else 1)
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "name", "quantity"),
    [
        ("Ponme otra de croquetas", "croquetas", 1),
        ("Dos cañas más", "cañas", 2),
        ("Lo habitual pero sin tortilla", "agua con gas", 1),
    ],
)
async def test_waiter_handles_new_and_modified_orders_without_fallback_model_calls(
    tmp_path: Path, message: str, name: str, quantity: int,
) -> None:
    settings = settings_for(tmp_path)
    store = SQLiteMemoryStore(settings.memory_database_path)
    remember(store, "tortilla, agua con gas")
    result = waiter_result("Apuntado en el borrador sin confirmar.")
    result["order_draft"] = {"items": [{"name": name, "quantity": quantity}]}
    model = ScriptedModel([{"result": result}])
    agent = create_waiter_agent(settings, memory_store=store, client=model)
    async with agent:
        manager = ConversationManager(agent, memory_store=store)
        conversation_id = manager.start_conversation(actor_id="ana", authenticated=True)
        response = await manager.send_message(
            conversation_id=conversation_id, actor_id="ana", message=message,
        )

    assert response.memory_intent is MemoryIntent.NONE
    assert [(item.name, item.quantity) for item in response.order_draft.items] == [
        (name, quantity)
    ]
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summaries", [[], ["tortilla, agua con gas"], ["agua con gas", "coca cola"]]
)
async def test_end_to_end_scripted_client_uses_the_new_memory_context(
    tmp_path: Path, monkeypatch, summaries: list[str],
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5] / "tests/end_to_end"))
    from scripted_chat_client import ScriptedChatClient

    settings = settings_for(tmp_path)
    store = SQLiteMemoryStore(settings.memory_database_path)
    for summary in summaries:
        remember(store, summary)
    model = ScriptedChatClient()
    agent = create_waiter_agent(settings, memory_store=store, client=model)
    async with agent:
        manager = ConversationManager(agent, memory_store=store)
        conversation_id = manager.start_conversation(actor_id="ana", authenticated=True)
        response = await manager.send_message(
            conversation_id=conversation_id, actor_id="ana", message="Ponme lo de siempre",
        )

    assert response.memory_intent is MemoryIntent.REUSE_LATEST_ORDER
    assert [item.name for item in response.order_draft.items] == (
        ["tortilla", "agua con gas"] if len(summaries) == 1 else []
    )
    assert response.kitchen is response.bar is None
    assert len(model.prompts) == 1
    assert all("inventado" not in item.value for item in store.list_memories("ana"))
