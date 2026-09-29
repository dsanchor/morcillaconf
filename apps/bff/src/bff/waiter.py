"""Narrow remote port between the BFF and the independent waiter service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.memory import DurableMemoryRecord, MemoryCandidate
from restaurant_contracts.waiter import (
    WAITER_TURN_RESPONSE_ADAPTER,
    WaiterTurnFailure,
    WaiterTurnRequest,
)

WaiterMode = Literal["scripted", "remote"]


@dataclass(frozen=True)
class WaiterTurn:
    conversation_id: str
    actor: ActorContext
    presented_name: str
    message: str
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int
    persisted_order_preferences: tuple[str, ...]
    session_json: str | None
    correlation_id: str
    visit_id: str | None = None
    seating_context: dict[str, Any] | None = None
    pending_assignment_id: str | None = None
    history_notes: tuple[str, ...] = ()
    memories: tuple[DurableMemoryRecord, ...] = ()


@dataclass(frozen=True)
class WaiterTurnResult:
    reply: str
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int
    persisted_order_preferences: tuple[str, ...]
    session_json: str | None
    seating_proposal: dict[str, Any] | None = None
    memory_candidates: tuple[MemoryCandidate, ...] = ()


class WaiterError(RuntimeError):
    pass


class WaiterUnavailableError(WaiterError):
    pass


class WaiterInvalidResponseError(WaiterError):
    pass


class WaiterTurnLimitError(WaiterError):
    pass


class WaiterSeatingUnavailableError(WaiterUnavailableError):
    pass


class WaiterPort(Protocol):
    mode: WaiterMode

    async def take_turn(self, turn: WaiterTurn) -> WaiterTurnResult: ...

    async def aclose(self) -> None: ...


class RemoteWaiter:
    """Invoke the standalone waiter through its Responses 2.0 endpoint."""

    mode: WaiterMode = "remote"

    def __init__(self, endpoint: str, *, timeout_seconds: float = 60) -> None:
        normalized = endpoint.rstrip("/")
        self._url = (
            normalized if normalized.endswith("/responses") else f"{normalized}/responses"
        )
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def take_turn(self, turn: WaiterTurn) -> WaiterTurnResult:
        request = WaiterTurnRequest(
            conversation_id=turn.conversation_id,
            actor=turn.actor,
            presented_name=turn.presented_name,
            message=turn.message,
            customer=turn.customer,
            order_draft=turn.order_draft,
            turn_count=turn.turn_count,
            persisted_order_preferences=list(turn.persisted_order_preferences),
            session_json=turn.session_json,
            correlation_id=turn.correlation_id,
            visit_id=turn.visit_id,
            seating_context=turn.seating_context,
            pending_assignment_id=turn.pending_assignment_id,
            history_notes=list(turn.history_notes),
            memories=list(turn.memories),
        )
        try:
            response = await self._client.post(
                self._url,
                json={
                    "model": "restaurant",
                    "input": request.model_dump_json(),
                    "conversation": {"id": turn.conversation_id},
                    "store": False,
                    "stream": False,
                },
                headers={"x-agent-user-id": turn.actor.actor_id},
            )
            response.raise_for_status()
            envelope = response.json()
            text = self._output_text(envelope)
            result = WAITER_TURN_RESPONSE_ADAPTER.validate_json(text)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise WaiterUnavailableError(type(exc).__name__) from exc

        if isinstance(result, WaiterTurnFailure):
            errors = {
                "turn_limit": WaiterTurnLimitError,
                "seating_unavailable": WaiterSeatingUnavailableError,
                "unavailable": WaiterUnavailableError,
                "invalid_response": WaiterInvalidResponseError,
                "internal_error": WaiterError,
            }
            raise errors[result.code](result.message)
        return WaiterTurnResult(
            reply=result.reply,
            customer=result.customer,
            order_draft=result.order_draft,
            turn_count=result.turn_count,
            persisted_order_preferences=tuple(
                result.persisted_order_preferences
            ),
            session_json=result.session_json,
            seating_proposal=result.seating_proposal,
            memory_candidates=tuple(result.memory_candidates),
        )

    @staticmethod
    def _output_text(envelope: dict[str, Any]) -> str:
        if envelope.get("status") != "completed":
            raise ValueError("Remote waiter response did not complete")
        for item in envelope.get("output") or ():
            if item.get("type") not in ("message", "output_message"):
                continue
            for content in item.get("content") or ():
                if content.get("type") in ("output_text", "text"):
                    text = content.get("text")
                    if isinstance(text, str):
                        return text
        raise ValueError("Remote waiter response has no output text")

    async def aclose(self) -> None:
        await self._client.aclose()
