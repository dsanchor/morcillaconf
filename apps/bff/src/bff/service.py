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
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

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
from restaurant_contracts.customer import CustomerSnapshot

from restaurant_agent.contracts import missing_customer_fields
from restaurant_agent.memory.store import (
    DurableMemoryRepository,
    MemoryConflictError,
    MemoryNotFoundError,
)

from bff.greeting import greeting
from bff.identity import InvalidNameError, actor_id_for, presented_name
from bff.notifier import Notifier
from bff.storage import ConversationRow, Database, Transaction
from bff.telemetry import tracer
from bff.waiter import (
    WaiterInvalidResponseError,
    WaiterPort,
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

    @property
    def waiter_mode(self) -> str:
        return self._waiter.mode

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
            task = asyncio.get_running_loop().create_task(self._run_turn(outcome.turn))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return outcome.result

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
        with tracer.start_as_current_span(
            "bff.waiter.turn",
            attributes={
                "bff.conversation_id": job.conversation_id,
                "bff.correlation_id": job.correlation_id,
                "bff.waiter.mode": self._waiter.mode,
            },
        ) as span:
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
            )
            outcome: WaiterTurnResult | tuple[ErrorCode, str]
            try:
                outcome = await self._waiter.take_turn(turn)
                if not outcome.reply.strip():
                    raise WaiterInvalidResponseError("Empty waiter reply")
            except WaiterTurnLimitError:
                outcome = (ErrorCode.TURN_LIMIT_EXCEEDED, TURN_LIMIT)
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
        self._notifier.notify(job.conversation_id)

    def _finish_turn(
        self, job: _TurnJob, outcome: WaiterTurnResult | tuple[ErrorCode, str]
    ) -> None:
        now = self._clock()
        with self._db.write() as tx:
            row = tx.get_conversation(job.conversation_id)
            if row is None or row.pending_event_id != job.event_id:
                return
            if isinstance(outcome, WaiterTurnResult):
                row.customer = outcome.customer
                row.order_draft = outcome.order_draft
                row.turn_count = outcome.turn_count
                row.persisted_order_preferences = list(outcome.persisted_order_preferences)
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

    # Projection and events

    def _snapshot(
        self, tx: Transaction, row: ConversationRow, cursor: int
    ) -> RestaurantSnapshot:
        memories = self._visible_memories(tx, row.actor_id)
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
            allowed_actions=self._allowed_actions(row, memories),
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
        self, row: ConversationRow, memories: list[VisibleMemory]
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
