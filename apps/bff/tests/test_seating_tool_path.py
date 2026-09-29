"""Regression for the rejected proposal that came back in the waiter's words.

The real waiter tool path (Agent, MCPStreamableHTTPTool, seating middleware)
runs against an MCP server that shares its state with the BFF's seating
gateway. A scripted chat client plays the model and records what it is given.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    MCPStreamableHTTPTool,
)

from restaurant_agent.memory.store import SQLiteMemoryStore
from restaurant_agent.seating import SeatingToolContextMiddleware, VisitContextProvider

from bff.service import RestaurantService
from bff.storage import Database
from bff.waiter import LocalWaiter
from seating_fake import FakeSeatingGateway
from seating_tool_server import SeatingToolServer


class ScriptedModel(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Plays one scripted step per turn and records what the model receives."""

    def __init__(self) -> None:
        super().__init__()
        self.script: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self._current: dict[str, Any] = {}

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def respond() -> ChatResponse:
            last_user = max(i for i, m in enumerate(messages) if m.role == "user")
            answered = any(
                content.type == "function_result"
                for message in messages[last_user:]
                for content in message.contents
            )
            if not answered:
                self._current = self.script.pop(0)
                self.turns.append(
                    {
                        "history": [(m.role, m.text) for m in messages[:last_user]],
                        "instructions": str(options.get("instructions")),
                    }
                )
                if "hold" in self._current:
                    call = Content.from_function_call(
                        call_id=f"call_{len(self.turns)}",
                        name="seating_hold_seating",
                        arguments=self._current["hold"],
                    )
                    return ChatResponse(messages=[Message(role="assistant", contents=[call])])
            result = {
                "reply": self._current["reply"],
                "customer": {"presented_name": None, "party_size": self._current.get("party", 1)},
                "order_draft": {"items": []},
                "memory_candidates": [],
                "memory_intent": "none",
                "remembered_memories": [],
            }
            return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(result)])])

        return respond()


@pytest.fixture
def world(tmp_path, clock):
    seating = FakeSeatingGateway(clock)
    server = SeatingToolServer(seating)
    server.start()
    model = ScriptedModel()
    agent = Agent(
        client=model,
        instructions="Camarero de prueba.",
        tools=[
            MCPStreamableHTTPTool(
                name="seating",
                url=server.url,
                tool_name_prefix="seating",
                allowed_tools=("get_seating_availability", "hold_seating"),
                request_timeout=5,
            )
        ],
        context_providers=[VisitContextProvider()],
        middleware=[SeatingToolContextMiddleware()],
        default_options={"store": False},
    )
    memory = SQLiteMemoryStore(tmp_path / "memory.db")
    service = RestaurantService(
        database=Database(tmp_path / "bff.db"),
        memory_store=memory,
        waiter=LocalWaiter(agent, mode="foundry", max_turns=20, memory_store=memory),
        clock=clock,
        seating=seating,
        room_cache_seconds=0,
    )
    yield service, model, server, seating
    server.stop()


async def _enter(service, commands, name):
    session = service.authenticate(service.open_session(name).token)
    result = await service.submit(session, commands.arrive())
    return session, result.conversation_id


async def _say(service, commands, session, conversation_id, text):
    pending = await service.submit(session, commands.say(conversation_id, text))
    await service.drain()
    return service.get_result(session, pending.event_id)


async def _occupy(seating, visit_id, size):
    held = await seating.hold(visit_id=visit_id, party_size=size, preference="table", idempotency_key=visit_id)
    await seating.confirm(assignment_id=held.assignment_id, visit_id=visit_id, expected_version=1, idempotency_key=f"c-{visit_id}")
    return held


async def test_a_rejected_proposal_reaches_the_model_on_the_next_turn(world, commands) -> None:
    service, model, server, seating = world
    await _occupy(seating, "visit_ana", 4)  # Ana at Mesa 3
    marta, conversation_id = await _enter(service, commands, "Marta")

    model.script.append({"hold": {"party_size": 4, "preference": "any"}, "reply": "Os propongo la Mesa 4.", "party": 4})
    assert (await _say(service, commands, marta, conversation_id, "Somos cuatro")).status == "completed"
    proposal = service.get_snapshot(marta, conversation_id).seating.proposal
    assert proposal.place.label == "Mesa 4"
    first_key = server.holds[-1][0]["idempotency_key"]

    decided = await service.submit(marta, commands.decide(conversation_id, proposal.proposal_id, decision="rejected"))
    assert decided.status == "completed"
    pablo = await _occupy(seating, "visit_pablo", 4)
    assert pablo.resource_id == "table-04"

    model.script.append({"hold": {"party_size": 4, "preference": "any"}, "reply": "Os propongo la Mesa 5.", "party": 4})
    assert (await _say(service, commands, marta, conversation_id, "¿Tenéis otra mesa?")).status == "completed"

    seen = model.turns[-1]
    # The customer's conversation and the model's now agree.
    assert ("assistant", "Sin problema, dejo libre la Mesa 4. ¿Preferís otro sitio?") in seen["history"]
    assert '"last_outcome": {"decision": "rejected", "place": "Mesa 4"}' in seen["instructions"]
    assert "Ya no existe: no la presentes como pendiente" in seen["instructions"]
    # The tool call was never the problem: a fresh key and a fresh hold.
    request, result = server.holds[-1]
    assert request["idempotency_key"] != first_key
    assert (result["resource_id"], result["status"]) == ("table-05", "held")
    after = service.get_snapshot(marta, conversation_id)
    assert after.seating.proposal.place.label == "Mesa 5"


async def test_a_waiter_answering_from_history_shows_no_card(world, commands) -> None:
    """The symptom seen in the Codespace: a reply about Mesa 4 without a hold."""

    service, model, server, seating = world
    marta, conversation_id = await _enter(service, commands, "Marta")
    model.script.append({"hold": {"party_size": 4, "preference": "any"}, "reply": "Os propongo la Mesa 3.", "party": 4})
    await _say(service, commands, marta, conversation_id, "Somos cuatro")
    proposal = service.get_snapshot(marta, conversation_id).seating.proposal
    await service.submit(marta, commands.decide(conversation_id, proposal.proposal_id, decision="rejected"))

    model.script.append({"reply": "Tienes la Mesa 3 propuesta; confírmala con los botones.", "party": 4})
    await _say(service, commands, marta, conversation_id, "dame el botón de confirmar")

    snapshot = service.get_snapshot(marta, conversation_id)
    assert snapshot.seating.status == "none"
    assert len(server.holds) == 1
    assert '"status": "none"' in model.turns[-1]["instructions"]
