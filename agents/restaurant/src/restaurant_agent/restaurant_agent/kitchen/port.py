"""A2A ``KitchenPort`` implementation used by the waiter."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable, Sequence
from typing import Protocol

import httpx
from a2a.client import A2AClientError, ClientConfig, ClientFactory
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)
from azure.identity import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from pydantic import ValidationError

from restaurant_contracts.kitchen import (
    KITCHEN_RESULT_ADAPTER,
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenPlan,
)

from restaurant_agent.config import Settings

logger = logging.getLogger(__name__)

FAILURES = {
    KitchenFailureCode.KITCHEN_NOT_CONFIGURED: (
        "Cocina no está configurada para recibir pedidos."
    ),
    KitchenFailureCode.TIMEOUT: "Cocina no ha respondido a tiempo.",
    KitchenFailureCode.CHEF_UNAVAILABLE: "Cocina no puede responder ahora mismo.",
    KitchenFailureCode.INVALID_PLAN: (
        "Cocina ha devuelto un plan que no supera la validación."
    ),
}
TERMINAL_FAILURES = {
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}


def kitchen_failure(order: KitchenOrder, code: KitchenFailureCode) -> KitchenFailure:
    return KitchenFailure(
        order_id=order.order_id,
        code=code,
        message=FAILURES[code],
    )


class KitchenA2ATransport(Protocol):
    async def send(self, request: KitchenOrder) -> str:
        """Return the external kitchen's serialized result."""


class SdkKitchenA2ATransport:
    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._token_provider = token_provider

    async def send(self, request: KitchenOrder) -> str:
        headers: dict[str, str] = {}
        if self._token_provider is not None:
            token = await asyncio.to_thread(self._token_provider)
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self._timeout_seconds,
        ) as http_client:
            factory = ClientFactory(
                ClientConfig(
                    streaming=True,
                    httpx_client=http_client,
                    accepted_output_modes=["text"],
                )
            )
            client = await factory.create_from_url(self._url)
            chunks: list[str] = []
            failed = False
            try:
                request = SendMessageRequest(
                    message=Message(
                        message_id=str(uuid.uuid4()),
                        role=Role.ROLE_USER,
                        parts=[Part(text=request.model_dump_json())],
                    ),
                    configuration=SendMessageConfiguration(
                        accepted_output_modes=["text"],
                    ),
                )
                async for event in client.send_message(request):
                    payload_type = event.WhichOneof("payload")
                    if payload_type == "artifact_update":
                        chunks.extend(_text_parts(event.artifact_update.artifact.parts))
                    elif payload_type == "status_update":
                        failed = event.status_update.status.state in TERMINAL_FAILURES
                    elif payload_type == "task":
                        failed = event.task.status.state in TERMINAL_FAILURES
            finally:
                await client.close()
        if failed or not chunks:
            raise A2AClientError("Kitchen A2A task did not produce a result")
        return "".join(chunks)


def _text_parts(parts: Sequence[Part]) -> list[str]:
    return [part.text for part in parts if part.WhichOneof("content") == "text"]


def _token_provider(settings: Settings) -> Callable[[], str] | None:
    if not settings.kitchen_a2a_token_scope:
        return None
    credential = (
        ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)
        if settings.app_environment == "production"
        else DefaultAzureCredential()
    )
    return get_bearer_token_provider(
        credential,
        settings.kitchen_a2a_token_scope,
    )


class A2AKitchen:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: KitchenA2ATransport | None = None,
    ) -> None:
        self._timeout_seconds = settings.kitchen_timeout_seconds
        self._transport = transport
        if self._transport is None and settings.kitchen_a2a_url is not None:
            self._transport = SdkKitchenA2ATransport(
                str(settings.kitchen_a2a_url),
                timeout_seconds=self._timeout_seconds,
                token_provider=_token_provider(settings),
            )

    async def plan(self, order: KitchenOrder) -> KitchenPlan | KitchenFailure:
        if self._transport is None:
            return kitchen_failure(order, KitchenFailureCode.KITCHEN_NOT_CONFIGURED)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                payload = await self._transport.send(order)
            result = KITCHEN_RESULT_ADAPTER.validate_json(payload)
            if result.order_id != order.order_id:
                raise ValueError("Kitchen result answers another order")
            return result
        except TimeoutError:
            return kitchen_failure(order, KitchenFailureCode.TIMEOUT)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "Invalid A2A kitchen result for %s: %s",
                order.order_id,
                type(exc).__name__,
            )
            return kitchen_failure(order, KitchenFailureCode.INVALID_PLAN)
        except (A2AClientError, httpx.HTTPError) as exc:
            logger.warning(
                "Kitchen A2A unavailable for %s: %s",
                order.order_id,
                type(exc).__name__,
            )
            return kitchen_failure(order, KitchenFailureCode.CHEF_UNAVAILABLE)
