"""Narrow remote port between the BFF and the independent waiter service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import (
    WAITER_SEATING_RESPONSE_ADAPTER,
    WAITER_TURN_RESPONSE_ADAPTER,
    SeatingReport,
    WaiterSeatingDecisionRequest,
    WaiterSeatingSyncRequest,
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


@dataclass(frozen=True)
class WaiterTurnResult:
    reply: str
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int
    persisted_order_preferences: tuple[str, ...]
    session_json: str | None
    seating: SeatingReport | None = None


@dataclass(frozen=True)
class WaiterSeatingCall:
    """Seating work the waiter does without the model: a decision or a sync."""

    conversation_id: str
    actor: ActorContext
    presented_name: str
    session_json: str | None
    correlation_id: str
    visit_id: str


@dataclass(frozen=True)
class WaiterSeatingResult:
    reply: str
    outcome: str | None
    session_json: str | None
    seating: SeatingReport | None


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


class WaiterNoPendingDecisionError(WaiterError):
    pass


class WaiterPort(Protocol):
    mode: WaiterMode

    async def take_turn(self, turn: WaiterTurn) -> WaiterTurnResult: ...

    async def decide_seating(
        self, call: WaiterSeatingCall, *, approved: bool, proposal_token: str
    ) -> WaiterSeatingResult: ...

    async def sync_seating(self, call: WaiterSeatingCall) -> WaiterSeatingResult: ...

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
        )
        result = await self._post(
            request, turn.conversation_id, turn.actor.actor_id, WAITER_TURN_RESPONSE_ADAPTER
        )
        _raise_failure(result)
        return WaiterTurnResult(
            reply=result.reply,
            customer=result.customer,
            order_draft=result.order_draft,
            turn_count=result.turn_count,
            persisted_order_preferences=tuple(
                result.persisted_order_preferences
            ),
            session_json=result.session_json,
            seating=result.seating,
        )

    async def decide_seating(
        self, call: WaiterSeatingCall, *, approved: bool, proposal_token: str
    ) -> WaiterSeatingResult:
        """Send the customer's button answer to the waiter's paused confirmation."""

        request = WaiterSeatingDecisionRequest(
            **_seating_fields(call),
            decision="confirmed" if approved else "rejected",
            proposal_token=proposal_token,
        )
        return await self._seating(request, call)

    async def sync_seating(self, call: WaiterSeatingCall) -> WaiterSeatingResult:
        return await self._seating(WaiterSeatingSyncRequest(**_seating_fields(call)), call)

    async def _seating(
        self,
        request: WaiterSeatingDecisionRequest | WaiterSeatingSyncRequest,
        call: WaiterSeatingCall,
    ) -> WaiterSeatingResult:
        result = await self._post(
            request, call.conversation_id, call.actor.actor_id, WAITER_SEATING_RESPONSE_ADAPTER
        )
        _raise_failure(result)
        return WaiterSeatingResult(
            reply=result.reply,
            outcome=result.outcome,
            session_json=result.session_json,
            seating=result.seating,
        )

    async def _post(
        self, request: Any, conversation_id: str, actor_id: str, adapter: Any
    ) -> Any:
        try:
            response = await self._client.post(
                self._url,
                json={
                    "model": "restaurant",
                    "input": request.model_dump_json(),
                    "conversation": {"id": conversation_id},
                    "store": False,
                    "stream": False,
                },
                headers={"x-agent-user-id": actor_id},
            )
            response.raise_for_status()
            envelope = response.json()
            text = self._output_text(envelope)
            return adapter.validate_json(text)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise WaiterUnavailableError(type(exc).__name__) from exc

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


def _seating_fields(call: WaiterSeatingCall) -> dict[str, Any]:
    return {
        "conversation_id": call.conversation_id,
        "actor": call.actor,
        "presented_name": call.presented_name,
        "session_json": call.session_json,
        "correlation_id": call.correlation_id,
        "visit_id": call.visit_id,
    }


def _raise_failure(result: object) -> None:
    if not isinstance(result, WaiterTurnFailure):
        return
    errors = {
        "turn_limit": WaiterTurnLimitError,
        "seating_unavailable": WaiterSeatingUnavailableError,
        "no_pending_decision": WaiterNoPendingDecisionError,
        "unavailable": WaiterUnavailableError,
        "invalid_response": WaiterInvalidResponseError,
        "internal_error": WaiterError,
    }
    raise errors[result.code](result.message)
