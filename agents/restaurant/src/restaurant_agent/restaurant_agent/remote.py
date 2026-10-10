"""Responses-protocol boundary for the independently hosted waiter."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from typing import Any, Callable, TypeVar

from agent_framework import AgentSession
from azure.ai.agentserver.responses import (
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from azure.ai.agentserver.core import get_request_context

from restaurant_contracts.activity import ActivityStep, merge_steps
from restaurant_contracts.cashier import (
    CashierFailureCode,
    CashierPort,
    CashierReport,
    PaymentChoice,
    PendingBill,
    Receipt,
    bill_misses,
    supersedes_bill,
)
from restaurant_contracts.waiter import (
    WAITER_REQUEST_ADAPTER,
    SeatingReport,
    WaiterPayRequest,
    WaiterPaySuccess,
    WaiterSeatingDecisionRequest,
    WaiterSeatingRequest,
    WaiterSeatingReleaseRequest,
    WaiterSeatingSuccess,
    WaiterSeatingSyncRequest,
    WaiterServeRequest,
    WaiterServeSuccess,
    WaiterTurnFailure,
    WaiterTurnRequest,
    WaiterTurnSuccess,
)

from restaurant_agent.activity import recording, tracked
from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.cashier import A2ACashier
from restaurant_agent.cashier.rendering import BILL_CLOSED, GOODBYE, PAY_AGAIN, render_text
from restaurant_agent.cashier_tool import BillingContext
from restaurant_agent.config import Settings
from restaurant_agent.conversation import (
    AgentUnavailableError,
    ConversationError,
    ConversationManager,
    InvalidAgentResponseError,
    NoPendingSeatingDecisionError,
    SeatingUnavailableError,
    SessionState,
    TurnLimitExceededError,
)
from restaurant_agent.memory import create_memory_store
from restaurant_agent.kitchen.rendering import served_text
from restaurant_agent.memory.store import (
    DurableMemoryRepository,
    MemoryConflictError,
    MemoryNotFoundError,
)
from restaurant_agent.seating import bind_visit

logger = logging.getLogger(__name__)
_T = TypeVar("_T")
# Payment failures after which the bill is no longer open at the cashier.
CLOSED_BILL_FAILURES = {
    CashierFailureCode.BILL_NOT_FOUND,
    CashierFailureCode.REVIEW_REJECTED,
    CashierFailureCode.CASHIER_NOT_CONFIGURED,
}


async def _cancel_when_signalled(
    operation: Awaitable[_T], cancellation_signal: asyncio.Event
) -> _T:
    operation_task = asyncio.ensure_future(operation)
    cancellation_task = asyncio.create_task(cancellation_signal.wait())
    try:
        done, _ = await asyncio.wait(
            {operation_task, cancellation_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation_task in done:
            operation_task.cancel()
            with suppress(asyncio.CancelledError):
                await operation_task
            raise asyncio.CancelledError
        return operation_task.result()
    finally:
        cancellation_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancellation_task


def _session_from_json(data: str | None) -> AgentSession | None:
    if not data:
        return None
    try:
        return AgentSession.from_dict(json.loads(data))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid serialized agent session") from exc


def _session_to_json(session: AgentSession) -> str:
    return json.dumps(session.to_dict(), ensure_ascii=False)


class RemoteWaiterService:
    def __init__(
        self,
        settings: Settings,
        *,
        agent_factory: Callable = create_waiter_agent,
        memory_store: DurableMemoryRepository | None = None,
        cashier: CashierPort | None = None,
    ) -> None:
        self._settings = settings
        # Seating decisions and syncs never call the model, so they never
        # connect to the knowledge base either.
        self._seating_settings = settings.model_copy(
            update={"azure_search_endpoint": None, "knowledge_base_name": None}
        )
        self._memory = memory_store or create_memory_store(settings)
        self._agent_factory = agent_factory
        # Payments and cancellations reach the cashier without the model.
        self._cashier = cashier or A2ACashier(settings)

    async def take_turn(
        self, request: WaiterTurnRequest
    ) -> WaiterTurnSuccess | WaiterTurnFailure:
        try:
            async with self._conversation(
                request,
                state=SessionState(
                    customer=request.customer,
                    order_draft=request.order_draft,
                    turn_count=request.turn_count,
                ),
                persisted_order_preferences=request.persisted_order_preferences,
            ) as manager:
                memory_reply = _handle_memory_message(manager, request)
                if memory_reply is not None:
                    exported = manager.export_conversation(
                        conversation_id=request.conversation_id,
                        actor_id=request.actor.actor_id,
                    )
                    return WaiterTurnSuccess(
                        reply=memory_reply,
                        customer=exported.state.customer,
                        order_draft=exported.state.order_draft,
                        turn_count=exported.state.turn_count + 1,
                        persisted_order_preferences=exported.persisted_order_preferences,
                        memory_intent="none",
                        session_json=_session_to_json(exported.agent_session),
                        seating=_report(manager, request),
                    )
                response = await manager.send_message(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                    message=request.message,
                    correlation_id=request.correlation_id,
                    billing=BillingContext(
                        served=request.served,
                        orders_at_pass=request.orders_at_pass,
                        pending_bill=request.pending_bill,
                    ),
                )
                fresh = [report for report in (response.kitchen, response.bar) if report is not None]
                if request.pending_bill is not None and any(
                    supersedes_bill(report) for report in fresh
                ):
                    await self._supersede(request.pending_bill)
                presented = response.cashier.pending if response.cashier is not None else None
                if presented is not None and any(
                    bill_misses(response.cashier.request, report) for report in fresh
                ):
                    # Presented in this turn before new dishes or drinks: stale at once.
                    await self._supersede(presented)
                exported = manager.export_conversation(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                )
                return WaiterTurnSuccess(
                    reply=response.reply,
                    customer=exported.state.customer,
                    order_draft=exported.state.order_draft,
                    turn_count=exported.state.turn_count,
                    persisted_order_preferences=exported.persisted_order_preferences,
                    memory_intent=response.memory_intent.value,
                    remembered_memory_count=len(response.remembered_memories),
                    session_json=_session_to_json(exported.agent_session),
                    seating=_report(manager, request),
                    kitchen=response.kitchen,
                    bar=response.bar,
                    cashier=response.cashier,
                )
        except ConversationError as exc:
            return _failure(exc)

    async def _supersede(self, pending: PendingBill) -> None:
        """New dishes or drinks make the presented bill stale: its cashier task is cancelled."""

        with tracked("caja", "Caja: anula la cuenta pendiente", "Hay platos o bebidas nuevos") as step:
            cancelled = await self._cashier.cancel(pending)
            step.detail = (
                "Cuenta anulada: pedidla otra vez al terminar"
                if cancelled
                else "Caja ya no la tenía abierta"
            )

    async def decide_seating(
        self, request: WaiterSeatingDecisionRequest
    ) -> WaiterSeatingSuccess | WaiterTurnFailure:
        """Resume the paused confirmation with the customer's button decision."""

        try:
            async with self._conversation(
                request, settings=self._seating_settings
            ) as manager:
                decided = await manager.decide_seating(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                    approved=request.decision == "confirmed",
                    proposal_token=request.proposal_token,
                )
                exported = manager.export_conversation(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                )
                return WaiterSeatingSuccess(
                    operation="decide_seating",
                    reply=decided.reply,
                    outcome=decided.outcome,
                    session_json=_session_to_json(exported.agent_session),
                    seating=_report(manager, request),
                )
        except ConversationError as exc:
            return _failure(exc)

    async def sync_seating(
        self, request: WaiterSeatingSyncRequest
    ) -> WaiterSeatingSuccess | WaiterTurnFailure:
        """Read the visit's seating and the room from the MCP, without the model."""

        try:
            async with self._conversation(
                request, settings=self._seating_settings
            ) as manager:
                await manager.sync_seating(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                )
                exported = manager.export_conversation(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                )
                return WaiterSeatingSuccess(
                    operation="sync_seating",
                    session_json=_session_to_json(exported.agent_session),
                    seating=_report(manager, request),
                )
        except ConversationError as exc:
            return _failure(exc)

    async def release_seating(
        self, request: WaiterSeatingReleaseRequest
    ) -> WaiterSeatingSuccess | WaiterTurnFailure:
        """Release the visit's place through the agent's MCP connection."""

        try:
            async with self._conversation(
                request, settings=self._seating_settings
            ) as manager:
                await manager.release_seating(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                )
                exported = manager.export_conversation(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                )
                return WaiterSeatingSuccess(
                    operation="release_seating",
                    reply="La mesa o barra queda libre. ¡Hasta pronto!",
                    outcome="cancelled",
                    session_json=_session_to_json(exported.agent_session),
                    seating=_report(manager, request),
                )
        except ConversationError as exc:
            return _failure(exc)

    async def serve_order(self, request: WaiterServeRequest) -> WaiterServeSuccess:
        """Take the cooked dishes from the pass to the table, without the model."""

        return WaiterServeSuccess(order_id=request.order_id, reply=served_text(request.dishes))

    async def pay_bill(self, request: WaiterPayRequest) -> WaiterPaySuccess:
        """Relay the customer's button to the cashier's same task, without the model.

        Only a receipt earns the goodbye; the BFF then ends the visit as «Salir»
        does. A retry or a double click gets the stored receipt, never a second
        charge.
        """

        choice = PaymentChoice(
            bill_id=request.bill.bill_id,
            version=request.bill.version,
            method=request.method,
            idempotency_key=request.idempotency_key,
        )
        steps: list[ActivityStep] = []

        def keep(step: ActivityStep) -> None:
            steps[:] = merge_steps(steps, step)

        with recording(keep):
            answer = await self._cashier.pay(request.bill, choice)
        result = answer.result
        if isinstance(result, Receipt):
            reply = GOODBYE
        elif result.code in CLOSED_BILL_FAILURES:
            reply = BILL_CLOSED
        else:
            reply = PAY_AGAIN
        return WaiterPaySuccess(
            reply=reply,
            cashier=CashierReport(
                bill_id=request.bill.bill_id,
                result=result,
                text=render_text(result, paying=True),
                task=answer.task,
            ),
            activity=steps,
        )

    @asynccontextmanager
    async def _conversation(
        self,
        request: WaiterTurnRequest | WaiterSeatingRequest,
        *,
        settings: Settings | None = None,
        state: SessionState | None = None,
        persisted_order_preferences: list[str] | None = None,
    ) -> AsyncIterator[ConversationManager]:
        agent = self._agent_factory(settings or self._settings, memory_store=self._memory)
        entered = False
        try:
            if hasattr(agent, "__aenter__"):
                await agent.__aenter__()
                entered = True
            session = _session_from_json(request.session_json)
            if request.visit_id is not None:
                if session is None:
                    session = agent.create_session(session_id=request.conversation_id)
                bind_visit(session.state, request.visit_id)
            manager = ConversationManager(
                agent,
                max_turns=self._settings.waiter_max_turns,
                memory_store=self._memory,
            )
            manager.restore_conversation(
                conversation_id=request.conversation_id,
                actor_id=request.actor.actor_id,
                authenticated=request.actor.authenticated,
                presented_name=request.presented_name,
                agent_session=session,
                state=state,
                persisted_order_preferences=persisted_order_preferences or (),
            )
            yield manager
        finally:
            if entered:
                await agent.__aexit__(None, None, None)


