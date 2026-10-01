"""The standalone waiter's three operations, with its own MCP connection.

RemoteWaiterService builds the real waiter (MCPStreamableHTTPTool with
approval, provider and middleware) with a scripted model and a Streamable HTTP
MCP stub; session JSON round-trips between requests as it does via the BFF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import (
    WAITER_REQUEST_ADAPTER,
    WaiterSeatingSuccess,
    WaiterTurnFailure,
    WaiterTurnRequest,
    WaiterTurnSuccess,
)

from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.remote import RemoteWaiterService, create_server
from scripted_model import ScriptedModel, hold, say


def _settings(url: str) -> Settings:
    return Settings(
        _env_file=None,
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_model_deployment_name="test-model",
        memory_database_path=Path("/tmp/unused-memory.db"),
        seating_mcp_url=url,
        seating_mcp_timeout_seconds=3,
    )


@pytest.fixture
def remote(seating_server):
    model = ScriptedModel()

    def factory(settings, memory_store):
        return create_waiter_agent(settings, memory_store=memory_store, client=model)

    service = RemoteWaiterService(_settings(seating_server.url), agent_factory=factory)
    return service, model


def _turn(message: str, session_json: str | None, turn_count: int = 0) -> WaiterTurnRequest:
    return WaiterTurnRequest(
        conversation_id="conv_1",
        actor=ActorContext(actor_id="ana", authenticated=True),
        presented_name="Ana",
        message=message,
        customer=CustomerSnapshot(presented_name="Ana"),
        order_draft=OrderDraft(),
        turn_count=turn_count,
        session_json=session_json,
        correlation_id="corr_1",
        visit_id="visit_ana",
    )


def _seating(operation: str, session_json: str | None, **fields):
    return WAITER_REQUEST_ADAPTER.validate_python(
        {
            "operation": operation,
            "conversation_id": "conv_1",
            "actor": {"actor_id": "ana", "authenticated": True},
            "presented_name": "Ana",
            "session_json": session_json,
            "correlation_id": "corr_2",
            "visit_id": "visit_ana",
            **fields,
        }
    )


async def test_a_turn_pauses_and_a_decision_confirms_through_the_agent(remote, seating_server) -> None:
    service, model = remote
    model.script += [hold(3), say("Os propongo la Mesa 3.", 3)]

    turn = await service.take_turn(_turn("Venimos tres", None))

    assert isinstance(turn, WaiterTurnSuccess)
    report = turn.seating
    assert (report.status, report.awaiting_decision, report.place.label) == ("proposed", True, "Mesa 3")
    assert "seat_1" not in turn.model_dump_json(exclude={"session_json"})

    stale = await service.decide_seating(
        _seating("decide_seating", turn.session_json, decision="confirmed", proposal_token="other")
    )
    assert isinstance(stale, WaiterTurnFailure) and stale.code == "no_pending_decision"

    decided = await service.decide_seating(
        _seating("decide_seating", turn.session_json, decision="confirmed", proposal_token=report.token)
    )

    assert isinstance(decided, WaiterSeatingSuccess)
    assert (decided.outcome, decided.reply) == (
        "confirmed",
        "¡Estupendo! Os acompaño a la Mesa 3. ¿Qué queréis tomar?",
    )
    assert decided.seating.status == "seated" and decided.seating.awaiting_decision is False
    assert [name for name, _ in seating_server.stub.calls].count("confirm_seating") == 1
    assert len(model.calls) == 2


async def test_a_rejection_cancels_the_hold_through_the_agent(remote, seating_server) -> None:
    service, model = remote
    model.script += [hold(3), say("Os propongo la Mesa 3.", 3)]
    turn = await service.take_turn(_turn("Venimos tres", None))

    decided = await service.decide_seating(
        _seating("decide_seating", turn.session_json, decision="rejected", proposal_token=turn.seating.token)
    )

    assert decided.outcome == "rejected"
    assert decided.seating.status == "none"
    assert decided.seating.last_outcome.decision == "rejected"
    assert seating_server.stub.assignments["seat_1"]["status"] == "cancelled"


async def test_a_sync_reads_the_room_without_the_model(remote, seating_server) -> None:
    service, model = remote
    synced = await service.sync_seating(_seating("sync_seating", None))

    assert isinstance(synced, WaiterSeatingSuccess)
    assert synced.seating.status == "none"
    assert {place.place_id for place in synced.seating.room} == {"table-01", "table-03"}
    assert '"visit_id": "visit_ana"' in synced.session_json
    assert model.calls == []


def test_the_endpoint_routes_every_operation(tmp_path, monkeypatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    seen = []

    class StubService:
        async def take_turn(self, request):
            seen.append(request.operation)
            return WaiterTurnSuccess(
                reply="Hola", customer=request.customer, order_draft=request.order_draft, turn_count=1
            )

        async def decide_seating(self, request):
            seen.append((request.operation, request.decision))
            return WaiterSeatingSuccess(operation="decide_seating", reply="Vale", outcome="rejected")

        async def sync_seating(self, request):
            seen.append(request.operation)
            return WaiterSeatingSuccess(operation="sync_seating")

    server = create_server(_settings("http://127.0.0.1:1/mcp"), service=StubService())
    payloads = [
        _turn("Hola", None).model_dump_json(),
        _seating("decide_seating", None, decision="rejected", proposal_token="t").model_dump_json(),
        _seating("sync_seating", None).model_dump_json(),
    ]
    with TestClient(server) as client:
        for payload in payloads:
            response = client.post(
                "/responses",
                json={"model": "restaurant", "input": payload, "conversation": {"id": "conv_1"}, "store": False, "stream": False},
                headers={"x-agent-user-id": "ana"},
            )
            assert response.status_code == 200
        forged = client.post(
            "/responses",
            json={"model": "restaurant", "input": payloads[1], "conversation": {"id": "conv_1"}, "store": False, "stream": False},
            headers={"x-agent-user-id": "luis"},
        )
    assert seen == ["take_turn", ("decide_seating", "rejected"), "sync_seating"]
    text = forged.json()["output"][0]["content"][0]["text"]
    assert WaiterTurnFailure.model_validate_json(text).code == "internal_error"
