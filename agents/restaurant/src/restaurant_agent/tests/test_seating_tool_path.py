"""The real seating tool path without Foundry.

A scripted Agent Framework chat client asks for ``seating_hold_seating``; the
call goes through MCPStreamableHTTPTool, the seating middleware and a real
Streamable HTTP MCP server, and the conversation keeps the proposal.
"""

import asyncio
import json
from pathlib import Path
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
)

from restaurant_agent.agent import create_seating_tools
from restaurant_agent.config import Settings
from restaurant_agent.conversation import ConversationManager, SeatingUnavailableError
from restaurant_agent.seating import (
    SeatingToolContextMiddleware,
    VisitContextProvider,
    bind_visit,
    pending_proposal,
    set_seating_context,
)
from seating_stub import free_port

FINAL = {
    "reply": "Os propongo la mesa 3. Confirmadla con el botón.",
    "customer": {"presented_name": "Ana", "party_size": 3, "preferences": [], "restrictions": []},
    "order_draft": {"items": []},
    "memory_candidates": [],
    "memory_intent": "none",
    "remembered_memories": [],
}


class ScriptedChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Asks for one hold, then answers with the structured waiter result."""

    def __init__(self, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.arguments = arguments
        self.requests: list[tuple[list[Message], dict[str, Any]]] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def respond() -> ChatResponse:
            self.requests.append((list(messages), dict(options)))
            answered = [
                content
                for message in messages
                for content in message.contents
                if content.type == "function_result"
            ]
            if not answered:
                call = Content.from_function_call(
                    call_id="call_1", name="seating_hold_seating", arguments=self.arguments
                )
                return ChatResponse(messages=[Message(role="assistant", contents=[call])])
            return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(FINAL)])])

        return respond()


def _agent(client: ScriptedChatClient, url: str) -> Agent:
    settings = Settings(
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_model_deployment_name="test-model",
        memory_database_path=Path("/tmp/unused-memory.db"),
        seating_mcp_url=url,
        seating_mcp_timeout_seconds=3,
        _env_file=None,
    )
    return Agent(
        client=client,
        instructions="Camarero de prueba.",
        tools=create_seating_tools(settings),
        context_providers=[VisitContextProvider()],
        middleware=[SeatingToolContextMiddleware()],
        default_options={"store": False},
    )


async def _turn(agent: Agent, session: Any, message: str = "Venimos tres") -> Any:
    manager = ConversationManager(agent)
    manager.restore_conversation(
        conversation_id="conv_1", actor_id="ana", presented_name="Ana", agent_session=session
    )
    return await manager.send_message(conversation_id="conv_1", actor_id="ana", message=message)


async def test_a_model_hold_goes_through_the_middleware_to_the_mcp(seating_server) -> None:
    client = ScriptedChatClient({"party_size": 3, "preference": "any", "visit_id": "forged"})
    agent = _agent(client, seating_server.url)
    session = agent.create_session(session_id="conv_1")
    bind_visit(session.state, "visit_bff")
    set_seating_context(session.state, {"status": "none"})
    try:
        response = await _turn(agent, session)
    finally:
        await agent.__aexit__(None, None, None)

    assert response.reply.startswith("Os propongo")
    name, arguments = next(call for call in seating_server.stub.calls if call[0] == "hold_seating")
    assert arguments["idempotency_key"].startswith("seating:visit_bff:1-")
    assert arguments | {"idempotency_key": "k"} == {
        "party_size": 3,
        "preference": "any",
        "visit_id": "visit_bff",
        "idempotency_key": "k",
    }
    proposal = pending_proposal(session.state)
    assert (proposal["resource_id"], proposal["resource_label"], proposal["version"]) == ("table-03", "Mesa 3", 1)
    instructions = json.dumps([str(options.get("instructions")) for _, options in client.requests], ensure_ascii=False)
    assert "Estado de asiento" in instructions
    tool_names = {
        getattr(tool, "name", None)
        for _, options in client.requests
        for tool in options.get("tools") or []
    }
    assert {"seating_hold_seating", "seating_get_seating_availability"} <= tool_names
    assert not {"seating_confirm_seating", "seating_cancel_seating_hold", "seating_get_seating_map"} & tool_names


async def test_the_tool_reconnects_after_an_mcp_restart(seating_server) -> None:
    client = ScriptedChatClient({"party_size": 2, "preference": "any"})
    agent = _agent(client, seating_server.url)
    try:
        first = agent.create_session(session_id="conv_1")
        bind_visit(first.state, "visit_1")
        await _turn(agent, first, "Somos dos")
        seating_server.stop()
        seating_server.start()
        second = agent.create_session(session_id="conv_1")
        bind_visit(second.state, "visit_2")
        await _turn(agent, second, "Somos dos")
    finally:
        await agent.__aexit__(None, None, None)
    visits = [arguments["visit_id"] for name, arguments in seating_server.stub.calls if name == "hold_seating"]
    assert visits == ["visit_1", "visit_2"]


async def test_an_unreachable_mcp_fails_the_turn_quickly() -> None:
    client = ScriptedChatClient({"party_size": 2, "preference": "any"})
    agent = _agent(client, f"http://127.0.0.1:{free_port()}/mcp")
    session = agent.create_session(session_id="conv_1")
    try:
        with pytest.raises(SeatingUnavailableError):
            await asyncio.wait_for(_turn(agent, session), timeout=20)
    finally:
        await agent.__aexit__(None, None, None)