def _report(
    manager: ConversationManager, request: WaiterTurnRequest | WaiterSeatingRequest
) -> SeatingReport | None:
    report = manager.seating_report(
        conversation_id=request.conversation_id, actor_id=request.actor.actor_id
    )
    return SeatingReport.model_validate(report) if report is not None else None


def _handle_memory_message(
    manager: ConversationManager,
    request: WaiterTurnRequest,
) -> str | None:
    try:
        parts = shlex.split(request.message)
    except ValueError:
        return "No entiendo ese comando de memoria."
    if not parts or parts[0].casefold() != "/memory":
        return None
    action = parts[1].casefold() if len(parts) > 1 else "list"
    try:
        if action == "list" and len(parts) in (1, 2):
            snapshot = manager.memory_snapshot(
                conversation_id=request.conversation_id,
                actor_id=request.actor.actor_id,
            )
            if not snapshot.memories:
                return "Aún no recuerdo ninguna preferencia tuya."
            rendered = "; ".join(
                f"{memory.preference_id}: {memory.kind.value}, {memory.value}"
                for memory in snapshot.memories
            )
            return f"Esto es lo que recuerdo: {rendered}."
        if action == "correct" and len(parts) >= 4:
            manager.correct_memory(
                conversation_id=request.conversation_id,
                actor_id=request.actor.actor_id,
                preference_id=parts[2],
                value=" ".join(parts[3:]),
            )
            return "He corregido ese recuerdo."
        if action == "delete" and len(parts) == 3:
            manager.delete_memory(
                conversation_id=request.conversation_id,
                actor_id=request.actor.actor_id,
                preference_id=parts[2],
            )
            return "He eliminado ese recuerdo."
        if action == "clear" and len(parts) == 2:
            manager.clear_memories(
                conversation_id=request.conversation_id,
                actor_id=request.actor.actor_id,
            )
            return "He olvidado tus preferencias y restricciones guardadas."
    except MemoryNotFoundError:
        return "No encuentro ese recuerdo."
    except MemoryConflictError:
        return "Ese recuerdo ya existe."
    return "Uso: /memory list|correct <id> <texto>|delete <id>|clear"


