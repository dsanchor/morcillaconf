"""The waiter's connection to the restaurant's Foundry IQ knowledge base.

The knowledge base (Azure AI Search agentic retrieval) exposes its own MCP
endpoint whose only tool, ``knowledge_base_retrieve``, queries the carta, the
recipe book with its ingredient sheet and, as an external fallback, the web.
The waiter connects to it as a second ``MCPStreamableHTTPTool`` next to the
seating one: read-only, never needs approval, Entra ID auth. The future chef
reuses the same endpoint.

An unreachable knowledge base never blocks a turn: the tool then answers that
it is unavailable, so the waiter says so instead of inventing a carta.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import threading
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, MutableMapping
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from agent_framework import (
    FunctionInvocationContext,
    FunctionMiddleware,
    FunctionTool,
    MCPStreamableHTTPTool,
)
from azure.core.credentials import AccessToken, TokenCredential
from mcp import types

from restaurant_agent.config import Settings

logger = logging.getLogger(__name__)

KNOWLEDGE_TOOL = "knowledge_base_retrieve"
# The knowledge base MCP endpoint only exists in this preview version;
# scripts/provision-knowledge.sh creates the knowledge base with the same one.
KNOWLEDGE_BASE_API_VERSION = "2026-08-01-preview"
SEARCH_SCOPE = "https://search.azure.com/.default"
CONNECT_TIMEOUT_SECONDS = 5
RETRY_AFTER_SECONDS = 30
TOKEN_REFRESH_MARGIN_SECONDS = 300

UNAVAILABLE = (
    "knowledge_unavailable: la base de conocimiento del restaurante no ha "
    "podido responder. Díselo al cliente con claridad, no respondas de memoria "
    "ni inventes platos, precios o alérgenos, y no vuelvas a consultarla en "
    "este turno."
)
NO_RESULTS = (
    "no_results: la base de conocimiento no tiene información sobre esto. "
    "Dilo sin inventar la respuesta."
)
HOUSE = "documento de la casa"
WEB = "fuente externa (web)"
UNKNOWN = "origen no indicado; trátalo como fuente externa"

# When each knowledge base last failed to connect. The remote waiter builds a
# new agent, and so a new tool, for every request: the pause is per process.
_OUTAGES: dict[str, float] = {}


def knowledge_base_mcp_url(settings: Settings) -> str | None:
    """The knowledge base's MCP endpoint, or None when it is not configured."""

    if settings.azure_search_endpoint is None or settings.knowledge_base_name is None:
        return None
    endpoint = str(settings.azure_search_endpoint).rstrip("/")
    return (
        f"{endpoint}/knowledgebases/{settings.knowledge_base_name}/mcp"
        f"?api-version={KNOWLEDGE_BASE_API_VERSION}"
    )


class SearchToken:
    """An Entra token for Azure AI Search, cached until shortly before it expires."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._token: AccessToken | None = None
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._token is None or self._token.expires_on - TOKEN_REFRESH_MARGIN_SECONDS < time.time():
                self._token = self._credential.get_token(SEARCH_SCOPE)
            return self._token.token


@functools.cache
def _process_token(production: bool, client_id: str | None) -> SearchToken:
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    # In Azure the user-assigned managed identity selected by AZURE_CLIENT_ID;
    # elsewhere the developer's Azure CLI or IDE login.
    credential = (
        ManagedIdentityCredential(client_id=client_id)
        if production
        else DefaultAzureCredential()
    )
    return SearchToken(credential)


def search_token(settings: Settings) -> SearchToken:
    """One cached token per process, so each turn does not fetch a new one."""

    return _process_token(
        settings.app_environment == "production",
        os.environ.get("AZURE_CLIENT_ID") or None,
    )


class SearchTokenAuth(httpx.Auth):
    """Adds the bearer token only to requests for the search service host."""

    def __init__(self, token: SearchToken, host: str) -> None:
        self._token = token
        self._host = host

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        if request.url.host == self._host:
            token = await asyncio.to_thread(self._token.get)
            request.headers["Authorization"] = f"Bearer {token}"
        yield request


async def _knowledge_unavailable(query_variants: list[str]) -> str:
    return UNAVAILABLE


class KnowledgeBaseTool(MCPStreamableHTTPTool):
    """The knowledge base MCP endpoint, tolerant to outages.

    Connecting (TCP, then each step of the MCP handshake) is bounded by
    ``CONNECT_TIMEOUT_SECONDS``; each retrieval by ``timeout_seconds``. If the
    endpoint cannot be reached, the tool still offers ``knowledge_base_retrieve``,
    answering that the knowledge base is unavailable, and no tool of the process
    tries to connect to it again for ``RETRY_AFTER_SECONDS``.
    """

    def __init__(
        self,
        url: str,
        *,
        token: SearchToken,
        timeout_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        outages: MutableMapping[str, float] | None = None,
    ) -> None:
        self._clock = clock
        self._outages = _OUTAGES if outages is None else outages
        self._call_timeout = timeout_seconds
        self._connect_timeout = min(CONNECT_TIMEOUT_SECONDS, timeout_seconds)
        self._http = httpx.AsyncClient(
            auth=SearchTokenAuth(token, urlsplit(url).hostname or ""),
            timeout=httpx.Timeout(timeout_seconds + 5, connect=self._connect_timeout),
            follow_redirects=False,
        )
        super().__init__(
            name="knowledge",
            url=url,
            http_client=self._http,
            allowed_tools=(KNOWLEDGE_TOOL,),
            approval_mode="never_require",
            load_prompts=False,
            request_timeout=timeout_seconds,
            parse_tool_results=summarize_retrieval,
            description="Carta, recetario e ingredientes del restaurante, con la web como respaldo.",
        )
        self._stand_in = FunctionTool(
            name=KNOWLEDGE_TOOL,
            description=(
                "Consulta la carta, el recetario y los ingredientes del "
                "restaurante. Ahora mismo no está disponible."
            ),
            func=_knowledge_unavailable,
        )

    @property
    def unavailable(self) -> bool:
        return not self.is_connected and self.url in self._outages

    @property
    def functions(self) -> list[FunctionTool]:
        if self.unavailable:
            return [self._stand_in]
        return super().functions

    async def connect(self, *, reset: bool = False) -> None:
        failed_at = self._outages.get(self.url)
        if not reset and failed_at is not None and self._clock() - failed_at < RETRY_AFTER_SECONDS:
            return
        # The MCP session reads its default timeout on every request: the
        # handshake (initialize, tools/list) gets the short connect budget...
        self.request_timeout = self._connect_timeout
        try:
            await super().connect(reset=reset)
        except Exception as exc:
            # Includes auth and network failures: the knowledge base is optional.
            self._outages[self.url] = self._clock()
            logger.warning("Knowledge base unreachable: %s", type(exc.__cause__ or exc).__name__)
            if reset:
                raise
            return
        finally:
            self.request_timeout = self._call_timeout
        self._outages.pop(self.url, None)
        # ...and retrievals the full one. The client session has no public
        # setter; test_knowledge.py fails if this stops extending it.
        if self.session is not None:
            self.session._session_read_timeout_seconds = timedelta(seconds=self._call_timeout)

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self._http.aclose()


class KnowledgeToolMiddleware(FunctionMiddleware):
    """A failed or timed-out retrieval becomes an explicit answer for the model."""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.function.name != KNOWLEDGE_TOOL:
            await call_next()
            return
        try:
            await call_next()
        except Exception as exc:
            logger.warning("Knowledge base retrieval failed: %s", type(exc).__name__)
            context.result = UNAVAILABLE


def create_knowledge_tool(
    settings: Settings, *, token: SearchToken | None = None
) -> KnowledgeBaseTool | None:
    """The waiter's knowledge base tool, or None when it is not configured."""

    url = knowledge_base_mcp_url(settings)
    if url is None:
        return None
    return KnowledgeBaseTool(
        url,
        token=token or search_token(settings),
        timeout_seconds=settings.knowledge_base_timeout_seconds,
    )


