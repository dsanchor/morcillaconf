"""A2A ``CashierPort`` implementation used by the waiter.

One A2A task per bill. ``present`` opens it and expects ``input-required``
with the bill: that is «bill presented», not a failure, and the task and
context ids travel in the answer so the payment resumes the same task.
``pay`` reads the task first: a completed task already holds its receipt (a
retry or a double click), so it is returned instead of paying again. The
cashier's live steps are relayed to the waiter's activity panel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)
from a2a.utils.errors import A2AError, TaskNotCancelableError, TaskNotFoundError
from azure.identity import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from pydantic import ValidationError

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.cashier import (
    CASHIER_RESULT_ADAPTER,
    Bill,
    BillRequest,
    CashierAnswer,
    CashierFailure,
    CashierFailureCode,
    CashierTask,
    PaymentChoice,
    PendingBill,
    Receipt,
    cashier_failure,
)

from restaurant_agent import activity
from restaurant_agent.config import Settings

logger = logging.getLogger(__name__)

TERMINAL_FAILURES = {
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}


@dataclass(frozen=True)
class CashierReply:
    """What the cashier's A2A task said: its ids, its state and its result artifacts."""

    task: CashierTask | None
    state: TaskState
    results: tuple[str, ...] = ()


class CashierA2ATransport(Protocol):
    async def send(self, payload: str, task: CashierTask | None = None) -> CashierReply:
        """Send one message, opening a task or resuming ``task``."""

    async def get(self, task: CashierTask) -> CashierReply:
        """The task's current state and every result artifact it holds."""

    async def cancel(self, task: CashierTask) -> bool:
        """Whether the task is now cancelled."""


def _text_parts(parts: Sequence[Part]) -> list[str]:
    return [part.text for part in parts if part.WhichOneof("content") == "text"]


def relay_steps(texts: Sequence[str]) -> None:
    """The cashier's steps go to the waiter's panel while it works."""

    for text in texts:
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict) and set(payload) == {"activity"}:
            try:
                activity.report(ActivityStep.model_validate(payload["activity"]))
            except ValidationError:
                continue


class SdkCashierA2ATransport:
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

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        headers: dict[str, str] = {}
        if self._token_provider is not None:
            token = await asyncio.to_thread(self._token_provider)
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(headers=headers, timeout=self._timeout_seconds) as http:
            client = await ClientFactory(
                ClientConfig(streaming=True, httpx_client=http, accepted_output_modes=["text"])
            ).create_from_url(self._url)
            try:
                yield client
            finally:
                await client.close()

    async def send(self, payload: str, task: CashierTask | None = None) -> CashierReply:
        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            task_id=task.task_id if task else "",
            context_id=task.context_id if task else "",
            parts=[Part(text=payload)],
        )
        request = SendMessageRequest(
            message=message,
            configuration=SendMessageConfiguration(accepted_output_modes=["text"]),
        )
        ids: tuple[str, str] = ("", "")
        state = TaskState.TASK_STATE_UNSPECIFIED
        results: list[str] = []
        async with self._client() as client:
            async for event in client.send_message(request):
                kind = event.WhichOneof("payload")
                if kind == "task":
                    ids = (event.task.id, event.task.context_id)
                    state = event.task.status.state
                elif kind == "status_update":
                    update = event.status_update
                    ids = (update.task_id, update.context_id)
                    state = update.status.state
                    relay_steps(_text_parts(update.status.message.parts))
                elif kind == "artifact_update":
                    results.extend(_text_parts(event.artifact_update.artifact.parts))
        found = CashierTask(task_id=ids[0], context_id=ids[1]) if all(ids) else None
        return CashierReply(task=found, state=state, results=tuple(results))

    async def get(self, task: CashierTask) -> CashierReply:
        async with self._client() as client:
            found = await client.get_task(GetTaskRequest(id=task.task_id))
        texts = [text for artifact in found.artifacts for text in _text_parts(artifact.parts)]
        return CashierReply(task=task, state=found.status.state, results=tuple(texts))

    async def cancel(self, task: CashierTask) -> bool:
        async with self._client() as client:
            try:
                cancelled = await client.cancel_task(CancelTaskRequest(id=task.task_id))
            except TaskNotCancelableError:
                return False
        return cancelled.status.state == TaskState.TASK_STATE_CANCELED


def _token_provider(settings: Settings) -> Callable[[], str] | None:
    if not settings.cashier_a2a_token_scope:
        return None
    credential = (
        ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)
        if settings.app_environment == "production"
        else DefaultAzureCredential()
    )
    return get_bearer_token_provider(credential, settings.cashier_a2a_token_scope)


class InvalidCashierAnswer(ValueError):
    pass


class CashierTaskFailed(Exception):
    """The cashier's task ended without a result: the cashier could not work."""


