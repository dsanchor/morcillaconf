"""Responses-protocol boundary for the independently hosted waiter."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import AbstractContextManager, asynccontextmanager, suppress
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Callable, TypeVar
from uuid import uuid4

from agent_framework import AgentSession
from azure.ai.agentserver.responses import (
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from azure.ai.agentserver.core import get_request_context

from restaurant_contracts.memory import (
    DurableMemoryRecord,
    MemoryCandidate,
    MemoryKind,
    MemorySnapshot,
)
from restaurant_contracts.waiter import (
    WAITER_REQUEST_ADAPTER,
    SeatingReport,
    WaiterSeatingDecisionRequest,
    WaiterSeatingRequest,
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
from restaurant_agent.memory.store import MemoryNotFoundError
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


class _MemoryState:
    def __init__(self, records: list[DurableMemoryRecord]) -> None:
        self.records = [record.model_copy(deep=True) for record in records]
        self.candidates: list[MemoryCandidate] = []


class _MemoryBinding(AbstractContextManager["_MemoryState"]):
    def __init__(
        self,
        variable: ContextVar[_MemoryState | None],
        state: _MemoryState,
    ) -> None:
        self._variable = variable
        self._state = state
        self._token: Token[_MemoryState | None] | None = None

    def __enter__(self) -> _MemoryState:
        self._token = self._variable.set(self._state)
        return self._state

    def __exit__(self, *args: object) -> None:
        assert self._token is not None
        self._variable.reset(self._token)


class RequestMemoryStore:
    """Request-scoped memory snapshot used while the BFF remains its authority."""

    def __init__(self) -> None:
        self._state: ContextVar[_MemoryState | None] = ContextVar(
            "remote_waiter_memory", default=None
        )

    def bind(self, records: list[DurableMemoryRecord]) -> _MemoryBinding:
        return _MemoryBinding(self._state, _MemoryState(records))

    def _current(self) -> _MemoryState:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Remote waiter memory is not bound to a request")
        return state

    def remember_memory(
        self,
        actor_id: str,
        *,
        kind: MemoryKind,
        value: str,
        source_conversation_id: str,
    ) -> DurableMemoryRecord:
        state = self._current()
        normalized = value.casefold()
        now = datetime.now(UTC)
        existing = next(
            (
                record
                for record in state.records
                if record.actor_id == actor_id
                and record.kind == kind
                and record.value.casefold() == normalized
            ),
            None,
        )
        state.candidates.append(MemoryCandidate(kind=kind, value=value))
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "value": value,
                    "source_conversation_id": source_conversation_id,
                    "updated_at": now,
                    "occurrence_count": existing.occurrence_count + 1,
                }
            )
            state.records[state.records.index(existing)] = updated
            return updated
        created = DurableMemoryRecord(
            preference_id=f"remote_{uuid4().hex}",
            actor_id=actor_id,
            kind=kind,
            value=value,
            source_conversation_id=source_conversation_id,
            created_at=now,
            updated_at=now,
        )
        state.records.append(created)
        return created

    def list_memories(self, actor_id: str) -> list[DurableMemoryRecord]:
        return [
            record.model_copy(deep=True)
            for record in self._current().records
            if record.actor_id == actor_id
        ]

    def correct_memory(
        self, actor_id: str, *, preference_id: str, value: str
    ) -> DurableMemoryRecord:
        raise MemoryNotFoundError(preference_id)

    def delete_memory(self, actor_id: str, *, preference_id: str) -> None:
        raise MemoryNotFoundError(preference_id)

    def delete_memories(self, actor_id: str, *, preference_ids: list[str]) -> int:
        return 0

    def delete_all_memories(self, actor_id: str) -> int:
        return 0

    def snapshot(self, actor_id: str) -> MemorySnapshot:
        return MemorySnapshot(memories=self.list_memories(actor_id))


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
    ) -> None:
        self._settings = settings
        self._memory = RequestMemoryStore()
        self._agent_factory = agent_factory

    async def take_turn(
        self, request: WaiterTurnRequest
    ) -> WaiterTurnSuccess | WaiterTurnFailure:
        with self._memory.bind(request.memories) as memory:
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
                        memory_candidates=memory.candidates,
                    )
            except ConversationError as exc:
                return _failure(exc)

    async def decide_seating(
        self, request: WaiterSeatingDecisionRequest
    ) -> WaiterSeatingSuccess | WaiterTurnFailure:
        """Resume the paused confirmation with the customer's button decision."""

        with self._memory.bind([]):
            try:
                async with self._conversation(request) as manager:
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

        with self._memory.bind([]):
            try:
                async with self._conversation(request) as manager:
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

    @asynccontextmanager
    async def _conversation(
        self,
        request: WaiterTurnRequest | WaiterSeatingRequest,
        *,
        state: SessionState | None = None,
        persisted_order_preferences: list[str] | None = None,
    ) -> AsyncIterator[ConversationManager]:
        agent = self._agent_factory(self._settings, memory_store=self._memory)
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