# Retrieval results


def summarize_retrieval(result: types.CallToolResult) -> str:
    """Each retrieved passage once, labelled with where it comes from.

    The endpoint returns the passages as a JSON array plus one reference per
    passage, whose ``sourceData`` repeats the passage for indexed sources: only
    the source is kept from it. It never fails: an unexpected format is passed
    on as it came.
    """

    raw = [text for item in result.content if isinstance(text := getattr(item, "text", None), str)]
    try:
        return _summarize(result)
    except Exception:
        logger.warning("Unexpected knowledge base result format", exc_info=True)
        return "\n".join(raw)


def _summarize(result: types.CallToolResult) -> str:
    passages: list[Mapping[str, Any]] = []
    references: dict[str, Mapping[str, Any]] = {}
    raw: list[str] = []
    parsed = False
    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        raw.append(text)
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, list):
            parsed = True
            passages.extend(entry for entry in data if isinstance(entry, Mapping))
        elif isinstance(data, Mapping) and data.get("kind") == "reference":
            references[str(data.get("ref_id"))] = data
    if not passages:
        if parsed or not any(text.strip() for text in raw):
            return NO_RESULTS
        return "\n".join(raw)
    blocks = []
    for passage in passages:
        reference = references.get(str(passage.get("ref_id")), {})
        content = str(passage.get("content") or "").strip()
        blocks.append(f"[{passage.get('ref_id')}] Origen: {source_label(passage, reference)}\n{content}")
    return "\n\n".join(blocks)


def source_label(passage: Mapping[str, Any], reference: Mapping[str, Any]) -> str:
    """``fuente externa (web)`` for Bing results, else ``documento de la casa``.

    Web passages carry their page ``url`` and their reference only a title;
    the restaurant's indexed documents carry their fields in ``sourceData``.
    """

    source_data = reference.get("sourceData")
    title = str(passage.get("title") or "")
    if passage.get("url") or (reference and not isinstance(source_data, Mapping)):
        link = str(passage.get("url") or reference.get("uri") or "")
        return ": ".join(part for part in (WEB, " — ".join(p for p in (title, link) if p)) if part)
    if not isinstance(source_data, Mapping):
        return UNKNOWN
    details = [
        str(source_data.get("title") or title) or _file_name(source_data),
        _field("tipo", source_data.get("doc_type")),
        _field("versión", source_data.get("version")),
    ]
    described = ", ".join(part for part in details if part)
    return f"{HOUSE}: {described}" if described else HOUSE


def _file_name(source_data: Mapping[str, Any]) -> str:
    for key in ("source_file", "blob_url", "filepath", "url"):
        value = source_data.get(key)
        if value:
            return str(value).rstrip("/").rsplit("/", 1)[-1]
    return ""


def _field(name: str, value: object) -> str:
    return f"{name} {value}" if value else ""
