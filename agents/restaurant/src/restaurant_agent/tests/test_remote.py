import asyncio
from types import SimpleNamespace

import pytest
from agent_framework import AgentSession

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import WaiterServeRequest, WaiterTurnRequest, WaiterTurnSuccess

from restaurant_agent.config import Settings
from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.memory.contracts import MemoryCandidate, MemoryIntent, MemoryKind
from restaurant_agent.memory.store import SQLiteMemoryStore
from restaurant_agent.remote import RemoteWaiterService, _cancel_when_signalled, create_server


class FakeAgent:
    def create_session(self, *, session_id=None):
        return AgentSession(session_id=session_id)

    async def run(self, messages, *, session, options):
        del messages, session, options
        return SimpleNamespace(
            value=WaiterModelResult(
                reply="Tomo nota.",
                customer=CustomerSnapshot(
                    presented_name="Otro nombre",
                    party_size=2,
                ),
                order_draft=OrderDraft(),
                memory_candidates=[
                    MemoryCandidate(
                        kind=MemoryKind.PREFERENCE,
                        value="agua con gas",
                    )
                ],
                memory_intent=MemoryIntent.NONE,
            )
        )


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_model_deployment_name="gpt-5.6-luna",
        memory_database_path=tmp_path / "unused.db",
    )


async def test_remote_service_preserves_the_application_context(tmp_path) -> None:
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = RemoteWaiterService(
        _settings(tmp_path),
        agent_factory=lambda settings, memory_store: FakeAgent(),
        memory_store=memory_store,
    )
    request = WaiterTurnRequest(
        conversation_id="conv_1",
        actor=ActorContext(actor_id="ana", authenticated=True),
        presented_name="Ana",
        message="Somos dos y prefiero agua con gas",
        customer=CustomerSnapshot(presented_name="Ana"),
        order_draft=OrderDraft(),
        turn_count=0,
        correlation_id="corr_1",
        visit_id="visit_1",
    )

    result = await service.take_turn(request)

    assert isinstance(result, WaiterTurnSuccess)
    assert result.customer.presented_name == "Ana"
    assert result.customer.party_size == 2
    assert result.turn_count == 1
    assert [(item.kind, item.value) for item in memory_store.list_memories("ana")] == [
        (MemoryKind.PREFERENCE, "agua con gas")
    ]
    assert '"visit_id": "visit_1"' in result.session_json


async def test_remote_service_manages_memory_without_the_bff(tmp_path) -> None:
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db")
    memory = memory_store.remember_memory(
        "ana",
        kind=MemoryKind.PREFERENCE,
        value="agua con gas",
        source_conversation_id="conv_old",
    )
    service = RemoteWaiterService(
        _settings(tmp_path),
        agent_factory=lambda settings, memory_store: FakeAgent(),
        memory_store=memory_store,
    )

    def request(message: str) -> WaiterTurnRequest:
        return WaiterTurnRequest(
            conversation_id="conv_1",
            actor=ActorContext(actor_id="ana", authenticated=True),
            presented_name="Ana",
            message=message,
            customer=CustomerSnapshot(presented_name="Ana"),
            order_draft=OrderDraft(),
            turn_count=0,
            correlation_id="corr_1",
        )

    listed = await service.take_turn(request("/memory"))
    assert memory.preference_id in listed.reply
    corrected = await service.take_turn(
        request(f"/memory correct {memory.preference_id} agua sin gas")
    )
    assert corrected.reply == "He corregido ese recuerdo."
    assert memory_store.list_memories("ana")[0].value == "agua sin gas"
    cleared = await service.take_turn(request("/memory clear"))
    assert cleared.reply.startswith("He olvidado")
    assert memory_store.list_memories("ana") == []


def test_responses_endpoint_returns_the_typed_result(tmp_path, monkeypatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    class StubService:
        async def take_turn(self, request):
            return WaiterTurnSuccess(
                reply=f"Hola, {request.presented_name}",
                customer=request.customer,
                order_draft=request.order_draft,
                turn_count=1,
            )

    server = create_server(_settings(tmp_path), service=StubService())
    request = WaiterTurnRequest(
        conversation_id="conv_1",
        actor=ActorContext(actor_id="ana", authenticated=True),
        presented_name="Ana",
        message="Hola",
        customer=CustomerSnapshot(presented_name="Ana"),
        order_draft=OrderDraft(),
        turn_count=0,
        correlation_id="corr_1",
    )

    with TestClient(server) as client:
        response = client.post(
            "/responses",
            json={
                "model": "restaurant",
                "input": request.model_dump_json(),
                "conversation": {"id": "conv_1"},
                "store": False,
                "stream": False,
            },
            headers={"x-agent-user-id": "ana"},
        )

    assert response.status_code == 200
    body = response.json()
    output = body["output"][0]["content"][0]["text"]
    assert WaiterTurnSuccess.model_validate_json(output).reply == "Hola, Ana"


async def test_the_waiter_serves_cooked_dishes_without_the_model(tmp_path) -> None:
    def no_model(*args, **kwargs):
        raise AssertionError("Serving never calls the model")

    service = RemoteWaiterService(
        _settings(tmp_path),
        agent_factory=no_model,
        memory_store=SQLiteMemoryStore(tmp_path / "memory.db"),
    )
    served = await service.serve_order(
        WaiterServeRequest(
            conversation_id="conv_1",
            actor=ActorContext(actor_id="ana", authenticated=True),
            correlation_id="corr_1",
            order_id="ko_1",
            dishes=["1 × Morcilla de Burgos a la brasa", "2 × Croquetas de morcilla"],
        )
    )
    assert served.order_id == "ko_1"
    assert served.reply == (
        "Aquí tenéis, recién salido de cocina: 1 × Morcilla de Burgos a la brasa "
        "y 2 × Croquetas de morcilla. ¡Que aproveche!"
    )


async def test_cancellation_signal_cancels_an_in_flight_turn() -> None:
    signal = asyncio.Event()
    operation_cancelled = asyncio.Event()

    async def block() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            operation_cancelled.set()

    task = asyncio.create_task(_cancel_when_signalled(block(), signal))
    await asyncio.sleep(0)
    signal.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert operation_cancelled.is_set()