def _failure(exc: ConversationError) -> WaiterTurnFailure:
    codes: tuple[tuple[type[ConversationError], Any], ...] = (
        (TurnLimitExceededError, "turn_limit"),
        (SeatingUnavailableError, "seating_unavailable"),
        (NoPendingSeatingDecisionError, "no_pending_decision"),
        (AgentUnavailableError, "unavailable"),
        (InvalidAgentResponseError, "invalid_response"),
    )
    code = next((code for kind, code in codes if isinstance(exc, kind)), "internal_error")
    return WaiterTurnFailure(code=code, message=str(exc)[:500] or code)


def create_server(
    settings: Settings,
    *,
    service: RemoteWaiterService | None = None,
) -> ResponsesAgentServerHost:
    selected_service = service or RemoteWaiterService(settings)
    server = ResponsesAgentServerHost(
        options=ResponsesServerOptions(default_model="restaurant"),
        # The process configures a trace-only provider in main.py. The host's
        # default bootstrap would also export logs and capture message content.
        configure_observability=None,
    )

    @server.response_handler
    async def handle(request, context, cancellation_signal):
        try:
            payload = request.get("input")
            if not isinstance(payload, str):
                raise ValueError("The waiter expects a JSON string input")
            turn = WAITER_REQUEST_ADAPTER.validate_json(payload)
            user_id = get_request_context().user_id
            if user_id is None or user_id != turn.actor.actor_id:
                raise ValueError("The request identity does not match the waiter turn")
            if isinstance(turn, WaiterSeatingDecisionRequest):
                operation = selected_service.decide_seating(turn)
            elif isinstance(turn, WaiterSeatingReleaseRequest):
                operation = selected_service.release_seating(turn)
            elif isinstance(turn, WaiterSeatingSyncRequest):
                operation = selected_service.sync_seating(turn)
            elif isinstance(turn, WaiterServeRequest):
                operation = selected_service.serve_order(turn)
            elif isinstance(turn, WaiterPayRequest):
                operation = selected_service.pay_bill(turn)
            else:
                # A turn streams its activity steps, one JSON line each, and
                # ends with the typed result as its last line.
                return TextResponse(
                    context,
                    request,
                    text=_turn_lines(selected_service.take_turn, turn, cancellation_signal),
                )
            result = await _cancel_when_signalled(operation, cancellation_signal)
        except Exception as exc:
            logger.error("Remote waiter request failed: %s", type(exc).__name__)
            result = WaiterTurnFailure(
                code="internal_error", message="The waiter request could not be processed"
            )
        return TextResponse(
            context,
            request,
            text=result.model_dump_json(),
        )

    return server


