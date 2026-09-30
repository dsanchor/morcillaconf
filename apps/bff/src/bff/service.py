"""Command processing of the BFF: identity, ownership, idempotency and events.

Every command runs in one SQLite transaction that persists state, stream
events and the command result together. Messages to the waiter return
``pending`` at once; the turn runs in a background task, one at a time per
conversation, and its confirmed snapshot and terminal result are published
through the same event log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from restaurant_contracts.application import (
    STREAM_EVENT_ADAPTER,
    Action,
    ActorContext,
    ArriveCommand,
    ChatMessage,
    ClearMemoryCommand,
    Command,
    CommandResult,
    CommandStatusChanged,
    CompletedCommandResult,
    CorrectMemoryCommand,
    DecideTableCommand,
    DeleteMemoryCommand,
    ErrorCode,
    FailedCommandResult,
    MemoryView,
    PendingCommandResult,
    PublicError,
    ReadMemoryCommand,
    RestaurantSnapshot,
    SendMessageCommand,
    SnapshotUpdated,
    StreamEvent,
    VisibleMemory,
)
from restaurant_contracts.customer import CustomerSnapshot, missing_customer_fields
from restaurant_contracts.seating import (
    RoomPlace,
    RoomSeat,
    RoomView,
    SeatingPlace,
    SeatingProposal,
    SeatingView,
)

from restaurant_contracts.memory_store import (
    DurableMemoryRepository,
    MemoryConflictError,
    MemoryNotFoundError,
)
from restaurant_contracts.waiter import SeatingReport

from bff.greeting import greeting
from bff.identity import InvalidNameError, actor_id_for, presented_name
from bff.notifier import Notifier
from bff.storage import ConversationRow, Database, SeatingRow, Transaction
from bff.telemetry import tracer
from bff.waiter import (
    WaiterError,
    WaiterInvalidResponseError,
    WaiterNoPendingDecisionError,
    WaiterPort,
    WaiterSeatingCall,
    WaiterSeatingResult,
    WaiterSeatingUnavailableError,
    WaiterTurn,
    WaiterTurnLimitError,
    WaiterTurnResult,
    WaiterUnavailableError,
)

logger = logging.getLogger(__name__)

NOT_FOUND_CONVERSATION = "No encuentro esa conversación."
FORBIDDEN_CONVERSATION = "No tienes acceso a esta conversación."
NOT_FOUND_VISIT = "No encuentro esa visita."
FORBIDDEN_VISIT = "Esa visita no es tuya."
BUSY = "El camarero todavía está con tu mensaje anterior."
TURN_LIMIT = (
    "Hemos llegado al límite de mensajes de esta visita. "
    "Escribe /new para empezar otra."
)
WAITER_UNAVAILABLE = (
    "El camarero no puede responder ahora mismo. Inténtalo de nuevo en un momento."
)
WAITER_CONFUSED = "El camarero se ha liado con la respuesta. Vuelve a intentarlo."
WAITER_FAILED = "Algo ha fallado mientras el camarero te atendía. Vuelve a intentarlo."
INTERRUPTED = "El camarero se interrumpió. Vuelve a escribir tu mensaje."
UNKNOWN_MEMORY = "No recuerdo nada con el identificador {memory_id}."
DUPLICATE_MEMORY = "Ya recuerdo eso."
IDEMPOTENCY_CONFLICT = "Ese identificador de comando ya se usó con otro contenido."
UNKNOWN_COMMAND = "No conozco ese comando."
UNAUTHENTICATED = "Tu sesión no es válida o ha caducado. Vuelve a entrar por la puerta."
CURSOR_EXPIRED = "El cursor ha caducado. Recupera un nuevo snapshot."
FUTURE_CURSOR = "Ese cursor todavía no existe en esta conversación."
INVALID_CURSOR = "El cursor no es válido."
SEATING_UNAVAILABLE = (
    "El servicio de mesas no responde ahora mismo. Inténtalo en un momento."
)
NO_PROPOSAL = "No tengo ninguna propuesta de sitio pendiente para ti."
STALE_PROPOSAL = "Esa propuesta ya no está vigente."
ALREADY_DECIDED = "Esa propuesta ya está decidida."
NEW_WHILE_SEATED = (
    "Ya estáis sentados en {place}. Podréis empezar otra visita cuando se libere."
)
NEW_WHILE_PROPOSED = (
    "Antes de empezar otra visita, confirma o rechaza la propuesta de {place}."
)

Recovery = Literal["none", "retry_same_command", "fetch_snapshot"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class PublicFailure(Exception):
    """Transport-level error, turned into a PublicError by the API."""

    def __init__(
        self, code: ErrorCode, message: str, *, recovery: Recovery = "none"
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery

    def to_error(self, correlation_id: str | None = None) -> PublicError:
        return PublicError(
            code=self.code,
            message=self.message,
            correlation_id=correlation_id or new_id("corr"),
            recovery=self.recovery,
        )


@dataclass(frozen=True)
class DemoSession:
    actor: ActorContext
    presented_name: str


@dataclass(frozen=True)
class OpenedSession:
    token: str
    identity: ActorContext
    presented_name: str
    active_visit_id: str | None
    waiter: str
    expires_at: datetime


@dataclass(frozen=True)
class Heartbeat:
    """Keeps an idle SSE connection alive."""


@dataclass(frozen=True)
class StreamFailure:
    error: PublicError


StreamItem = StreamEvent | Heartbeat | StreamFailure


@dataclass(frozen=True)
class _TurnJob:
    conversation_id: str
    event_id: str
    correlation_id: str
    actor: ActorContext
    message: str


@dataclass(frozen=True)
class _Outcome:
    result: CommandResult
    conversation_id: str | None = None
    turn: _TurnJob | None = None


def fingerprint(command: Command) -> str:
    canonical = json.dumps(
        command.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _place_text(seat: SeatingRow) -> str:
    if seat.kind == "bar":
        if not seat.seats:
            return "la barra"
        if len(seat.seats) == 1:
            return f"la barra, puesto {seat.seats[0]}"
        return f"la barra, puestos {seat.seats[0]} a {seat.seats[-1]}"
    return f"la {seat.label}"


class RestaurantService:
    def __init__(
        self,
        *,
        database: Database,
        memory_store: DurableMemoryRepository,
        waiter: WaiterPort,
        max_turns: int = 20,
        session_ttl: timedelta = timedelta(hours=12),
        heartbeat_seconds: float = 15.0,
        notifier: Notifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = database
        self._memory = memory_store
        self._waiter = waiter
        self._max_turns = max_turns
        self._session_ttl = session_ttl
        self._heartbeat = heartbeat_seconds
        self._notifier = notifier or Notifier()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tasks: set[asyncio.Task[None]] = set()
        self._waiter_locks: dict[str, asyncio.Lock] = {}

    @property
    def waiter_mode(self) -> str:
        return self._waiter.mode

    @property
    def seating_enabled(self) -> bool:
        """Whether the waiter reports seating: it is the only client of the MCP."""

        with self._db.read() as tx:
            cache = tx.get_room_cache()
        return cache is not None and cache.enabled

    # Sessions

    def open_session(self, name: str) -> OpenedSession:
        try:
            presented = presented_name(name)
            actor_id = actor_id_for(name)
        except InvalidNameError as exc:
            raise PublicFailure(ErrorCode.INVALID_COMMAND, str(exc)) from exc
        token = secrets.token_urlsafe(32)
        now = self._clock()
        expires_at = now + self._session_ttl
        with self._db.write() as tx:
            tx.insert_session(
                token_hash=_token_hash(token),
                actor_id=actor_id,
                presented_name=presented,
                created_at=now,
                expires_at=expires_at,
            )
            active_visit_id = tx.latest_visit_id(actor_id)
        return OpenedSession(
            token=token,
            identity=ActorContext(actor_id=actor_id, authenticated=True),
            presented_name=presented,
            active_visit_id=active_visit_id,
            waiter=self._waiter.mode,
            expires_at=expires_at,
        )

    def authenticate(self, token: str | None) -> DemoSession:
        if not token:
            raise PublicFailure(ErrorCode.UNAUTHENTICATED, UNAUTHENTICATED)
        with self._db.read() as tx:
            row = tx.get_session(_token_hash(token))
        if row is None or row.expires_at <= self._clock():
            raise PublicFailure(ErrorCode.UNAUTHENTICATED, UNAUTHENTICATED)
        return DemoSession(
            actor=ActorContext(actor_id=row.actor_id, authenticated=True),
            presented_name=row.presented_name,
        )

    # Commands

    async def submit(self, session: DemoSession, command: Command) -> CommandResult:
        if isinstance(command, DecideTableCommand):
            return await self._decide_table(session, command)
        if isinstance(command, ArriveCommand) and command.payload.resume_visit_id is None:
            refused = await self._leave_seating_for_new_visit(session, command)
            if refused is not None:
                return refused
        result = await self._submit(session, command)
        if isinstance(command, ArriveCommand) and result.status == "completed":
            self._start(self._sync_seating(result.conversation_id))
        return result

    async def _submit(self, session: DemoSession, command: Command) -> CommandResult:
        actor_id = session.actor.actor_id
        digest = fingerprint(command)
        with tracer.start_as_current_span(
            "bff.command", attributes={"bff.command.type": command.event_type}
        ) as span:
            with self._db.write() as tx:
                stored = tx.get_result(actor_id, command.event_id)
                if stored is not None:
                    if stored.fingerprint != digest:
                        raise PublicFailure(
                            ErrorCode.IDEMPOTENCY_CONFLICT, IDEMPOTENCY_CONFLICT
                        )
                    span.set_attribute("bff.command.replayed", True)
                    span.set_attribute("bff.command.status", stored.result.status)
                    return stored.result
                outcome = self._execute(tx, session, command, new_id("corr"))
                tx.save_result(
                    actor_id=actor_id,
                    fingerprint=digest,
                    conversation_id=outcome.conversation_id,
                    result=outcome.result,
                    now=self._clock(),
                )
            span.set_attribute("bff.correlation_id", outcome.result.correlation_id)
            span.set_attribute("bff.command.status", outcome.result.status)
            if outcome.conversation_id:
                span.set_attribute("bff.conversation_id", outcome.conversation_id)
        if outcome.conversation_id:
            self._notifier.notify(outcome.conversation_id)
        if outcome.turn is not None:
            self._start(self._run_turn(outcome.turn))
        return outcome.result

    def _start(self, work: Any) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def get_result(self, session: DemoSession, event_id: str) -> CommandResult:
        with self._db.read() as tx:
            stored = tx.get_result(session.actor.actor_id, event_id)
        if stored is None:
            raise PublicFailure(ErrorCode.NOT_FOUND, UNKNOWN_COMMAND)
        return stored.result

    def get_snapshot(self, session: DemoSession, conversation_id: str) -> RestaurantSnapshot:
        # A write transaction: projecting memories may assign new short ids.
        with self._db.write() as tx:
            row = self._owned(tx, session, conversation_id)
            return self._snapshot(tx, row, row.cursor)

    # Stream

    def check_stream(
        self, session: DemoSession, conversation_id: str, after_cursor: int
    ) -> None:
        """Authorize and validate the cursor before any replay starts."""

        with self._db.read() as tx:
            row = self._owned(tx, session, conversation_id)
            if isinstance(after_cursor, bool) or after_cursor < 0:
                raise PublicFailure(ErrorCode.INVALID_COMMAND, INVALID_CURSOR)
            if after_cursor > row.cursor:
                raise PublicFailure(ErrorCode.CONFLICT, FUTURE_CURSOR)
            oldest = tx.oldest_cursor(conversation_id)
            if row.cursor > after_cursor and (oldest is None or oldest > after_cursor + 1):
                raise PublicFailure(
                    ErrorCode.CURSOR_EXPIRED, CURSOR_EXPIRED, recovery="fetch_snapshot"
                )

    async def stream(
        self, conversation_id: str, after_cursor: int
    ) -> AsyncIterator[StreamItem]:
        """Replay persisted events after the cursor, then follow new ones."""

        cursor = after_cursor
        while True:
            wake = self._notifier.subscribe(conversation_id)
            try:
                with self._db.read() as tx:
                    rows = tx.events_after(conversation_id, cursor)
                if rows:
                    if rows[0][0] != cursor + 1:
                        yield StreamFailure(
                            PublicFailure(
                                ErrorCode.CURSOR_EXPIRED,
                                CURSOR_EXPIRED,
                                recovery="fetch_snapshot",
                            ).to_error()
                        )
                        return
                    for event_cursor, raw in rows:
                        yield STREAM_EVENT_ADAPTER.validate_json(raw)
                        cursor = event_cursor
                    continue
                try:
                    await asyncio.wait_for(wake, timeout=self._heartbeat)
                except TimeoutError:
                    yield Heartbeat()
            finally:
                self._notifier.unsubscribe(conversation_id, wake)

    # Lifecycle

    def recover_interrupted_turns(self) -> int:
        """Fail turns left in flight by a previous process, so nothing hangs."""

        recovered: list[str] = []
        with self._db.write() as tx:
            for row in tx.processing_conversations():
                event_id = row.pending_event_id
                row.process_status = "idle"
                row.pending_event_id = None
                row.updated_at = self._clock()
                tx.update_conversation(row)
                stored = tx.get_result(row.actor_id, event_id) if event_id else None
                correlation_id = stored.correlation_id if stored else new_id("corr")
                command_event_id = event_id or new_id("cmd")
                self._emit_snapshot(tx, row, command_event_id, correlation_id)
                if event_id:
                    result = self._failed(
                        event_id, correlation_id, ErrorCode.UNAVAILABLE, INTERRUPTED
                    )
                    self._emit_status(tx, row, event_id, correlation_id, result)
                    tx.update_result(row.actor_id, result, self._clock())
                recovered.append(row.conversation_id)
        for conversation_id in recovered:
            self._notifier.notify(conversation_id)
        return len(recovered)

    async def drain(self) -> None:
        """Wait for the waiter turns in flight (tests and graceful shutdown)."""

        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        close = getattr(self._waiter, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as exc:
                logger.warning("Could not close the waiter client: %s", type(exc).__name__)

    # Command handlers

    def _execute(
        self, tx: Transaction, session: DemoSession, command: Command, correlation_id: str
    ) -> _Outcome:
        if isinstance(command, ArriveCommand):
            return self._arrive(tx, session, command, correlation_id)
        row = tx.get_conversation(command.conversation_id)
        if row is None:
            return _Outcome(
                self._failed(
                    command.event_id, correlation_id, ErrorCode.NOT_FOUND, NOT_FOUND_CONVERSATION
                )
            )
        if row.actor_id != session.actor.actor_id:
            return _Outcome(
                self._failed(
                    command.event_id, correlation_id, ErrorCode.FORBIDDEN, FORBIDDEN_CONVERSATION
                )
            )
        if isinstance(command, SendMessageCommand):
            return self._accept_message(tx, session, row, command, correlation_id)
        return self._memory_command(tx, row, command, correlation_id)

    def _arrive(
        self,
        tx: Transaction,
        session: DemoSession,
        command: ArriveCommand,
        correlation_id: str,
    ) -> _Outcome:
        resume = command.payload.resume_visit_id
        if resume is None:
            row = self._open_visit(tx, session, command)
        else:
            visit = tx.get_visit(resume)
            if visit is None:
                return _Outcome(
                    self._failed(
                        command.event_id, correlation_id, ErrorCode.NOT_FOUND, NOT_FOUND_VISIT
                    )
                )
            if visit.actor_id != session.actor.actor_id:
                return _Outcome(
                    self._failed(
                        command.event_id, correlation_id, ErrorCode.FORBIDDEN, FORBIDDEN_VISIT
                    )
                )
            row = tx.get_conversation_by_visit(resume)
            assert row is not None
        return self._complete(tx, row, command, correlation_id)

    def _open_visit(
        self, tx: Transaction, session: DemoSession, command: ArriveCommand
    ) -> ConversationRow:
        now = self._clock()
        visit_id = new_id("visit")
        name = session.presented_name
        tx.insert_visit(
            visit_id=visit_id,
            actor_id=session.actor.actor_id,
            presented_name=name,
            created_at=now,
        )
        row = ConversationRow(
            conversation_id=new_id("conv"),
            visit_id=visit_id,
            actor_id=session.actor.actor_id,
            presented_name=name,
            created_at=now,
            updated_at=now,
            customer=CustomerSnapshot(presented_name=name),
        )
        tx.insert_conversation(row)
        tx.add_message(
            row.conversation_id,
            ChatMessage(
                message_id=new_id("msg"),
                role="assistant",
                text=greeting(name),
                occurred_at=now,
                command_event_id=command.event_id,
            ),
        )
        return row

    def _accept_message(
        self,
        tx: Transaction,
        session: DemoSession,
        row: ConversationRow,
        command: SendMessageCommand,
        correlation_id: str,
    ) -> _Outcome:
        if row.process_status == "processing":
            return self._fail_in(tx, row, command, correlation_id, ErrorCode.CONFLICT, BUSY)
        if row.turn_count >= self._max_turns:
            return self._fail_in(
                tx, row, command, correlation_id, ErrorCode.TURN_LIMIT_EXCEEDED, TURN_LIMIT
            )
        now = self._clock()
        tx.add_message(
            row.conversation_id,
            ChatMessage(
                message_id=new_id("msg"),
                role="user",
                text=command.payload.message,
                occurred_at=now,
                command_event_id=command.event_id,
            ),
        )
        row.process_status = "processing"
        row.pending_event_id = command.event_id
        row.updated_at = now
        tx.update_conversation(row)
        pending = PendingCommandResult(
            schema_version=1,
            event_id=command.event_id,
            correlation_id=correlation_id,
            status="pending",
        )
        self._emit_status(tx, row, command.event_id, correlation_id, pending)
        self._emit_snapshot(tx, row, command.event_id, correlation_id)
        return _Outcome(
            pending,
            row.conversation_id,
            _TurnJob(
                conversation_id=row.conversation_id,
                event_id=command.event_id,
                correlation_id=correlation_id,
                actor=session.actor,
                message=command.payload.message,
            ),
        )

    def _memory_command(
        self,
        tx: Transaction,
        row: ConversationRow,
        command: ReadMemoryCommand
        | CorrectMemoryCommand
        | DeleteMemoryCommand
        | ClearMemoryCommand,
        correlation_id: str,
    ) -> _Outcome:
        if isinstance(command, ReadMemoryCommand):
            return self._complete(tx, row, command, correlation_id)
        if row.process_status == "processing":
            return self._fail_in(tx, row, command, correlation_id, ErrorCode.CONFLICT, BUSY)
        if isinstance(command, ClearMemoryCommand):
            self._memory.delete_all_memories(row.actor_id)
            return self._complete(tx, row, command, correlation_id)
        alias = command.payload.memory_id
        memory_id = tx.memory_for_alias(row.actor_id, alias)
        unknown = UNKNOWN_MEMORY.format(memory_id=alias)
        if memory_id is None:
            return self._fail_in(tx, row, command, correlation_id, ErrorCode.NOT_FOUND, unknown)
        try:
            if isinstance(command, CorrectMemoryCommand):
                self._memory.correct_memory(
                    row.actor_id, preference_id=memory_id, value=command.payload.value
                )
            else:
                self._memory.delete_memory(row.actor_id, preference_id=memory_id)
        except MemoryNotFoundError:
            return self._fail_in(tx, row, command, correlation_id, ErrorCode.NOT_FOUND, unknown)
        except MemoryConflictError:
            return self._fail_in(
                tx, row, command, correlation_id, ErrorCode.CONFLICT, DUPLICATE_MEMORY
            )
        return self._complete(tx, row, command, correlation_id)

    # Waiter turns

    async def _run_turn(self, job: _TurnJob) -> None:
        """Run one turn and always leave the conversation idle with a terminal result."""

        try:
            await self._turn(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Only the type: storage or validation errors may carry customer content.
            logger.error(
                "Could not finish the waiter turn (correlation %s): %s",
                job.correlation_id,
                type(exc).__name__,
            )
            try:
                self._finish_turn(job, (ErrorCode.INTERNAL_ERROR, WAITER_FAILED))
            except Exception as fallback:
                logger.error(
                    "Could not record the failed turn (correlation %s): %s",
                    job.correlation_id,
                    type(fallback).__name__,
                )
        self._notifier.notify(job.conversation_id)

    async def _turn(self, job: _TurnJob) -> None:
        with tracer.start_as_current_span(
            "bff.waiter.turn",
            attributes={
                "bff.conversation_id": job.conversation_id,
                "bff.correlation_id": job.correlation_id,
                "bff.waiter.mode": self._waiter.mode,
            },
        ) as span:
            async with self._lock(job.conversation_id):
                await self._turn_locked(job, span)

    async def _turn_locked(self, job: _TurnJob, span: Any) -> None:
        with self._db.read() as tx:
            row = tx.get_conversation(job.conversation_id)
        if row is None or row.pending_event_id != job.event_id:
            return
        turn = WaiterTurn(
            conversation_id=row.conversation_id,
            actor=job.actor,
            presented_name=row.presented_name,
            message=job.message,
            customer=row.customer,
            order_draft=row.order_draft,
            turn_count=row.turn_count,
            persisted_order_preferences=tuple(row.persisted_order_preferences),
            session_json=row.agent_session_json,
            correlation_id=job.correlation_id,
            memories=tuple(self._memory.list_memories(row.actor_id)),
            visit_id=row.visit_id,
        )
        outcome: WaiterTurnResult | tuple[ErrorCode, str]
        try:
            outcome = await self._waiter.take_turn(turn)
            if not outcome.reply.strip():
                raise WaiterInvalidResponseError("Empty waiter reply")
        except WaiterTurnLimitError:
            outcome = (ErrorCode.TURN_LIMIT_EXCEEDED, TURN_LIMIT)
        except WaiterSeatingUnavailableError:
            logger.warning("Seating unavailable (correlation %s)", job.correlation_id)
            outcome = (ErrorCode.UNAVAILABLE, SEATING_UNAVAILABLE)
        except WaiterUnavailableError as exc:
            logger.warning(
                "Waiter unavailable (correlation %s): %s", job.correlation_id, exc
            )
            outcome = (ErrorCode.UNAVAILABLE, WAITER_UNAVAILABLE)
        except WaiterInvalidResponseError:
            logger.warning("Invalid waiter response (correlation %s)", job.correlation_id)
            outcome = (ErrorCode.INTERNAL_ERROR, WAITER_CONFUSED)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Only the type: model errors may echo customer content.
            logger.error(
                "Waiter turn failed (correlation %s): %s",
                job.correlation_id,
                type(exc).__name__,
            )
            outcome = (ErrorCode.INTERNAL_ERROR, WAITER_FAILED)
        succeeded = isinstance(outcome, WaiterTurnResult)
        span.set_attribute("bff.waiter.outcome", "completed" if succeeded else outcome[0])
        if succeeded:
            span.set_attribute("bff.waiter.turn_number", outcome.turn_count)
        self._finish_turn(job, outcome)

    def _finish_turn(
        self,
        job: _TurnJob,
        outcome: WaiterTurnResult | tuple[ErrorCode, str],
    ) -> None:
        now = self._clock()
        with self._db.write() as tx:
            row = tx.get_conversation(job.conversation_id)
            if row is None or row.pending_event_id != job.event_id:
                return
            if isinstance(outcome, WaiterTurnResult):
                self._apply_report(tx, row.conversation_id, outcome.seating)
                row.customer = outcome.customer
                row.order_draft = outcome.order_draft
                row.turn_count = outcome.turn_count
                row.persisted_order_preferences = list(outcome.persisted_order_preferences)
                for candidate in outcome.memory_candidates:
                    self._memory.remember_memory(
                        row.actor_id,
                        kind=candidate.kind,
                        value=candidate.value,
                        source_conversation_id=row.conversation_id,
                    )
                if outcome.session_json is not None:
                    row.agent_session_json = outcome.session_json
                tx.add_message(
                    row.conversation_id,
                    ChatMessage(
                        message_id=new_id("msg"),
                        role="assistant",
                        text=outcome.reply,
                        occurred_at=now,
                        command_event_id=job.event_id,
                    ),
                )
            row.process_status = "idle"
            row.pending_event_id = None
            row.updated_at = now
            tx.update_conversation(row)
            cursor = self._emit_snapshot(tx, row, job.event_id, job.correlation_id)
            if isinstance(outcome, WaiterTurnResult):
                result: CommandResult = CompletedCommandResult(
                    schema_version=1,
                    event_id=job.event_id,
                    correlation_id=job.correlation_id,
                    status="completed",
                    visit_id=row.visit_id,
                    conversation_id=row.conversation_id,
                    cursor=cursor,
                )
            else:
                result = self._failed(job.event_id, job.correlation_id, *outcome)
            self._emit_status(tx, row, job.event_id, job.correlation_id, result)
            tx.update_result(job.actor.actor_id, result, now)

    # Seating: the waiter is the only client of the seating MCP. The BFF
    # persists and presents what it reports (the card and the room) and sends
    # the customer's button decisions back to it.

    def _lock(self, conversation_id: str) -> asyncio.Lock:
        return self._waiter_locks.setdefault(conversation_id, asyncio.Lock())

    def room(self, session: DemoSession, conversation_id: str) -> RoomView:
        """The room from the latest waiter report, with the viewer's places marked."""

        now = self._clock()
        with self._db.read() as tx:
            self._owned(tx, session, conversation_id)
            cache = tx.get_room_cache()
            seat = tx.get_seating(conversation_id)
        if cache is None or not cache.enabled or not cache.room:
            return RoomView(schema_version=1, seating_enabled=False, generated_at=now)
        return RoomView(
            schema_version=1,
            seating_enabled=True,
            generated_at=now,
            places=[self._room_place(place, seat, now) for place in cache.room],
        )

    @staticmethod
    def _room_place(place: dict[str, Any], seat: SeatingRow | None, now: datetime) -> RoomPlace:
        def expired(item: dict[str, Any]) -> bool:
            value = item.get("expires_at")
            return (
                item.get("state") == "held"
                and isinstance(value, str)
                and datetime.fromisoformat(value) <= now
            )

        own = seat is not None and seat.resource_id == place["place_id"]
        common = {
            "place_id": place["place_id"],
            "kind": place["kind"],
            "label": place["label"],
            "capacity": place["capacity"],
            "display_order": place["display_order"],
        }
        if place["kind"] == "bar":
            mine_positions = set(seat.seats) if own and seat is not None else set()
            seats = []
            for item in place.get("seats") or ():
                state = "free" if expired(item) else item["state"]
                seats.append(
                    RoomSeat(
                        position=item["position"],
                        state=state,
                        mine=state != "free" and item["position"] in mine_positions,
                    )
                )
            if any(item.state == "free" for item in seats):
                state = "free"
            elif any(item.state == "occupied" for item in seats):
                state = "occupied"
            else:
                state = "held"
            return RoomPlace(**common, state=state, mine=any(item.mine for item in seats), seats=seats)
        state = "free" if expired(place) else place["state"]
        taken = state != "free"
        size = min(place.get("party_size") or 1, place["capacity"], 20) if taken else None
        return RoomPlace(**common, state=state, party_size=size, mine=taken and own)

    def _apply_report(
        self, tx: Transaction, conversation_id: str, report: SeatingReport | None
    ) -> bool:
        """Persist what the waiter reported: the room for everyone, the place for this visit.

        Returns whether the customer's own seating changed.
        """

        now = self._clock()
        if report is None:
            cache = tx.get_room_cache()
            if cache is None or cache.enabled:
                tx.put_room_cache(enabled=False, room=[], now=now)
            return False
        tx.put_room_cache(
            enabled=True,
            room=[place.model_dump(mode="json") for place in report.room],
            now=now,
        )
        current = tx.get_seating(conversation_id)
        place = report.place
        if report.status == "none" or place is None or report.token is None:
            if current is None:
                return False
            tx.delete_seating(conversation_id)
            return True
        if report.status == "proposed" and not report.awaiting_decision:
            # A hold without a pending confirmation cannot be decided: no card.
            if current is None:
                return False
            tx.delete_seating(conversation_id)
            return True
        same = current is not None and current.token == report.token
        row = SeatingRow(
            conversation_id=conversation_id,
            status=report.status,
            proposal_id=current.proposal_id if same else new_id("prop"),
            token=report.token,
            resource_id=place.place_id,
            kind=place.kind,
            label=place.label,
            capacity=place.capacity,
            seats=list(place.seats),
            party_size=report.party_size or 1,
            version=report.version or (current.version if same else 1),
            expires_at=report.expires_at if report.status == "proposed" else None,
            seated_at=(report.seated_at or now) if report.status == "seated" else None,
        )
        if current == row:
            return False
        tx.put_seating(row)
        return True

    @staticmethod
    def _seating_place(seat: SeatingRow) -> SeatingPlace:
        return SeatingPlace(
            place_id=seat.resource_id,
            kind=seat.kind,
            label=seat.label,
            capacity=seat.capacity,
            seats=seat.seats if seat.kind == "bar" else [],
        )

    def _seating_view(self, seat: SeatingRow | None) -> SeatingView:
        if seat is None:
            return SeatingView()
        place = self._seating_place(seat)
        if seat.status == "proposed":
            return SeatingView(
                status="proposed",
                proposal=SeatingProposal(
                    proposal_id=seat.proposal_id,
                    version=seat.version,
                    place=place,
                    party_size=seat.party_size,
                    expires_at=seat.expires_at or self._clock(),
                ),
            )
        return SeatingView(
            status="seated",
            place=place,
            party_size=seat.party_size,
            seated_at=seat.seated_at or self._clock(),
        )

    @staticmethod
    def _seating_call(row: ConversationRow, correlation_id: str) -> WaiterSeatingCall:
        return WaiterSeatingCall(
            conversation_id=row.conversation_id,
            actor=ActorContext(actor_id=row.actor_id, authenticated=True),
            presented_name=row.presented_name,
            session_json=row.agent_session_json,
            correlation_id=correlation_id,
            visit_id=row.visit_id,
        )

    async def _sync_seating(self, conversation_id: str | None) -> None:
        """Let the waiter read the visit's seating and the room, without the model."""

        if conversation_id is None:
            return
        try:
            async with self._lock(conversation_id):
                await self._sync_locked(conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The greeting stands; the next waiter call reports the room.
            logger.warning("Seating sync failed: %s", type(exc).__name__)

    async def _sync_locked(self, conversation_id: str) -> WaiterSeatingResult | None:
        with self._db.read() as tx:
            row = tx.get_conversation(conversation_id)
        if row is None or row.process_status == "processing":
            return None
        with tracer.start_as_current_span(
            "bff.seating.sync", attributes={"bff.conversation_id": conversation_id}
        ):
            result = await self._waiter.sync_seating(self._seating_call(row, new_id("corr")))
        with self._db.write() as tx:
            current = tx.get_conversation(conversation_id)
            if current is None or current.process_status == "processing":
                return result
            if result.session_json is not None:
                current.agent_session_json = result.session_json
                tx.update_conversation(current)
            if self._apply_report(tx, conversation_id, result.seating):
                self._emit_snapshot(tx, current, new_id("sync"), new_id("corr"))
        self._notifier.notify(conversation_id)
        return result

    async def _decide_table(
        self, session: DemoSession, command: DecideTableCommand
    ) -> CommandResult:
        actor_id = session.actor.actor_id
        digest = fingerprint(command)
        payload = command.payload
        conversation_id = command.conversation_id
        with tracer.start_as_current_span(
            "bff.seating.decision",
            attributes={
                "bff.command.type": command.event_type,
                "bff.seating.decision": payload.decision,
                "bff.conversation_id": conversation_id,
            },
        ) as span:
            async with self._lock(conversation_id):
                correlation_id = new_id("corr")
                with self._db.write() as tx:
                    stored = tx.get_result(actor_id, command.event_id)
                    if stored is not None:
                        if stored.fingerprint != digest:
                            raise PublicFailure(
                                ErrorCode.IDEMPOTENCY_CONFLICT, IDEMPOTENCY_CONFLICT
                            )
                        span.set_attribute("bff.command.replayed", True)
                        return stored.result
                    early, seat, row = self._check_decision(
                        tx, session, command, correlation_id
                    )
                    if early is not None:
                        tx.save_result(
                            actor_id=actor_id,
                            fingerprint=digest,
                            conversation_id=early.conversation_id,
                            result=early.result,
                            now=self._clock(),
                        )
                    else:
                        assert row is not None
                        row.process_status = "processing"
                        row.pending_event_id = command.event_id
                        row.updated_at = self._clock()
                        tx.update_conversation(row)
                        tx.save_result(
                            actor_id=actor_id,
                            fingerprint=digest,
                            conversation_id=conversation_id,
                            result=PendingCommandResult(
                                schema_version=1,
                                event_id=command.event_id,
                                correlation_id=correlation_id,
                                status="pending",
                            ),
                            now=self._clock(),
                        )
                        self._emit_snapshot(tx, row, command.event_id, correlation_id)
                if early is not None:
                    span.set_attribute("bff.seating.outcome", "checked")
                    if early.conversation_id:
                        self._notifier.notify(early.conversation_id)
                    return early.result
                self._notifier.notify(conversation_id)
                assert seat is not None and row is not None
                decided: WaiterSeatingResult | ErrorCode
                try:
                    decided = await self._waiter.decide_seating(
                        self._seating_call(row, correlation_id),
                        approved=payload.decision == "confirmed",
                        proposal_token=seat.token,
                    )
                except WaiterNoPendingDecisionError:
                    decided = ErrorCode.CONFLICT
                except (WaiterSeatingUnavailableError, WaiterUnavailableError):
                    decided = ErrorCode.UNAVAILABLE
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Never leave the conversation processing; the waiter keeps the truth.
                    logger.error(
                        "Seating decision failed (correlation %s): %s",
                        correlation_id,
                        type(exc).__name__,
                    )
                    decided = ErrorCode.UNAVAILABLE
                with self._db.write() as tx:
                    outcome = self._finish_decision(
                        tx, command, correlation_id, seat, decided
                    )
                    tx.update_result(actor_id, outcome.result, self._clock())
            span.set_attribute(
                "bff.seating.outcome",
                decided.outcome or "none" if isinstance(decided, WaiterSeatingResult) else decided,
            )
        self._notifier.notify(conversation_id)
        return outcome.result

    def _check_decision(
        self,
        tx: Transaction,
        session: DemoSession,
        command: DecideTableCommand,
        correlation_id: str,
    ) -> tuple[_Outcome | None, SeatingRow | None, ConversationRow | None]:
        payload = command.payload
        row = tx.get_conversation(command.conversation_id)
        if row is None:
            return _Outcome(
                self._failed(
                    command.event_id, correlation_id, ErrorCode.NOT_FOUND, NOT_FOUND_CONVERSATION
                )
            ), None, None
        if row.actor_id != session.actor.actor_id:
            return _Outcome(
                self._failed(
                    command.event_id, correlation_id, ErrorCode.FORBIDDEN, FORBIDDEN_CONVERSATION
                )
            ), None, None

        def fail(code: ErrorCode, message: str) -> tuple[_Outcome, None, ConversationRow]:
            return self._fail_in(tx, row, command, correlation_id, code, message), None, row

        prior = tx.get_seating_decision(payload.proposal_id)
        if prior is not None and prior.conversation_id == row.conversation_id:
            # A double click or a retry with another event id: same answer.
            if prior.decision == payload.decision and prior.outcome in ("seated", "rejected"):
                return self._complete(tx, row, command, correlation_id), None, row
            return fail(
                ErrorCode.CONFLICT,
                ALREADY_DECIDED if prior.outcome in ("seated", "rejected") else STALE_PROPOSAL,
            )
        seat = tx.get_seating(row.conversation_id)
        if seat is None or seat.status != "proposed":
            return fail(ErrorCode.CONFLICT, NO_PROPOSAL)
        if seat.proposal_id != payload.proposal_id or seat.version != payload.version:
            return fail(ErrorCode.CONFLICT, STALE_PROPOSAL)
        if row.process_status == "processing":
            return fail(ErrorCode.CONFLICT, BUSY)
        return None, seat, row

    def _finish_decision(
        self,
        tx: Transaction,
        command: DecideTableCommand,
        correlation_id: str,
        seat: SeatingRow,
        decided: WaiterSeatingResult | ErrorCode,
    ) -> _Outcome:
        now = self._clock()
        conversation_id = command.conversation_id
        row = tx.get_conversation(conversation_id)
        assert row is not None
        row.process_status = "idle"
        row.pending_event_id = None
        row.updated_at = now

        def fail(code: ErrorCode, message: str) -> _Outcome:
            tx.update_conversation(row)
            self._emit_snapshot(tx, row, command.event_id, correlation_id)
            return self._fail_in(tx, row, command, correlation_id, code, message)

        if decided == ErrorCode.UNAVAILABLE:
            return fail(ErrorCode.UNAVAILABLE, SEATING_UNAVAILABLE)
        if decided == ErrorCode.CONFLICT:
            # The waiter has no confirmation waiting for this card any more.
            tx.delete_seating(conversation_id)
            tx.save_seating_decision(
                proposal_id=seat.proposal_id,
                conversation_id=conversation_id,
                decision=command.payload.decision,
                outcome="stale",
                now=now,
            )
            return fail(ErrorCode.CONFLICT, STALE_PROPOSAL)
        assert isinstance(decided, WaiterSeatingResult)
        if decided.session_json is not None:
            row.agent_session_json = decided.session_json
        self._apply_report(tx, conversation_id, decided.seating)
        if decided.outcome == "unavailable":
            return fail(ErrorCode.UNAVAILABLE, SEATING_UNAVAILABLE)
        stored = {"confirmed": "seated", "rejected": "rejected"}.get(
            decided.outcome or "", decided.outcome or "stale"
        )
        tx.save_seating_decision(
            proposal_id=seat.proposal_id,
            conversation_id=conversation_id,
            decision=command.payload.decision,
            outcome=stored,
            now=now,
        )
        if decided.outcome not in ("confirmed", "rejected"):
            return fail(ErrorCode.CONFLICT, decided.reply or STALE_PROPOSAL)
        if decided.reply:
            tx.add_message(
                conversation_id,
                ChatMessage(
                    message_id=new_id("msg"),
                    role="assistant",
                    text=decided.reply,
                    occurred_at=now,
                    command_event_id=command.event_id,
                ),
            )
        tx.update_conversation(row)
        return self._complete(tx, row, command, correlation_id)

    async def _leave_seating_for_new_visit(
        self, session: DemoSession, command: ArriveCommand
    ) -> CommandResult | None:
        """/new: a pending proposal is rejected through the waiter; seated refuses."""

        actor_id = session.actor.actor_id
        with self._db.read() as tx:
            if tx.get_result(actor_id, command.event_id) is not None:
                return None
            latest = tx.latest_visit_id(actor_id)
            row = tx.get_conversation_by_visit(latest) if latest else None
            seat = tx.get_seating(row.conversation_id) if row is not None else None
        if row is None or seat is None:
            return None
        conversation_id = row.conversation_id
        async with self._lock(conversation_id):
            refusal: str | None = None
            with self._db.read() as tx:
                row = tx.get_conversation(conversation_id)
                seat = tx.get_seating(conversation_id)
            if row is None or seat is None:
                return None
            if row.process_status == "processing":
                # A message or a decision is in flight: its outcome decides.
                refusal = BUSY
            else:
                try:
                    if seat.status == "seated":
                        # The waiter may notice a release or a reset first.
                        await self._sync_locked(conversation_id)
                    else:
                        await self._reject_for_new_visit(conversation_id, seat)
                except WaiterError:
                    if seat.status == "proposed":
                        refusal = NEW_WHILE_PROPOSED.format(place=_place_text(seat))
            with self._db.read() as tx:
                current = tx.get_seating(conversation_id)
            if refusal is None and current is not None:
                template = NEW_WHILE_SEATED if current.status == "seated" else NEW_WHILE_PROPOSED
                refusal = template.format(place=_place_text(current))
            if refusal is None:
                return None
            correlation_id = new_id("corr")
            with self._db.write() as tx:
                if tx.get_result(actor_id, command.event_id) is not None:
                    return None
                old = tx.get_conversation(conversation_id)
                assert old is not None
                outcome = self._fail_in(
                    tx, old, command, correlation_id, ErrorCode.CONFLICT, refusal
                )
                tx.save_result(
                    actor_id=actor_id,
                    fingerprint=fingerprint(command),
                    conversation_id=conversation_id,
                    result=outcome.result,
                    now=self._clock(),
                )
        self._notifier.notify(conversation_id)
        return outcome.result

    async def _reject_for_new_visit(self, conversation_id: str, seat: SeatingRow) -> None:
        with self._db.read() as tx:
            row = tx.get_conversation(conversation_id)
        assert row is not None
        try:
            decided = await self._waiter.decide_seating(
                self._seating_call(row, new_id("corr")),
                approved=False,
                proposal_token=seat.token,
            )
        except WaiterNoPendingDecisionError:
            # Nothing waits on the waiter's side for this card: it was stale.
            with self._db.write() as tx:
                current = tx.get_seating(conversation_id)
                if (
                    current is not None
                    and current.status == "proposed"
                    and current.token == seat.token
                ):
                    tx.delete_seating(conversation_id)
            return
        with self._db.write() as tx:
            current = tx.get_conversation(conversation_id)
            assert current is not None
            if decided.session_json is not None:
                current.agent_session_json = decided.session_json
                tx.update_conversation(current)
            self._apply_report(tx, conversation_id, decided.seating)
            tx.save_seating_decision(
                proposal_id=seat.proposal_id,
                conversation_id=conversation_id,
                decision="rejected",
                outcome="abandoned",
                now=self._clock(),
            )
            self._emit_snapshot(tx, current, new_id("sync"), new_id("corr"))
        self._notifier.notify(conversation_id)

    # Projection and events

    def _snapshot(
        self, tx: Transaction, row: ConversationRow, cursor: int
    ) -> RestaurantSnapshot:
        memories = self._visible_memories(tx, row.actor_id)
        seat = tx.get_seating(row.conversation_id)
        return RestaurantSnapshot(
            schema_version=1,
            visit_id=row.visit_id,
            conversation_id=row.conversation_id,
            identity=ActorContext(actor_id=row.actor_id, authenticated=True),
            cursor=cursor,
            messages=tx.list_messages(row.conversation_id),
            customer=row.customer,
            order_draft=row.order_draft,
            pending_fields=missing_customer_fields(row.customer),
            memory=MemoryView(memories=memories),
            process_status="processing" if row.process_status == "processing" else "idle",
            allowed_actions=self._allowed_actions(row, memories, seat),
            seating=self._seating_view(seat),
        )

    def _visible_memories(self, tx: Transaction, actor_id: str) -> list[VisibleMemory]:
        records = sorted(
            self._memory.list_memories(actor_id),
            key=lambda record: (record.created_at, record.preference_id),
        )
        aliases = tx.memory_aliases(actor_id, [record.preference_id for record in records])
        memories = [
            VisibleMemory(
                memory_id=aliases[record.preference_id],
                kind=record.kind,
                value=record.value,
                source=record.source_conversation_id,
                recorded_at=record.updated_at,
            )
            for record in records
        ]
        return sorted(memories, key=lambda memory: int(memory.memory_id[1:]))

    def _allowed_actions(
        self,
        row: ConversationRow,
        memories: list[VisibleMemory],
        seat: SeatingRow | None = None,
    ) -> list[Action]:
        idle = row.process_status != "processing"
        actions = [Action.ARRIVE]
        if idle and row.turn_count < self._max_turns:
            actions.append(Action.SEND_MESSAGE)
        actions.append(Action.READ_MEMORY)
        if idle and memories:
            actions.extend((Action.CORRECT_MEMORY, Action.DELETE_MEMORY))
        if idle:
            actions.append(Action.CLEAR_MEMORY)
        if idle and seat is not None and seat.status == "proposed":
            actions.append(Action.DECIDE_TABLE)
        return actions

    def _complete(
        self, tx: Transaction, row: ConversationRow, command: Command, correlation_id: str
    ) -> _Outcome:
        cursor = self._emit_snapshot(tx, row, command.event_id, correlation_id)
        result = CompletedCommandResult(
            schema_version=1,
            event_id=command.event_id,
            correlation_id=correlation_id,
            status="completed",
            visit_id=row.visit_id,
            conversation_id=row.conversation_id,
            cursor=cursor,
        )
        self._emit_status(tx, row, command.event_id, correlation_id, result)
        return _Outcome(result, row.conversation_id)

    def _fail_in(
        self,
        tx: Transaction,
        row: ConversationRow,
        command: Command,
        correlation_id: str,
        code: ErrorCode,
        message: str,
    ) -> _Outcome:
        result = self._failed(command.event_id, correlation_id, code, message)
        self._emit_status(tx, row, command.event_id, correlation_id, result)
        return _Outcome(result, row.conversation_id)

    def _emit_snapshot(
        self, tx: Transaction, row: ConversationRow, command_event_id: str, correlation_id: str
    ) -> int:
        return tx.append_event(
            row,
            lambda cursor: SnapshotUpdated(
                **self._envelope(row, command_event_id, correlation_id, cursor),
                event_type="snapshot.updated",
                snapshot=self._snapshot(tx, row, cursor),
            ),
        )

    def _emit_status(
        self,
        tx: Transaction,
        row: ConversationRow,
        command_event_id: str,
        correlation_id: str,
        result: CommandResult,
    ) -> int:
        return tx.append_event(
            row,
            lambda cursor: CommandStatusChanged(
                **self._envelope(row, command_event_id, correlation_id, cursor),
                event_type="command.status_changed",
                result=result,
            ),
        )

    def _envelope(
        self, row: ConversationRow, command_event_id: str, correlation_id: str, cursor: int
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "event_id": new_id("evt"),
            "conversation_id": row.conversation_id,
            "command_event_id": command_event_id,
            "correlation_id": correlation_id,
            "occurred_at": self._clock(),
            "cursor": cursor,
        }

    @staticmethod
    def _failed(
        event_id: str, correlation_id: str, code: ErrorCode, message: str
    ) -> FailedCommandResult:
        return FailedCommandResult(
            schema_version=1,
            event_id=event_id,
            correlation_id=correlation_id,
            status="failed",
            error=PublicError(
                code=code, message=message, correlation_id=correlation_id, recovery="none"
            ),
        )

    @staticmethod
    def _owned(tx: Transaction, session: DemoSession, conversation_id: str) -> ConversationRow:
        row = tx.get_conversation(conversation_id)
        if row is None:
            raise PublicFailure(ErrorCode.NOT_FOUND, NOT_FOUND_CONVERSATION)
        if row.actor_id != session.actor.actor_id:
            raise PublicFailure(ErrorCode.FORBIDDEN, FORBIDDEN_CONVERSATION)
        return row
