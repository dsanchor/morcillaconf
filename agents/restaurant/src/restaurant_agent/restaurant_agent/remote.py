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

from restaurant_contracts.waiter import (
    WAITER_REQUEST_ADAPTER,
    SeatingReport,
    WaiterSeatingDecisionRequest,
    WaiterSeatingRequest,
    WaiterSeatingReleaseRequest,
    WaiterSeatingSuccess,
    WaiterSeatingSyncRequest,
    WaiterTurnFailure,
    WaiterTurnRequest,
    WaiterTurnSuccess,
)

from restaurant_agent.agent import create_waiter_agent
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
from restaurant_agent.memory.store import (
    DurableMemoryRepository,
    MemoryConflictError,
    MemoryNotFoundError,
)
from restaurant_agent.seating import bind_visit

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


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
    ) -> None:
        self._settings = settings
        # Seating decisions and syncs never call the model, so they never
        # connect to the knowledge base either.
        self._seating_settings = settings.model_copy(
            update={"azure_search_endpoint": None, "knowledge_base_name": None}
        )
        self._memory = memory_store or create_memory_store(settings)
        self._agent_factory = agent_factory

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
                        session_json=_session_to_json(exported.agent_session),
                        seating=_report(manager, request),
                    )
                response = await manager.send_message(
                    conversation_id=request.conversation_id,
                    actor_id=request.actor.actor_id,
                    message=request.message,
                    correlation_id=request.correlation_id,
                )
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
                    session_json=_session_to_json(exported.agent_session),
                    seating=_report(manager, request),
                )
        except ConversationError as exc:
            return _failure(exc)

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
        options=ResponsesServerOptions(default_model="restaurant")
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
            else:
                operation = selected_service.take_turn(turn)
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
