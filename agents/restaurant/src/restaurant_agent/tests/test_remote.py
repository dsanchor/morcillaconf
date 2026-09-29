import asyncio
from types import SimpleNamespace

import pytest
from agent_framework import AgentSession

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import WaiterTurnRequest, WaiterTurnSuccess

from restaurant_agent.config import Settings
from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.memory.contracts import MemoryCandidate, MemoryIntent, MemoryKind
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
    service = RemoteWaiterService(
        _settings(tmp_path),
        agent_factory=lambda settings, memory_store: FakeAgent(),
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
    assert [(item.kind, item.value) for item in result.memory_candidates] == [
        (MemoryKind.PREFERENCE, "agua con gas")
    ]
    assert '"visit_id": "visit_1"' in result.session_json


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
