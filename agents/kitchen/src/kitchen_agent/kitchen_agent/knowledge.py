"""Foundry IQ connection used exclusively by the external kitchen agent."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
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

from kitchen_agent.config import Settings

logger = logging.getLogger(__name__)

KNOWLEDGE_TOOL = "knowledge_base_retrieve"
KNOWLEDGE_BASE_API_VERSION = "2026-08-01-preview"
SEARCH_SCOPE = "https://search.azure.com/.default"
CONNECT_TIMEOUT_SECONDS = 5
TOKEN_REFRESH_MARGIN_SECONDS = 300

UNAVAILABLE = (
    "knowledge_unavailable: la base de conocimiento del restaurante no ha "
    "podido responder. No inventes platos, ingredientes ni alérgenos."
)
NO_RESULTS = "no_results: la base de conocimiento no tiene información sobre esto."
HOUSE = "documento de la casa"
WEB = "fuente externa (web)"
UNKNOWN = "origen no indicado; trátalo como fuente externa"
PASSAGE_MARKER = re.compile(r"^(\[[^\]\n]*\] Origen: )", re.MULTILINE)


def knowledge_base_mcp_url(settings: Settings) -> str | None:
    if settings.azure_search_endpoint is None or settings.knowledge_base_name is None:
        return None
    endpoint = str(settings.azure_search_endpoint).rstrip("/")
    return (
        f"{endpoint}/knowledgebases/{settings.knowledge_base_name}/mcp"
        f"?api-version={KNOWLEDGE_BASE_API_VERSION}"
    )


class SearchToken:
    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._token: AccessToken | None = None
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if (
                self._token is None
                or self._token.expires_on - TOKEN_REFRESH_MARGIN_SECONDS < time.time()
            ):
                self._token = self._credential.get_token(SEARCH_SCOPE)
            return self._token.token


@functools.cache
def _process_token(production: bool, client_id: str | None) -> SearchToken:
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    credential = (
        ManagedIdentityCredential(client_id=client_id)
        if production
        else DefaultAzureCredential()
    )
    return SearchToken(credential)


def search_token(settings: Settings) -> SearchToken:
    return _process_token(
        settings.app_environment == "production",
        os.environ.get("AZURE_CLIENT_ID") or None,
    )


class SearchTokenAuth(httpx.Auth):
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
    def __init__(
        self,
        url: str,
        *,
        token: SearchToken,
        timeout_seconds: int,
    ) -> None:
        self._call_timeout = timeout_seconds
        self._http = httpx.AsyncClient(
            auth=SearchTokenAuth(token, urlsplit(url).hostname or ""),
            timeout=httpx.Timeout(
                timeout_seconds + 5,
                connect=min(CONNECT_TIMEOUT_SECONDS, timeout_seconds),
            ),
            follow_redirects=False,
        )
        self._unavailable = False
        super().__init__(
            name="knowledge",
            url=url,
            http_client=self._http,
            allowed_tools=(KNOWLEDGE_TOOL,),
            approval_mode="never_require",
            load_prompts=False,
            request_timeout=timeout_seconds,
            parse_tool_results=summarize_retrieval,
            description=(
                "Carta, recetario e ingredientes del restaurante, con la web "
                "como respaldo externo."
            ),
        )
        self._stand_in = FunctionTool(
            name=KNOWLEDGE_TOOL,
            description="La base de conocimiento no está disponible.",
            func=_knowledge_unavailable,
        )

    @property
    def unavailable(self) -> bool:
        return self._unavailable

    @property
    def functions(self) -> list[FunctionTool]:
        return [self._stand_in] if self._unavailable else super().functions

    async def connect(self, *, reset: bool = False) -> None:
        self.request_timeout = min(CONNECT_TIMEOUT_SECONDS, self._call_timeout)
        try:
            await super().connect(reset=reset)
        except Exception as exc:
            logger.warning(
                "Kitchen knowledge base unreachable: %s",
                type(exc.__cause__ or exc).__name__,
            )
            self._unavailable = True
            if reset:
                raise
            return
        finally:
            self.request_timeout = self._call_timeout
        self._unavailable = False
        if self.session is not None:
            self.session._session_read_timeout_seconds = timedelta(
                seconds=self._call_timeout
            )

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self._http.aclose()


class KnowledgeToolMiddleware(FunctionMiddleware):
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
            logger.warning("Kitchen knowledge retrieval failed: %s", type(exc).__name__)
            context.result = UNAVAILABLE


def create_knowledge_tool(
    settings: Settings, *, token: SearchToken | None = None
) -> KnowledgeBaseTool | None:
    url = knowledge_base_mcp_url(settings)
    if url is None:
        return None
    return KnowledgeBaseTool(
        url,
        token=token or search_token(settings),
        timeout_seconds=settings.knowledge_base_timeout_seconds,
    )


def summarize_retrieval(result: types.CallToolResult) -> str:
    raw = [
        text
        for item in result.content
        if isinstance(text := getattr(item, "text", None), str)
    ]
    try:
        return _summarize(result)
    except Exception:
        logger.warning("Unexpected kitchen knowledge result format", exc_info=True)
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
        content = PASSAGE_MARKER.sub(
            r"> \1", str(passage.get("content") or "").strip()
        )
        blocks.append(
            f"[{passage.get('ref_id')}] Origen: "
            f"{source_label(passage, reference)}\n{content}"
        )
    return "\n\n".join(blocks)


def source_label(
    passage: Mapping[str, Any], reference: Mapping[str, Any]
) -> str:
    source_data = reference.get("sourceData")
    title = str(passage.get("title") or "")
    if passage.get("url") or (reference and not isinstance(source_data, Mapping)):
        link = str(passage.get("url") or reference.get("uri") or "")
        description = " — ".join(part for part in (title, link) if part)
        return ": ".join(part for part in (WEB, description) if part)
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
