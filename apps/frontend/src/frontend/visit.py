"""Projection of one visit for the view, fed only by the BffClient contract.

The view replaces its state with confirmed snapshots and never infers business
transitions from the waiter's text. A command in flight keeps its event_id, so
an interrupted rerun consults its result instead of sending it again.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from restaurant_contracts.application import (
    Action,
    ArriveCommand,
    ArrivePayload,
    ChatMessage,
    ClearMemoryCommand,
    Command,
    CommandResult,
    CommandStatusChanged,
    CorrectMemoryCommand,
    CorrectMemoryPayload,
    DeleteMemoryCommand,
    DeleteMemoryPayload,
    EmptyPayload,
    ErrorCode,
    ReadMemoryCommand,
    ResponseTextDelta,
    RestaurantSnapshot,
    SendMessageCommand,
    SendMessagePayload,
    SnapshotUpdated,
    VisibleMemory,
)
from restaurant_contracts.client import BffClient, BffClientError

Updater = Callable[[], None]
CommandFactory = Callable[[str, datetime, str], Command]

REMEMBERED = "Lo que recuerdo de ti:"
NOTHING_REMEMBERED = "Aún no recuerdo nada de ti."
FORGOTTEN = "He olvidado todo lo que sabía de ti."
KEEPS_REMEMBERING = "Lo que me cuentes a partir de ahora lo volveré a recordar."
UNKNOWN_COMMAND = "Ese comando no lo conozco: mira la lista de la izquierda."
TOO_LONG_MEMORY = "Eso es demasiado largo para un recuerdo."
NOT_ALLOWED = "Ahora mismo no puedo hacer eso."
MESSAGES_CLOSED = "Ahora mismo no puedo recibir más mensajes."
TRY_NEW_VISIT = "Prueba con /new."


@dataclass(frozen=True)
class Card:
    """A note placed in the conversation after the message it follows."""

    after_message_id: str | None
    title: str
    memories: tuple[VisibleMemory, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class ConversationView:
    messages: tuple[ChatMessage, ...] = ()
    cards: tuple[Card, ...] = ()
    outgoing: str | None = None
    provisional: tuple[tuple[str, str], ...] = ()
    waiting: bool = False


class VisitSession:
    """Synchronous facade over the async client for Streamlit's script thread."""

    def __init__(
        self,
        client: BffClient,
        *,
        new_event_id: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._new_event_id = new_event_id or (lambda: f"cmd_{uuid.uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self.snapshot: RestaurantSnapshot | None = None
        self.cursor = 0
        self.cards: list[Card] = []
        self.pending: Command | None = None
        self.outgoing: str | None = None
        self._provisional: dict[str, str] = {}
        self._reported: str | None = None

    @property
    def name(self) -> str:
        if self.snapshot is None:
            return ""
        return self.snapshot.customer.presented_name or self.snapshot.identity.actor_id

    @property
    def greeting_id(self) -> str | None:
        """First waiter message of the visit, revealed by the entrance choreography."""

        if self.snapshot and self.snapshot.messages and self.snapshot.messages[0].role == "assistant":
            return self.snapshot.messages[0].message_id
        return None

    def allows(self, action: Action) -> bool:
        return self.snapshot is not None and action in self.snapshot.allowed_actions

    def view(self) -> ConversationView:
        messages = tuple(self.snapshot.messages) if self.snapshot else ()
        confirmed = {message.message_id for message in messages}
        provisional = tuple(
            (message_id, text)
            for message_id, text in self._provisional.items()
            if message_id not in confirmed
        )
        outgoing = self.outgoing
        if outgoing is not None and self.pending is not None and any(
            message.role == "user" and message.command_event_id == self.pending.event_id
            for message in messages
        ):
            outgoing = None
        processing = self.snapshot is not None and self.snapshot.process_status == "processing"
        waiting = (processing or outgoing is not None) and not provisional
        return ConversationView(messages, tuple(self.cards), outgoing, provisional, waiting)

    def arrive(self, resume_visit_id: str | None = None) -> None:
        if self.snapshot is not None and not self.allows(Action.ARRIVE):
            self._notice(NOT_ALLOWED)
            return
        try:
            command = ArriveCommand(
                schema_version=1,
                event_id=self._new_event_id(),
                occurred_at=self._clock(),
                event_type="customer.arrived",
                payload=ArrivePayload(resume_visit_id=resume_visit_id),
            )
        except ValidationError:
            self._notice(UNKNOWN_COMMAND)
            return
        self._run(self._dispatch(command, None))

    def send_message(self, text: str, on_update: Updater | None = None) -> None:
        if self.snapshot is None:
            return
        if not self.allows(Action.SEND_MESSAGE):
            hint = f" {TRY_NEW_VISIT}" if self.allows(Action.ARRIVE) else ""
            self._notice(MESSAGES_CLOSED + hint)
            _notify(on_update)
            return
        try:
            command = SendMessageCommand(
                schema_version=1,
                event_id=self._new_event_id(),
                occurred_at=self._clock(),
                event_type="conversation.message_sent",
                conversation_id=self.snapshot.conversation_id,
                payload=SendMessagePayload(message=text),
            )
        except ValidationError:
            return
        self.outgoing = command.payload.message
        _notify(on_update)
        try:
            self._run(self._dispatch(command, on_update))
        finally:
            self.outgoing = None
        _notify(on_update)

    def read_memory(self, on_update: Updater | None = None) -> None:
        self._send(
            lambda event_id, at, conversation_id: ReadMemoryCommand(
                schema_version=1,
                event_id=event_id,
                occurred_at=at,
                event_type="memory.read_requested",
                conversation_id=conversation_id,
                payload=EmptyPayload(),
            ),
            on_update,
            action=Action.READ_MEMORY,
        )

    def correct_memory(
        self, memory_id: str, value: str, on_update: Updater | None = None
    ) -> None:
        self._send(
            lambda event_id, at, conversation_id: CorrectMemoryCommand(
                schema_version=1,
                event_id=event_id,
                occurred_at=at,
                event_type="memory.correction_requested",
                conversation_id=conversation_id,
                payload=CorrectMemoryPayload(memory_id=memory_id, value=value),
            ),
            on_update,
            action=Action.CORRECT_MEMORY,
            invalid=TOO_LONG_MEMORY,
        )

    def delete_memory(self, memory_id: str, on_update: Updater | None = None) -> None:
        self._send(
            lambda event_id, at, conversation_id: DeleteMemoryCommand(
                schema_version=1,
                event_id=event_id,
                occurred_at=at,
                event_type="memory.deletion_requested",
                conversation_id=conversation_id,
                payload=DeleteMemoryPayload(memory_id=memory_id),
            ),
            on_update,
            action=Action.DELETE_MEMORY,
        )

    def clear_memory(self, on_update: Updater | None = None) -> None:
        self._send(
            lambda event_id, at, conversation_id: ClearMemoryCommand(
                schema_version=1,
                event_id=event_id,
                occurred_at=at,
                event_type="memory.clear_requested",
                conversation_id=conversation_id,
                payload=EmptyPayload(),
            ),
            on_update,
            action=Action.CLEAR_MEMORY,
        )

    def reject_unknown_command(self) -> None:
        self._notice(UNKNOWN_COMMAND)

    def resolve_pending(self, on_update: Updater | None = None) -> None:
        """Consult a command left in flight by an interrupted run, without resending it."""

        if self.pending is not None:
            self._run(self._resolve(self.pending, on_update))

    def _send(
        self,
        factory: CommandFactory,
        on_update: Updater | None,
        *,
        action: Action,
        invalid: str = UNKNOWN_COMMAND,
    ) -> None:
        if self.snapshot is None:
            return
        if not self.allows(action):
            self._notice(NOT_ALLOWED)
            _notify(on_update)
            return
        try:
            command = factory(self._new_event_id(), self._clock(), self.snapshot.conversation_id)
        except ValidationError:
            self._notice(invalid)
            _notify(on_update)
            return
        self._run(self._dispatch(command, on_update))

    async def _dispatch(self, command: Command, on_update: Updater | None) -> None:
        self.pending = command
        try:
            result = await self._client.submit(command)
        except BffClientError as exc:
            self._fail_in_transport(exc)
            return
        await self._settle(command, result, on_update)

    async def _resolve(self, command: Command, on_update: Updater | None) -> None:
        try:
            result = await self._client.get_result(command.event_id)
        except BffClientError as exc:
            if exc.error.code == ErrorCode.NOT_FOUND:
                await self._dispatch(command, on_update)
            else:
                self._fail_in_transport(exc)
            return
        await self._settle(command, result, on_update)

    async def _settle(
        self, command: Command, result: CommandResult, on_update: Updater | None
    ) -> None:
        try:
            follows = (
                not isinstance(command, ArriveCommand)
                and self.snapshot is not None
                and result.status != "failed"
            )
            if follows:
                until = result.cursor if result.status == "completed" else None
                terminal = await self._follow(command.event_id, on_update, until_cursor=until)
                if terminal is not None:
                    result = terminal
                elif result.status == "pending":
                    result = await self._client.get_result(command.event_id)
            if result.status == "completed":
                await self._refresh(result.conversation_id, result.cursor)
        except BffClientError as exc:
            self._fail_in_transport(exc)
            return
        if result.status == "pending":
            return
        self.pending = None
        self._reported = None
        if result.status == "failed":
            self._notice(result.error.message)
        else:
            self._conclude(command)
        _notify(on_update)

    async def _follow(
        self,
        command_event_id: str,
        on_update: Updater | None,
        *,
        until_cursor: int | None = None,
    ) -> CommandResult | None:
        """Apply confirmed events until the command resolves or its known cursor is reached.

        A live SSE stream never ends on its own, so a completed result stops at
        the cursor that confirms it instead of waiting for more events.
        """

        assert self.snapshot is not None
        if until_cursor is not None and self.cursor >= until_cursor:
            return None
        conversation_id = self.snapshot.conversation_id
        stream = self._client.events(conversation_id, after_cursor=self.cursor)
        terminal: CommandResult | None = None
        try:
            async for event in stream:
                if event.cursor <= self.cursor:
                    continue
                self.cursor = event.cursor
                if isinstance(event, SnapshotUpdated):
                    self._apply_snapshot(event.snapshot)
                elif isinstance(event, ResponseTextDelta):
                    self._provisional[event.message_id] = (
                        self._provisional.get(event.message_id, "") + event.delta
                    )
                elif (
                    isinstance(event, CommandStatusChanged)
                    and event.command_event_id == command_event_id
                    and event.result.status != "pending"
                ):
                    terminal = event.result
                _notify(on_update)
                if terminal is not None or (
                    until_cursor is not None and self.cursor >= until_cursor
                ):
                    break
        except BffClientError as exc:
            if exc.error.recovery != "fetch_snapshot":
                raise
            self._apply_snapshot(await self._client.get_snapshot(conversation_id))
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        return terminal

    async def _refresh(self, conversation_id: str, cursor: int) -> None:
        if (
            self.snapshot is None
            or self.snapshot.conversation_id != conversation_id
            or self.snapshot.cursor < cursor
        ):
            self._apply_snapshot(await self._client.get_snapshot(conversation_id))

    def _apply_snapshot(self, snapshot: RestaurantSnapshot) -> None:
        current = self.snapshot
        if current is not None and current.conversation_id != snapshot.conversation_id:
            self.cards.clear()
            self._provisional.clear()
            self.cursor = 0
        elif current is not None and snapshot.cursor < current.cursor:
            return
        self.snapshot = snapshot
        self.cursor = max(self.cursor, snapshot.cursor)
        for message in snapshot.messages:
            self._provisional.pop(message.message_id, None)

    def _conclude(self, command: Command) -> None:
        memories = tuple(self.snapshot.memory.memories) if self.snapshot else ()
        if isinstance(command, ReadMemoryCommand):
            self._card(REMEMBERED if memories else NOTHING_REMEMBERED, memories)
        elif isinstance(command, ClearMemoryCommand):
            self._card(FORGOTTEN, note=KEEPS_REMEMBERING)
        elif isinstance(command, DeleteMemoryCommand):
            self._card(f"He borrado {command.payload.memory_id}.", memories)
        elif isinstance(command, CorrectMemoryCommand):
            self._card(f"He corregido {command.payload.memory_id}.", memories)

    def _fail_in_transport(self, exc: BffClientError) -> None:
        if exc.error.recovery != "retry_same_command":
            self.pending = None
            self._reported = None
        elif self.pending is not None:
            if self._reported == self.pending.event_id:
                return
            self._reported = self.pending.event_id
        self._notice(exc.error.message)

    def _notice(self, message: str) -> None:
        self._card(message)

    def _card(
        self, title: str, memories: tuple[VisibleMemory, ...] = (), note: str | None = None
    ) -> None:
        last = (
            self.snapshot.messages[-1].message_id
            if self.snapshot is not None and self.snapshot.messages
            else None
        )
        self.cards.append(Card(last, title, memories, note))

    @staticmethod
    def _run(work: Coroutine[Any, Any, None]) -> None:
        asyncio.run(work)


def _notify(on_update: Updater | None) -> None:
    if on_update is not None:
        on_update()
