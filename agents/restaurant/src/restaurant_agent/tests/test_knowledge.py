"""The waiter's knowledge base tool: configuration, auth, outages and results.

The waiter built by create_waiter_agent runs with a scripted model against a
Streamable HTTP stub of the knowledge base MCP endpoint and the seating stub.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from azure.core.credentials import AccessToken
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

import restaurant_agent.knowledge as knowledge
from knowledge_stub import KB_NAME, StubKnowledge, build_server
from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.config import Settings
from restaurant_agent.conversation import ConversationManager
from restaurant_agent.knowledge import (
    KNOWLEDGE_TOOL,
    UNAVAILABLE,
    KnowledgeBaseTool,
    SearchToken,
    SearchTokenAuth,
    create_knowledge_tool,
    knowledge_base_mcp_url,
    summarize_retrieval,
)
from restaurant_agent.remote import RemoteWaiterService
from restaurant_agent.seating import bind_visit, pending_confirm_request
from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import WAITER_REQUEST_ADAPTER, WaiterTurnRequest
from scripted_model import ScriptedModel, hold, say
from seating_stub import RunningServer, free_port

QUESTION = "¿Qué tenéis típico de Burgos?"


class FakeToken:
    def get(self) -> str:
        return "test-token"


class CountingCredential:
    def __init__(self, lifetime: int) -> None:
        self.lifetime = lifetime
        self.scopes: list[str] = []

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        self.scopes.extend(scopes)
        return AccessToken(f"token-{len(self.scopes)}", int(time.time()) + self.lifetime)


@pytest.fixture(autouse=True)
def fake_search_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "search_token", lambda settings: FakeToken())


@pytest.fixture(autouse=True)
def no_recorded_outages():
    knowledge._OUTAGES.clear()
    yield
    knowledge._OUTAGES.clear()


class SilentServer:
    """Accepts TCP connections and never answers, like a stalled endpoint."""

    def __init__(self) -> None:
        self._socket = socket.create_server(("127.0.0.1", 0))
        self._socket.settimeout(0.1)
        self.port = self._socket.getsockname()[1]
        self.connections: list[socket.socket] = []
        self._running = True
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while self._running:
            try:
                self.connections.append(self._socket.accept()[0])
            except OSError:
                continue

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=5)
        for connection in self.connections:
            connection.close()
        self._socket.close()


@pytest.fixture
def silent_server():
    server = SilentServer()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def knowledge_server():
    server = RunningServer(StubKnowledge(), builder=build_server)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "azure_ai_model_deployment_name": "test-model",
        "memory_database_path": Path("/tmp/unused-memory.db"),
    }
    values.update(overrides)
    return Settings(**values)


def with_knowledge(port: int, seating_url: str | None = None, **overrides: object) -> Settings:
    return settings(
        azure_search_endpoint=f"http://127.0.0.1:{port}",
        knowledge_base_name=KB_NAME,
        knowledge_base_timeout_seconds=5,
        seating_mcp_url=seating_url,
        seating_mcp_timeout_seconds=3,
        **overrides,
    )


def ask_knowledge(call_id: str = "call_kb", question: str = QUESTION) -> dict:
    return {"calls": [(call_id, KNOWLEDGE_TOOL, {"query_variants": [question]})]}


def tool_results(model: ScriptedModel, call: int) -> list[str]:
    return [
        text
        for _, contents in model.calls[call]["messages"]
        for kind, _, _, text in contents
        if kind == "function_result"
    ]


class Waiter:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.session = agent.create_session(session_id="conv_1")
        bind_visit(self.session.state, "visit_ana")
        self.manager = ConversationManager(agent)
        self.manager.restore_conversation(
            conversation_id="conv_1", actor_id="ana", presented_name="Ana", agent_session=self.session
        )

    async def say(self, text: str):
        return await self.manager.send_message(conversation_id="conv_1", actor_id="ana", message=text)

    async def close(self) -> None:
        await self.agent.__aexit__(None, None, None)
        for tool in self.agent.mcp_tools:
            if tool.is_connected:
                await tool.close()


@pytest.fixture
async def waiter():
    """Request it after the stub servers, so it closes its connections first."""

    created: list[Waiter] = []

    def make(configured: Settings, script: list[dict]) -> tuple[Waiter, ScriptedModel]:
        model = ScriptedModel(script)
        created.append(Waiter(create_waiter_agent(configured, client=model)))
        return created[-1], model

    yield make
    for item in created:
        await item.close()


# Configuration


def test_the_knowledge_base_is_optional() -> None:
    configured = settings()
    assert knowledge_base_mcp_url(configured) is None
    assert create_knowledge_tool(configured) is None
    assert not create_waiter_agent(configured, client=ScriptedModel()).mcp_tools


def test_empty_values_leave_it_off() -> None:
    assert create_knowledge_tool(settings(azure_search_endpoint="", knowledge_base_name=" ")) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"azure_search_endpoint": "https://srch-demo.search.windows.net"},
        {"knowledge_base_name": "conocimiento-restaurante"},
        {"azure_search_endpoint": "https://srch-demo.search.windows.net", "knowledge_base_name": "Carta/../x"},
        {
            "azure_search_endpoint": "https://srch-demo.search.windows.net",
            "knowledge_base_name": "conocimiento-restaurante",
            "knowledge_base_timeout_seconds": 0,
        },
    ],
)
def test_incomplete_or_invalid_knowledge_settings_are_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_the_mcp_endpoint_is_the_knowledge_base_preview_endpoint() -> None:
    configured = settings(
        azure_search_endpoint="https://srch-demo.search.windows.net/",
        knowledge_base_name="conocimiento-restaurante",
    )
    assert knowledge_base_mcp_url(configured) == (
        "https://srch-demo.search.windows.net/knowledgebases/conocimiento-restaurante/mcp"
        "?api-version=2026-08-01-preview"
    )


async def test_the_tool_is_read_only_never_asks_for_approval_and_is_bounded() -> None:
    tool = create_knowledge_tool(
        settings(
            azure_search_endpoint="https://srch-demo.search.windows.net",
            knowledge_base_name="conocimiento-restaurante",
            knowledge_base_timeout_seconds=12,
        )
    )
    try:
        assert isinstance(tool, KnowledgeBaseTool)
        assert tuple(tool.allowed_tools) == (KNOWLEDGE_TOOL,)
        assert tool.approval_mode == "never_require"
        assert tool.tool_name_prefix is None
        assert tool.request_timeout == 12
        assert tool.load_prompts_flag is False
        timeout = tool._http.timeout
        assert (timeout.connect, timeout.read) == (5, 17)
        assert tool._http.follow_redirects is False
    finally:
        await tool.close()


# Auth


async def test_the_bearer_token_only_goes_to_the_search_host() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.headers.get("authorization")
        return httpx.Response(200)

    auth = SearchTokenAuth(FakeToken(), "srch-demo.search.windows.net")
    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(handler)) as client:
        await client.get("https://srch-demo.search.windows.net/knowledgebases")
        await client.get("https://example.com/elsewhere")

    assert seen == {"srch-demo.search.windows.net": "Bearer test-token", "example.com": None}


def test_the_search_token_is_cached_until_it_is_about_to_expire() -> None:
    credential = CountingCredential(lifetime=3600)
    token = SearchToken(credential)
    assert token.get() == token.get() == "token-1"
    assert credential.scopes == ["https://search.azure.com/.default"]

    expiring = SearchToken(CountingCredential(lifetime=60))
    assert expiring.get() != expiring.get()


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("production", ("managed", "client-123")), ("development", ("default", None))],
)
def test_azure_uses_the_managed_identity_and_elsewhere_the_developer_login(
    monkeypatch: pytest.MonkeyPatch, environment: str, expected: tuple
) -> None:
    import azure.identity

    monkeypatch.undo()  # the real search_token, with fake credential classes
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", lambda client_id: ("managed", client_id))
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda: ("default", None))
    knowledge._process_token.cache_clear()
    try:
        token = knowledge.search_token(settings(app_environment=environment))
        assert token._credential == expected
    finally:
        knowledge._process_token.cache_clear()


# The waiter with the knowledge base


async def test_the_waiter_consults_the_knowledge_base_with_its_token(knowledge_server, waiter) -> None:
    ana, model = waiter(
        with_knowledge(knowledge_server.port),
        [ask_knowledge(), say("Tenemos morcilla de Burgos a la brasa. Fuente: carta de la casa, versión 1.")],
    )

    response = await ana.say(QUESTION)

    assert response.reply == "Tenemos morcilla de Burgos a la brasa. Fuente: carta de la casa, versión 1."
    assert knowledge_server.stub.queries == [[QUESTION]]
    assert knowledge_server.stub.authorizations == ["Bearer test-token"]
    assert KNOWLEDGE_TOOL in model.calls[0]["tools"]
    [result] = tool_results(model, 1)
    assert result.startswith("[0] Origen: documento de la casa: carta.md\n### morcilla-de-burgos-a-la-brasa")
    assert result.count("Precio: 8,50 €") == 1  # the duplicated snippet is dropped


async def test_a_failed_retrieval_is_reported_instead_of_invented(knowledge_server, waiter) -> None:
    knowledge_server.stub.fail = True
    ana, model = waiter(
        with_knowledge(knowledge_server.port),
        [ask_knowledge(), say("Ahora no puedo consultar la carta.")],
    )

    response = await ana.say(QUESTION)

    assert response.reply == "Ahora no puedo consultar la carta."
    assert tool_results(model, 1) == [UNAVAILABLE]


async def test_an_unreachable_knowledge_base_does_not_block_the_turn(waiter) -> None:
    ana, model = waiter(
        with_knowledge(free_port()),
        [ask_knowledge(), say("Ahora no puedo consultar la carta.")],
    )

    response = await ana.say(QUESTION)

    assert response.reply == "Ahora no puedo consultar la carta."
    assert KNOWLEDGE_TOOL in model.calls[0]["tools"]
    assert tool_results(model, 1) == [UNAVAILABLE]
    [tool] = [tool for tool in ana.agent.mcp_tools if isinstance(tool, KnowledgeBaseTool)]
    assert tool.unavailable


async def test_an_outage_pauses_every_tool_of_the_process_before_retrying() -> None:
    # The remote waiter builds a new tool per request: the pause must be shared.
    port = free_port()
    url = knowledge_base_mcp_url(with_knowledge(port))
    clock = [1000.0]

    def new_tool() -> KnowledgeBaseTool:
        return KnowledgeBaseTool(url, token=FakeToken(), timeout_seconds=5, clock=lambda: clock[0])

    server = RunningServer(StubKnowledge(), port=port, builder=build_server)
    tools = [new_tool(), new_tool(), new_tool()]
    try:
        await tools[0].connect()
        assert tools[0].unavailable and [f.name for f in tools[0].functions] == [KNOWLEDGE_TOOL]

        server.start()
        clock[0] += knowledge.RETRY_AFTER_SECONDS - 1
        await tools[1].connect()
        assert tools[1].unavailable and not tools[1].is_connected

        clock[0] += 2
        await tools[2].connect()
        assert tools[2].is_connected and not tools[2].unavailable
        assert [f.name for f in tools[2].functions] == [KNOWLEDGE_TOOL]
        assert url not in knowledge._OUTAGES
    finally:
        for tool in tools:
            await tool.close()
        server.stop()


async def test_a_stalled_knowledge_base_costs_one_short_wait_not_one_per_turn(
    monkeypatch: pytest.MonkeyPatch, silent_server
) -> None:
    monkeypatch.setattr(knowledge, "CONNECT_TIMEOUT_SECONDS", 1)
    model = ScriptedModel([step for _ in range(3) for step in (ask_knowledge(), say("No puedo consultar la carta."))])

    def factory(configured: Settings, memory_store):
        return create_waiter_agent(configured, memory_store=memory_store, client=model)

    service = RemoteWaiterService(with_knowledge(silent_server.port), agent_factory=factory)
    durations = []
    for number in range(3):
        started = time.monotonic()
        turn = await service.take_turn(
            WaiterTurnRequest(
                conversation_id=f"conv_{number}",
                actor=ActorContext(actor_id="ana", authenticated=True),
                presented_name="Ana",
                message=QUESTION,
                customer=CustomerSnapshot(presented_name="Ana"),
                order_draft=OrderDraft(),
                turn_count=0,
                correlation_id=f"corr_{number}",
                visit_id=f"visit_{number}",
            )
        )
        durations.append(time.monotonic() - started)
        assert turn.status == "completed" and turn.reply == "No puedo consultar la carta."
        assert tool_results(model, 2 * number + 1) == [UNAVAILABLE]

    assert len(silent_server.connections) == 1
    assert durations[0] < 4  # the 1 s connect budget, not the 5 s retrieval timeout
    assert max(durations[1:]) < 1


async def test_a_retrieval_may_take_longer_than_the_connect_budget(
    monkeypatch: pytest.MonkeyPatch, knowledge_server
) -> None:
    monkeypatch.setattr(knowledge, "CONNECT_TIMEOUT_SECONDS", 1)
    knowledge_server.stub.delay_seconds = 1.5
    tool = create_knowledge_tool(with_knowledge(knowledge_server.port))
    try:
        await tool.connect()
        summary = await tool.call_tool(KNOWLEDGE_TOOL, query_variants=[QUESTION])
    finally:
        await tool.close()

    assert summary.startswith("[0] Origen: documento de la casa: carta.md")
    assert tool.request_timeout == 5


async def test_seating_keeps_its_own_tool_and_approval_next_to_the_knowledge_base(
    knowledge_server, seating_server, waiter
) -> None:
    ana, model = waiter(
        with_knowledge(knowledge_server.port, seating_server.url),
        [ask_knowledge(), hold(3), say("Tenemos morcilla; os propongo la Mesa 3.", 3)],
    )

    await ana.say("Venimos tres, ¿qué tenéis típico de Burgos?")

    report = ana.manager.seating_report(conversation_id="conv_1", actor_id="ana")
    assert (report["status"], report["awaiting_decision"]) == ("proposed", True)
    assert pending_confirm_request(ana.session.state) is not None
    assert {"seating_hold_seating", "seating_confirm_seating", KNOWLEDGE_TOOL} <= set(model.calls[0]["tools"])
    assert [name for name, _ in seating_server.stub.calls][:1] == ["get_seating_map"]


async def test_an_unreachable_knowledge_base_does_not_break_seating(seating_server, waiter) -> None:
    ana, _ = waiter(
        with_knowledge(free_port(), seating_server.url),
        [hold(2), say("Os propongo la Mesa 1.", 2)],
    )

    await ana.say("Somos dos")

    report = ana.manager.seating_report(conversation_id="conv_1", actor_id="ana")
    assert (report["status"], report["awaiting_decision"]) == ("proposed", True)


async def test_seating_decisions_and_syncs_do_not_connect_to_the_knowledge_base(seating_server) -> None:
    built: list[Settings] = []
    model = ScriptedModel([say("Hola, Ana.")])

    def factory(configured: Settings, memory_store):
        built.append(configured)
        return create_waiter_agent(configured, memory_store=memory_store, client=model)

    service = RemoteWaiterService(with_knowledge(free_port(), seating_server.url), agent_factory=factory)
    turn = await service.take_turn(
        WaiterTurnRequest(
            conversation_id="conv_1",
            actor=ActorContext(actor_id="ana", authenticated=True),
            presented_name="Ana",
            message="Hola",
            customer=CustomerSnapshot(presented_name="Ana"),
            order_draft=OrderDraft(),
            turn_count=0,
            correlation_id="corr_1",
            visit_id="visit_ana",
        )
    )
    assert turn.status == "completed"
    await service.sync_seating(
        WAITER_REQUEST_ADAPTER.validate_python(
            {
                "operation": "sync_seating",
                "conversation_id": "conv_1",
                "actor": {"actor_id": "ana", "authenticated": True},
                "presented_name": "Ana",
                "session_json": turn.session_json,
                "correlation_id": "corr_2",
                "visit_id": "visit_ana",
            }
        )
    )

    assert [knowledge_base_mcp_url(configured) is not None for configured in built] == [True, False]
    assert built[1].seating_mcp_url == built[0].seating_mcp_url


# Retrieval results


def result(*texts: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text) for text in texts])


def test_passages_are_labelled_as_house_documents_or_external_web() -> None:
    # Shapes returned by the knowledge base MCP endpoint (2026-08-01-preview)
    # for the blob, search index and web knowledge sources.
    passages = [
        {"ref_id": 0, "content": "- Fuente: carta de la casa, versión 1 (documento: carta)"},
        {"ref_id": 1, "title": "Recetario de la casa (recetario.pdf)", "content": "R01. Morcilla de Burgos"},
        {
            "ref_id": 2, "title": "IGP Morcilla de Burgos", "url": "https://igp.example.es/morcilla/",
            "content": "The protected geographical indication certifies the sausage.",
        },
        {"ref_id": 3, "content": "Sin referencia."},
    ]
    references = [
        {
            "kind": "reference", "ref_id": 0, "uri": "https://stdemo.blob.core.windows.net/carta/carta.md",
            "sourceData": {"uid": "u0", "blob_url": "https://stdemo.blob.core.windows.net/carta/carta.md",
                           "snippet": "- Fuente: carta de la casa, versión 1 (documento: carta)"},
        },
        {
            "kind": "reference", "ref_id": 1,
            "uri": "https://srch-demo.search.windows.net/indexes/recetario-index/docs/k1",
            "sourceData": {"chunk_id": "k1", "title": "Recetario de la casa (recetario.pdf)",
                           "doc_type": "recetario", "version": "1", "source_file": "recetario.pdf",
                           "content": "R01. Morcilla de Burgos"},
        },
        {"kind": "reference", "ref_id": 2, "uri": "https://igp.example.es/morcilla/",
         "sourceData": "IGP Morcilla de Burgos"},
    ]

    summary = summarize_retrieval(
        result(json.dumps(passages), *(json.dumps(reference) for reference in references))
    )

    assert summary == (
        "[0] Origen: documento de la casa: carta.md\n"
        "- Fuente: carta de la casa, versión 1 (documento: carta)\n\n"
        "[1] Origen: documento de la casa: Recetario de la casa (recetario.pdf), tipo recetario, versión 1\n"
        "R01. Morcilla de Burgos\n\n"
        "[2] Origen: fuente externa (web): IGP Morcilla de Burgos — https://igp.example.es/morcilla/\n"
        "The protected geographical indication certifies the sausage.\n\n"
        "[3] Origen: origen no indicado; trátalo como fuente externa\n"
        "Sin referencia."
    )


def test_an_empty_retrieval_says_there_is_no_information() -> None:
    assert summarize_retrieval(result("[]")).startswith("no_results:")
    assert summarize_retrieval(result()).startswith("no_results:")


def test_an_unexpected_result_format_is_kept_as_is() -> None:
    assert summarize_retrieval(result("La carta no está disponible.")) == "La carta no está disponible."
    odd = result(json.dumps([{"ref_id": 0, "content": "x"}]), json.dumps({"kind": "reference", "ref_id": 0, "sourceData": 7}))
    assert summarize_retrieval(odd).startswith("[0] Origen: fuente externa (web)")