async def _turn_lines(
    take_turn: Callable[[WaiterTurnRequest], Awaitable[Any]],
    turn: WaiterTurnRequest,
    cancellation_signal: asyncio.Event,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[ActivityStep] = asyncio.Queue()
    steps: list[ActivityStep] = []
    with recording(queue.put_nowait):
        # The task copies the context, so the turn reports into this queue.
        operation = asyncio.ensure_future(_cancel_when_signalled(take_turn(turn), cancellation_signal))
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({operation, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                step = getter.result()
                steps = merge_steps(steps, step)
                yield json.dumps({"activity": step.model_dump(mode="json")}, ensure_ascii=False) + "\n"
                continue
            getter.cancel()
            break
        while not queue.empty():
            step = queue.get_nowait()
            steps = merge_steps(steps, step)
            yield json.dumps({"activity": step.model_dump(mode="json")}, ensure_ascii=False) + "\n"
        try:
            result = operation.result()
        except Exception as exc:
            logger.error("Remote waiter turn failed: %s", type(exc).__name__)
            result = WaiterTurnFailure(
                code="internal_error", message="The waiter request could not be processed"
            )
        if isinstance(result, WaiterTurnSuccess):
            result = result.model_copy(update={"activity": steps})
        yield result.model_dump_json()
    finally:
        if not operation.done():
            operation.cancel()