def _last_result(reply: CashierReply, bill_id: str) -> Bill | Receipt | CashierFailure:
    if not reply.results:
        if reply.state in TERMINAL_FAILURES:
            raise CashierTaskFailed(TaskState.Name(reply.state))
        raise InvalidCashierAnswer("The cashier task holds no result")
    result = CASHIER_RESULT_ADAPTER.validate_json(reply.results[-1])
    if result.bill_id != bill_id:
        raise InvalidCashierAnswer("The cashier answered another bill")
    return result


class A2ACashier:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: CashierA2ATransport | None = None,
    ) -> None:
        self._timeout_seconds = settings.cashier_timeout_seconds
        self._transport = transport
        if self._transport is None and settings.cashier_a2a_url is not None:
            self._transport = SdkCashierA2ATransport(
                str(settings.cashier_a2a_url),
                timeout_seconds=self._timeout_seconds,
                token_provider=_token_provider(settings),
            )

    async def present(self, request: BillRequest) -> CashierAnswer:
        bill_id = request.bill_id
        if self._transport is None:
            return self._failed(bill_id, CashierFailureCode.CASHIER_NOT_CONFIGURED)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                reply = await self._transport.send(request.model_dump_json())
            result = _last_result(reply, bill_id)
            if isinstance(result, Receipt):
                raise InvalidCashierAnswer("A new bill cannot be paid already")
            if not isinstance(result, Bill):
                return CashierAnswer(result=result)
            if reply.state != TaskState.TASK_STATE_INPUT_REQUIRED or reply.task is None:
                raise InvalidCashierAnswer("A presented bill waits for the customer")
            return CashierAnswer(result=result, task=reply.task)
        except TimeoutError:
            return self._failed(bill_id, CashierFailureCode.TIMEOUT)
        except ValueError as exc:
            # Includes pydantic's ValidationError.
            logger.warning("Invalid A2A cashier bill for %s: %s", bill_id, type(exc).__name__)
            return self._failed(bill_id, CashierFailureCode.INVALID_BILL)
        except (CashierTaskFailed, A2AError, httpx.HTTPError) as exc:
            logger.warning("Cashier A2A unavailable for %s: %s", bill_id, type(exc).__name__)
            return self._failed(bill_id, CashierFailureCode.CASHIER_UNAVAILABLE)

    async def pay(self, pending: PendingBill, choice: PaymentChoice) -> CashierAnswer:
        bill_id = pending.bill_id
        if self._transport is None:
            return self._failed(bill_id, CashierFailureCode.CASHIER_NOT_CONFIGURED)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                reply = await self._settle(pending, choice)
            result = _last_result(reply, bill_id)
            if isinstance(result, Bill):
                raise InvalidCashierAnswer("The payment answered with a bill")
            return CashierAnswer(result=result, task=pending.task)
        except TaskNotFoundError:
            return self._failed(bill_id, CashierFailureCode.BILL_NOT_FOUND, pending.task)
        except TimeoutError:
            return self._failed(bill_id, CashierFailureCode.TIMEOUT, pending.task)
        except ValueError as exc:
            logger.warning("Invalid A2A cashier receipt for %s: %s", bill_id, type(exc).__name__)
            return self._failed(bill_id, CashierFailureCode.INVALID_BILL, pending.task)
        except (CashierTaskFailed, A2AError, httpx.HTTPError) as exc:
            logger.warning("Cashier A2A unavailable for %s: %s", bill_id, type(exc).__name__)
            return self._failed(bill_id, CashierFailureCode.CASHIER_UNAVAILABLE, pending.task)

    async def _settle(self, pending: PendingBill, choice: PaymentChoice) -> CashierReply:
        assert self._transport is not None
        current = await self._transport.get(pending.task)
        if current.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            try:
                return await self._transport.send(choice.model_dump_json(), pending.task)
            except TaskNotFoundError:
                raise
            except A2AError:
                # Another attempt completed the task in between: read its receipt.
                current = await self._transport.get(pending.task)
        if current.state == TaskState.TASK_STATE_COMPLETED:
            return current
        raise TaskNotFoundError("The bill's task is no longer open")

    async def cancel(self, pending: PendingBill) -> bool:
        if self._transport is None:
            return False
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._transport.cancel(pending.task)
        except (TimeoutError, A2AError, httpx.HTTPError) as exc:
            logger.warning(
                "Cashier task for %s not cancelled: %s", pending.bill_id, type(exc).__name__
            )
            return False

    @staticmethod
    def _failed(
        bill_id: str, code: CashierFailureCode, task: CashierTask | None = None
    ) -> CashierAnswer:
        return CashierAnswer(result=cashier_failure(bill_id, code), task=task)
