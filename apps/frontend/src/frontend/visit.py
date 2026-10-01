"""Projection of one visit for the view, fed only by the BffClient contract.

The view replaces its state with confirmed snapshots and never infers business
transitions from the waiter's text. A command in flight keeps its event_id, so
an interrupted rerun consults its result instead of sending it again.
"""

from __future__ import annotations

import asyncio
import time
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
    Command,
    CommandResult,
    CommandStatusChanged,
    DecideTableCommand,
    EmptyPayload,
    EndVisitCommand,
    ErrorCode,
    ResponseTextDelta,
    RestaurantSnapshot,
    SendMessageCommand,
    SendMessagePayload,
    SnapshotUpdated,
    TableDecisionPayload,
)
from restaurant_contracts.client import BffClient, BffClientError
from restaurant_contracts.seating import RoomView

Updater = Callable[[], None]
CommandFactory = Callable[[str, datetime, str], Command]

UNKNOWN_COMMAND = "Ese comando no lo conozco: mira la lista de la izquierda."
NOT_ALLOWED = "Ahora mismo no puedo hacer eso."
MESSAGES_CLOSED = "Ahora mismo no puedo recibir más mensajes."
TRY_NEW_VISIT = "Prueba con /new."


@dataclass(frozen=True)
class Card:
    """A note placed in the conversation after the message it follows."""

    after_message_id: str | None
    title: str
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
        self._last_completed_event_id: str | None = None
        self.room: RoomView | None = None
        # Monotonic time when this browser saw its group sit down: the walk
        # to the seats plays once from here, never after a reload.
        self.seated_since: float | None = None
        self._monotonic = time.monotonic

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

    def decide_table(self, decision: str, on_update: Updater | None = None) -> None:
        """Confirm or reject the pending proposal with its id and version."""

        seating = self.snapshot.seating if self.snapshot is not None else None
        if seating is None or seating.proposal is None or self.pending is not None:
            # A second click after the first one decided: nothing to do.
            return
        proposal = seating.proposal
        self._send(
            lambda event_id, at, conversation_id: DecideTableCommand(
                schema_version=1,
                event_id=event_id,
                occurred_at=at,
                event_type="table.confirmation_decided",
                conversation_id=conversation_id,
                payload=TableDecisionPayload(
                    proposal_id=proposal.proposal_id,
                    version=proposal.version,
                    decision=decision,
                ),
            ),
            on_update,
            action=Action.DECIDE_TABLE,
        )

    def exit(self, on_update: Updater | None = None) -> bool:
        """Release this visit's seating; return whether the backend confirmed it."""

        if self.snapshot is None or self.pending is not None:
            return False
        event_id = self._new_event_id()
        try:
            command = EndVisitCommand(
                schema_version=1,
                event_id=event_id,
                occurred_at=self._clock(),
                event_type="visit.end_requested",
                conversation_id=self.snapshot.conversation_id,
                payload=EmptyPayload(),
            )
        except ValidationError:
            self._notice(UNKNOWN_COMMAND)
            return False
        if not self.allows(Action.END_VISIT):
            self._notice(NOT_ALLOWED)
            return False
        self._run(self._dispatch(command, on_update))
        return self._last_completed_event_id == event_id

    def walk_elapsed(self) -> float | None:
        """Seconds since this browser saw the group sit down, for the walk."""

        if self.seated_since is None:
            return None
        return self._monotonic() - self.seated_since

    def refresh_room(self) -> bool:
        """Read the room; returns True when it revealed a newer own seating."""

        if self.snapshot is None:
            return False
        try:
            self.room = self._run_value(self._client.get_room(self.snapshot.conversation_id))
        except BffClientError:
            return False
        if self.room is None or not self.room.seating_enabled:
            return False
        mine = any(place.mine for place in self.room.places)
        seated_in_room = any(
            place.mine
            and (
                place.state == "occupied"
                if place.kind == "table"
                else any(seat.mine and seat.state == "occupied" for seat in place.seats)
            )
            for place in self.room.places
        )
        status = self.snapshot.seating.status
        consistent = (
            (status == "seated" and seated_in_room)
            or (status == "none" and not mine)
            or (status == "proposed" and mine and not seated_in_room)
        )
        if consistent or self.pending is not None:
            return False
        previous = self.snapshot
        try:
            self._run(self._reload(previous.conversation_id))
        except BffClientError:
            return False
        return self.snapshot is not None and self.snapshot.seating != previous.seating

    async def _reload(self, conversation_id: str) -> None:
        self._apply_snapshot(await self._client.get_snapshot(conversation_id))

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
                if (
                    isinstance(command, ArriveCommand)
                    and self.snapshot is not None
                    and self.snapshot.process_status == "processing"
                ):
                    # A resumed visit whose waiter is still answering a message
                    # sent before the reload: follow it until it is idle.
                    await self._follow(None, on_update, until_idle=True)
        except BffClientError as exc:
            self._fail_in_transport(exc)
            return
        if result.status == "pending":
            return
        self.pending = None
        self._reported = None
        if result.status == "failed":
            self._notice(result.error.message)
            if isinstance(command, DecideTableCommand) and self.snapshot is not None:
                # An expired or stale proposal is gone: show the confirmed state.
                try:
                    await self._reload(self.snapshot.conversation_id)
                except BffClientError:
                    pass
        else:
            self._last_completed_event_id = command.event_id
            self._conclude(command)
        _notify(on_update)

    async def _follow(
        self,
        command_event_id: str | None,
        on_update: Updater | None,
        *,
        until_cursor: int | None = None,
        until_idle: bool = False,
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
                idle = until_idle and self.snapshot.process_status != "processing"
                if idle or terminal is not None or (
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
            self.room = None
            self.seated_since = None
        elif current is not None and snapshot.cursor < current.cursor:
            return
        self.snapshot = snapshot
        self.cursor = max(self.cursor, snapshot.cursor)
        for message in snapshot.messages:
            self._provisional.pop(message.message_id, None)

    def _conclude(self, command: Command) -> None:
        if (
            isinstance(command, DecideTableCommand)
            and self.snapshot is not None
            and self.snapshot.seating.status == "seated"
        ):
            self.seated_since = self._monotonic()

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

    def _card(self, title: str, note: str | None = None) -> None:
        last = (
            self.snapshot.messages[-1].message_id
            if self.snapshot is not None and self.snapshot.messages
            else None
        )
        self.cards.append(Card(last, title, note))

    @staticmethod
    def _run(work: Coroutine[Any, Any, None]) -> None:
        asyncio.run(work)

    @staticmethod
    def _run_value(work: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run(work)


def _notify(on_update: Updater | None) -> None:
    if on_update is not None:
        on_update()
