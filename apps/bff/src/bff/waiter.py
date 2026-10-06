"""Narrow remote port between the BFF and the independent waiter service."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.application import ActorContext
from restaurant_contracts.cashier import CashierReport, PaymentMethod, PendingBill, ServedLine
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import KitchenReport
from restaurant_contracts.waiter import (
    WAITER_PAY_RESPONSE_ADAPTER,
    WAITER_SEATING_RESPONSE_ADAPTER,
    WAITER_SERVE_RESPONSE_ADAPTER,
    WAITER_TURN_RESPONSE_ADAPTER,
    SeatingReport,
    WaiterPayRequest,
    WaiterSeatingDecisionRequest,
    WaiterSeatingReleaseRequest,
    WaiterSeatingSyncRequest,
    WaiterServeRequest,
    WaiterTurnFailure,
    WaiterTurnRequest,
)

WaiterMode = Literal["scripted", "remote"]
# Where the waiter's live steps go during the turn being run.
TURN_ACTIVITY: ContextVar[Callable[[ActivityStep], None] | None] = ContextVar(
    "turn_activity", default=None
)


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
    # What the bill may include: served kitchen dishes, cooked orders still at
    # the pass and the bill already waiting for card or cash.
    served: tuple[ServedLine, ...] = ()
    orders_at_pass: int = 0
    pending_bill: PendingBill | None = None


@dataclass(frozen=True)
class WaiterTurnResult:
    reply: str
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int
    persisted_order_preferences: tuple[str, ...]
    session_json: str | None
    seating: SeatingReport | None = None
    # The kitchen's answer when the waiter sent it the order in this turn.
    kitchen: KitchenReport | None = None
    # The cashier's answer when the customer asked for the bill in this turn.
    cashier: CashierReport | None = None
    activity: tuple[ActivityStep, ...] = ()


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


@dataclass(frozen=True)
class WaiterServeCall:
    """Cooked dishes the waiter takes from the pass to the customer."""

    conversation_id: str
    actor: ActorContext
    correlation_id: str
    order_id: str
    dishes: tuple[str, ...]


@dataclass(frozen=True)
class WaiterPayCall:
    """The customer's card or cash button, relayed to the cashier's task."""

    conversation_id: str
    actor: ActorContext
    correlation_id: str
    bill: PendingBill
    method: PaymentMethod
    idempotency_key: str


@dataclass(frozen=True)
class WaiterPayResult:
    """The cashier's receipt or failure, and the waiter's fixed answer to it."""

    reply: str
    cashier: CashierReport
    activity: tuple[ActivityStep, ...] = ()


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

    async def release_seating(self, call: WaiterSeatingCall) -> WaiterSeatingResult: ...

    async def serve_order(self, call: WaiterServeCall) -> str: ...

    async def pay_bill(self, call: WaiterPayCall) -> WaiterPayResult: ...

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
            served=list(turn.served),
            orders_at_pass=turn.orders_at_pass,
            pending_bill=turn.pending_bill,
        )
        result = await self._stream_turn(request, turn.conversation_id, turn.actor.actor_id)
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
            kitchen=result.kitchen,
            cashier=result.cashier,
            activity=tuple(result.activity),
        )

    async def _stream_turn(
        self, request: WaiterTurnRequest, conversation_id: str, actor_id: str
    ) -> Any:
        """Relay each activity line live; the last line is the typed result."""

        lines: list[str] = []
        buffer = ""

        def take(piece: str) -> None:
            if not piece.strip():
                return
            step = _activity_line(piece)
            if step is None:
                lines.append(piece)
                return
            sink = TURN_ACTIVITY.get()
            if sink is not None:
                sink(step)

        try:
            async with self._client.stream(
                "POST",
                self._url,
                json={
                    "model": "restaurant",
                    "input": request.model_dump_json(),
                    "conversation": {"id": conversation_id},
                    "store": False,
                    "stream": True,
                },
                headers={"x-agent-user-id": actor_id},
            ) as response:
                response.raise_for_status()
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    text = self._output_text(json.loads(await response.aread()))
                    for piece in text.split("\n"):
                        take(piece)
                else:
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = json.loads(line[5:].strip())
                        kind = data.get("type")
                        if kind == "response.output_text.delta":
                            buffer += data.get("delta", "")
                            while "\n" in buffer:
                                piece, buffer = buffer.split("\n", 1)
                                take(piece)
                        elif kind in ("response.failed", "response.incomplete"):
                            raise ValueError("Remote waiter response did not complete")
                    take(buffer)
            if not lines:
                raise ValueError("Remote waiter response has no result")
            return WAITER_TURN_RESPONSE_ADAPTER.validate_json(lines[-1])
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise WaiterUnavailableError(type(exc).__name__) from exc

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

    async def release_seating(self, call: WaiterSeatingCall) -> WaiterSeatingResult:
        return await self._seating(
            WaiterSeatingReleaseRequest(**_seating_fields(call)), call
        )

    async def serve_order(self, call: WaiterServeCall) -> str:
        request = WaiterServeRequest(
            conversation_id=call.conversation_id,
            actor=call.actor,
            correlation_id=call.correlation_id,
            order_id=call.order_id,
            dishes=list(call.dishes),
        )
        result = await self._post(
            request, call.conversation_id, call.actor.actor_id, WAITER_SERVE_RESPONSE_ADAPTER
        )
        _raise_failure(result)
        return result.reply

    async def pay_bill(self, call: WaiterPayCall) -> WaiterPayResult:
        request = WaiterPayRequest(
            conversation_id=call.conversation_id,
            actor=call.actor,
            correlation_id=call.correlation_id,
            bill=call.bill,
            method=call.method,
            idempotency_key=call.idempotency_key,
        )
        result = await self._post(
            request, call.conversation_id, call.actor.actor_id, WAITER_PAY_RESPONSE_ADAPTER
        )
        _raise_failure(result)
        return WaiterPayResult(
            reply=result.reply, cashier=result.cashier, activity=tuple(result.activity)
        )

    async def _seating(
        self,
        request: (
            WaiterSeatingDecisionRequest
            | WaiterSeatingSyncRequest
            | WaiterSeatingReleaseRequest
        ),
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


def _activity_line(piece: str) -> ActivityStep | None:
    try:
        payload = json.loads(piece)
    except ValueError:
        return None
    if not isinstance(payload, dict) or set(payload) != {"activity"}:
        return None
    try:
        return ActivityStep.model_validate(payload["activity"])
    except ValueError:
        return None


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
