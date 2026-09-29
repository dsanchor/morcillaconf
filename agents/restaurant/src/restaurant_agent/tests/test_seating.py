import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.seating import (
    SeatingToolContextMiddleware,
    VisitContextProvider,
)


@pytest.mark.asyncio
async def test_visit_context_is_created_once_per_session() -> None:
    provider = VisitContextProvider()
    session = SimpleNamespace(state={})

    await provider.before_run(
        agent=object(),
        session=session,
        context=SimpleNamespace(),
        state={},
    )
    first_visit = session.state["visit_context"]["visit_id"]

    await provider.before_run(
        agent=object(),
        session=session,
        context=SimpleNamespace(),
        state={},
    )

    assert session.state["visit_context"]["visit_id"] == first_visit


@pytest.mark.asyncio
async def test_hold_seating_uses_session_values_and_persists_proposal() -> None:
    session = SimpleNamespace(
        state={
            "visit_context": {
                "visit_id": "visit-1",
                "hold_sequence": 0,
                "hold_requests": {},
            }
        }
    )
    context = SimpleNamespace(
        function=SimpleNamespace(name="seating_hold_seating"),
        session=session,
        arguments={
            "party_size": 2,
            "preference": "table",
            "visit_id": "model-value",
            "idempotency_key": "model-value",
        },
        result=None,
    )

    async def call_next() -> None:
        assert context.arguments["visit_id"] == "visit-1"
        assert context.arguments["idempotency_key"] == "seating:visit-1:1"
        context.result = json.dumps(
            {
                "assignment_id": "seat-1",
                "resource_id": "table-01",
                "resource_kind": "table",
                "party_size": 2,
                "version": 1,
                "expires_at": "2026-09-28T18:30:00+00:00",
            }
        )

    await SeatingToolContextMiddleware().process(context, call_next)

    assert session.state["seating_proposal"]["assignment_id"] == "seat-1"


@pytest.mark.asyncio
async def test_same_hold_request_reuses_idempotency_key_while_pending() -> None:
    session = SimpleNamespace(
        state={
            "visit_context": {
                "visit_id": "visit-1",
                "hold_sequence": 1,
                "hold_requests": {"2:table": "seating:visit-1:1"},
            },
            "seating_proposal": {
                "assignment_id": "seat-1",
                "resource_id": "table-01",
                "resource_kind": "table",
                "party_size": 2,
                "version": 1,
                "expires_at": "2026-09-28T18:30:00+00:00",
                "idempotency_key": "seating:visit-1:1",
                "fingerprint": "2:table",
            },
        }
    )
    context = SimpleNamespace(
        function=SimpleNamespace(name="seating_hold_seating"),
        session=session,
        arguments={"party_size": 2, "preference": "table"},
        result=None,
    )

    async def call_next() -> None:
        assert context.arguments["idempotency_key"] == "seating:visit-1:1"

    await SeatingToolContextMiddleware().process(context, call_next)


def test_agent_exposes_only_hold_and_availability_mcp_tools() -> None:
    settings = Settings(
        foundry_project_endpoint=(
            "https://example.services.ai.azure.com/api/projects/demo"
        ),
        azure_ai_model_deployment_name="test-model",
        memory_database_path=Path("/tmp/memory.db"),
        seating_mcp_url="http://localhost:8080/mcp",
        _env_file=None,
    )

    agent = create_waiter_agent(settings)

    assert len(agent.mcp_tools) == 1
    assert agent.mcp_tools[0].allowed_tools == (
        "get_seating_availability",
        "hold_seating",
    )
