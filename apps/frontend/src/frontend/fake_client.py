"""Simulated BFF, the default adapter; ``HttpBffClient`` talks to the real one.

Everything here is fake and presented as such: an in-memory restaurant that
answers with valid 3A contracts and a waiter that only knows a few scripted
phrases. It never imports agents, Agent Framework, Foundry, MCP or a database.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from restaurant_contracts.application import (
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
    ResponseTextDelta,
    RestaurantSnapshot,
    SendMessageCommand,
    SnapshotUpdated,
    StreamEvent,
    VisibleMemory,
)
from restaurant_contracts.client import BffClientError
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft, PendingField
from restaurant_contracts.memory import MemoryKind

from frontend.greeting import greeting

ProcessStatus = Literal["idle", "processing", "awaiting_customer"]

MAX_MEMORIES_PER_KIND = 20
MAX_VALUES_PER_MESSAGE = 5
MAX_CUSTOMER_ITEMS = 20
MESSAGE_LIMIT = 2_000
MEMORY_LIMIT = 200
SCRIPTED_REPLIES = ("Tomo nota.", "Entendido, {name}.", "Te escucho.")

_CLAUSES = re.compile(
    r"[.;!?]+|(?:,\s*|\s+y\s+)(?=(?:y\s+)?(?:soy|prefiero|tengo|me)\b)", re.IGNORECASE
)
_RESTRICTION = re.compile(
    r"\b(?:al[eé]rgic[oa]s?|alergia|intolerante|intolerancia)\s+(?:a|al)\s+(?P<value>.+)",
    re.IGNORECASE,
)
_PREFERENCE = re.compile(r"\bprefiero\s+(?P<value>.+)", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:el|la|los|las|un|una|unos|unas)\s+", re.IGNORECASE)
_WORD = re.compile(r"\S+\s*")


def extract_memories(text: str) -> tuple[list[str], list[str]]:
    """Scripted stand-in for the waiter: «prefiero …» and «soy alérgica a …» only."""

    preferences: list[str] = []
    restrictions: list[str] = []
    for clause in _CLAUSES.split(text):
        for pattern, found in ((_RESTRICTION, restrictions), (_PREFERENCE, preferences)):
            match = pattern.search(clause)
            if match is None:
                continue
            value = _ARTICLE.sub("", match["value"].strip(" \t\n\"'¡¿«»"))[:MEMORY_LIMIT]
            value = value.strip()
            if value and len(found) < MAX_VALUES_PER_MESSAGE:
                found.append(value)
            break
    return preferences, restrictions


def scripted_reply(
    name: str, preferences: list[str], restrictions: list[str], turn: int
) -> str:
    """The simulated waiter's answer; nothing here reaches the real model."""

    if preferences and restrictions:
        pronoun = "las" if len(restrictions) > 1 else "la"
        text = (
            f"Apuntado: {_join(preferences)}. Y tu alergia a {_join(restrictions)}, "
            f"te {pronoun} preguntaré en cada visita."
        )
    elif restrictions:
        text = f"Apuntado: {_join(restrictions)}. Te lo preguntaré en cada visita."
    elif preferences:
        text = f"Apuntado: {_join(preferences)}."
    else:
        text = SCRIPTED_REPLIES[turn % len(SCRIPTED_REPLIES)].format(name=name)
    return text if len(text) <= MESSAGE_LIMIT else f"{text[: MESSAGE_LIMIT - 1]}…"


def _join(values: list[str]) -> str:
    return values[0] if len(values) == 1 else f"{', '.join(values[:-1])} y {values[-1]}"


def _chunks(text: str, words_per_chunk: int = 3) -> list[str]:
    words = _WORD.findall(text)
    return [
        "".join(words[index : index + words_per_chunk])
        for index in range(0, len(words), words_per_chunk)
    ] or [text]


@dataclass(frozen=True)
class _Failure:
    code: ErrorCode
    message: str


@dataclass(frozen=True)
class _LoggedEvent:
    event: StreamEvent
    pause: float = 0.0


@dataclass
class _StoredResult:
    fingerprint: dict[str, Any]
    result: CommandResult


@dataclass
class _Conversation:
    conversation_id: str
    visit_id: str
    owner: ActorContext
    presented_name: str
    messages: list[ChatMessage] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    events: list[_LoggedEvent] = field(default_factory=list)
    cursor: int = 0
    process_status: ProcessStatus = "idle"
    turns: int = 0


class FakeRestaurant:
    """Server-side state of the simulation, shared by the clients of one browser session.

    Memories belong to the identity, so they survive a new visit or leaving and
    entering again with the same name while the session lives.
    """

    def __init__(
        self,
        *,
        pause_seconds: float = 0.0,
        max_turns: int = 20,
        retained_events: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if pause_seconds < 0:
            raise ValueError("pause_seconds cannot be negative")
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if retained_events is not None and retained_events < 1:
            raise ValueError("retained_events must be positive")
        self.pause_seconds = pause_seconds
        self.max_turns = max_turns
        self.retained_events = retained_events
        self._clock = clock or (lambda: datetime.now(UTC))
        self._conversations: dict[str, _Conversation] = {}
        self._visits: dict[str, str] = {}
        self._active_visits: dict[str, str] = {}
        self._results: dict[tuple[str, str], _StoredResult] = {}
        self._memories: dict[str, list[VisibleMemory]] = {}
        self._memory_numbers: dict[str, int] = {}
        self._counters: dict[str, int] = {}

    def client(self, identity: ActorContext) -> FakeBffClient:
        return FakeBffClient(self, identity)

    def submit(self, identity: ActorContext, command: Command) -> CommandResult:
        key = (identity.actor_id, command.event_id)
        fingerprint = command.model_dump(mode="json")
        stored = self._results.get(key)
        if stored is not None:
            if stored.fingerprint != fingerprint:
                raise self._client_error(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Ese identificador de comando ya se usó con otro contenido.",
                )
            return stored.result
        result = self._execute(identity, command, self._next_id("corr"))
        self._results[key] = _StoredResult(fingerprint, result)
        return result

    def active_visit_id(self, identity: ActorContext) -> str | None:
        """Latest visit of the identity, resumed when it enters again."""

        return self._active_visits.get(identity.actor_id)

    def get_result(self, identity: ActorContext, event_id: str) -> CommandResult:
        stored = self._results.get((identity.actor_id, event_id))
        if stored is None:
            raise self._client_error(ErrorCode.NOT_FOUND, "No conozco ese comando.")
        return stored.result

    def get_snapshot(self, identity: ActorContext, conversation_id: str) -> RestaurantSnapshot:
        conversation = self._owned(identity, conversation_id)
        return self._snapshot(conversation, conversation.cursor, conversation.process_status)

    async def stream(
        self, identity: ActorContext, conversation_id: str, after_cursor: int
    ) -> AsyncIterator[StreamEvent]:
        """Replay the confirmed log after the cursor, then end; SSE will stay open in 3C."""

        conversation = self._owned(identity, conversation_id)
        if isinstance(after_cursor, bool) or not isinstance(after_cursor, int) or after_cursor < 0:
            raise self._client_error(ErrorCode.INVALID_COMMAND, "El cursor no es válido.")
        if after_cursor > conversation.cursor:
            raise self._client_error(
                ErrorCode.CONFLICT, "Ese cursor todavía no existe en esta conversación."
            )
        oldest = conversation.events[0].event.cursor if conversation.events else conversation.cursor + 1
        if after_cursor + 1 < oldest:
            raise self._client_error(
                ErrorCode.CURSOR_EXPIRED,
                "El cursor ha caducado. Recupera un nuevo snapshot.",
                recovery="fetch_snapshot",
            )
        for logged in list(conversation.events):
            if logged.event.cursor <= after_cursor:
                continue
            if logged.pause:
                await asyncio.sleep(logged.pause)
            yield logged.event

    def _execute(
        self, identity: ActorContext, command: Command, correlation_id: str
    ) -> CommandResult:
        if isinstance(command, ArriveCommand):
            return self._arrive(identity, command, correlation_id)
        conversation = self._conversations.get(command.conversation_id)
        if conversation is None:
            return self._failed(command, correlation_id, ErrorCode.NOT_FOUND, "No encuentro esa conversación.")
        if conversation.owner.actor_id != identity.actor_id:
            return self._failed(
                command, correlation_id, ErrorCode.FORBIDDEN, "No tienes acceso a esta conversación."
            )
        self._publish_status(conversation, command, correlation_id, self._pending(command, correlation_id))
        failure = self._apply(conversation, command, correlation_id)
        if failure is not None:
            result = self._failed(command, correlation_id, failure.code, failure.message)
            self._publish_status(conversation, command, correlation_id, result)
            return result
        return self._complete(conversation, command, correlation_id)

    def _arrive(
        self, identity: ActorContext, command: ArriveCommand, correlation_id: str
    ) -> CommandResult:
        resume = command.payload.resume_visit_id
        if resume is not None:
            conversation_id = self._visits.get(resume)
            if conversation_id is None:
                return self._failed(command, correlation_id, ErrorCode.NOT_FOUND, "No encuentro esa visita.")
            conversation = self._conversations[conversation_id]
            if conversation.owner.actor_id != identity.actor_id:
                return self._failed(command, correlation_id, ErrorCode.FORBIDDEN, "Esa visita no es tuya.")
            self._publish_status(conversation, command, correlation_id, self._pending(command, correlation_id))
            return self._complete(conversation, command, correlation_id)
        conversation = self._open_visit(identity)
        self._publish_status(conversation, command, correlation_id, self._pending(command, correlation_id))
        self._say(conversation, command, correlation_id, greeting(conversation.presented_name), pause=0.0)
        return self._complete(conversation, command, correlation_id)

    def _apply(
        self, conversation: _Conversation, command: Command, correlation_id: str
    ) -> _Failure | None:
        if isinstance(command, SendMessageCommand):
            return self._converse(conversation, command, correlation_id)
        if not conversation.owner.authenticated:
            return _Failure(ErrorCode.FORBIDDEN, "Los invitados no tienen recuerdos guardados.")
        memories = self._memories.setdefault(conversation.owner.actor_id, [])
        if isinstance(command, ReadMemoryCommand):
            return None
        if isinstance(command, ClearMemoryCommand):
            memories.clear()
            return None
        index = next(
            (i for i, memory in enumerate(memories) if memory.memory_id == command.payload.memory_id),
            None,
        )
        if index is None:
            return _Failure(
                ErrorCode.NOT_FOUND,
                f"No recuerdo nada con el identificador {command.payload.memory_id}.",
            )
        if isinstance(command, DeleteMemoryCommand):
            del memories[index]
        elif isinstance(command, CorrectMemoryCommand):
            memories[index] = memories[index].model_copy(
                update={
                    "value": command.payload.value,
                    "source": conversation.conversation_id,
                    "recorded_at": self._clock(),
                }
            )
        return None

    def _converse(
        self, conversation: _Conversation, command: SendMessageCommand, correlation_id: str
    ) -> _Failure | None:
        if conversation.turns >= self.max_turns:
            return _Failure(
                ErrorCode.TURN_LIMIT_EXCEEDED,
                "Hemos llegado al límite de mensajes de esta visita. Escribe /new para empezar otra.",
            )
        conversation.turns += 1
        text = command.payload.message
        self._add_message(conversation, "user", text, command.event_id)
        self._publish_snapshot(conversation, command, correlation_id, "processing")
        preferences, restrictions = extract_memories(text)
        self._note(conversation.preferences, preferences)
        self._note(conversation.restrictions, restrictions)
        if conversation.owner.authenticated:
            for kind, values in (
                (MemoryKind.PREFERENCE, preferences),
                (MemoryKind.RESTRICTION, restrictions),
            ):
                for value in values:
                    self._remember(conversation, kind, value)
        else:
            preferences, restrictions = [], []
        reply = scripted_reply(
            conversation.presented_name, preferences, restrictions, conversation.turns - 1
        )
        self._say(conversation, command, correlation_id, reply, pause=self.pause_seconds)
        return None

    def _say(
        self,
        conversation: _Conversation,
        command: Command,
        correlation_id: str,
        text: str,
        *,
        pause: float,
    ) -> None:
        message_id = self._next_id("msg")
        for index, chunk in enumerate(_chunks(text)):
            self._publish(
                conversation,
                lambda cursor, chunk=chunk: ResponseTextDelta(
                    **self._envelope(conversation, command, correlation_id, cursor),
                    event_type="conversation.text_delta",
                    message_id=message_id,
                    delta=chunk,
                    provisional=True,
                ),
                pause=pause if index == 0 else pause / 8,
            )
        self._add_message(conversation, "assistant", text, command.event_id, message_id)

    def _open_visit(self, identity: ActorContext) -> _Conversation:
        conversation = _Conversation(
            conversation_id=self._next_id("conv"),
            visit_id=self._next_id("visit"),
            owner=identity,
            presented_name=" ".join(identity.actor_id.split())[:100],
        )
        self._conversations[conversation.conversation_id] = conversation
        self._visits[conversation.visit_id] = conversation.conversation_id
        self._active_visits[identity.actor_id] = conversation.visit_id
        return conversation

    def _remember(self, conversation: _Conversation, kind: MemoryKind, value: str) -> None:
        actor_id = conversation.owner.actor_id
        memories = self._memories.setdefault(actor_id, [])
        if any(m.kind == kind and m.value.casefold() == value.casefold() for m in memories):
            return
        same_kind = [m for m in memories if m.kind == kind]
        if len(same_kind) >= MAX_MEMORIES_PER_KIND:
            memories.remove(same_kind[0])
        number = self._memory_numbers.get(actor_id, 0) + 1
        self._memory_numbers[actor_id] = number
        memories.append(
            VisibleMemory(
                memory_id=f"m{number}",
                kind=kind,
                value=value,
                source=conversation.conversation_id,
                recorded_at=self._clock(),
            )
        )

    @staticmethod
    def _note(current: list[str], values: list[str]) -> None:
        for value in values:
            if value not in current and len(current) < MAX_CUSTOMER_ITEMS:
                current.append(value)

    def _add_message(
        self,
        conversation: _Conversation,
        role: Literal["user", "assistant"],
        text: str,
        command_event_id: str,
        message_id: str | None = None,
    ) -> None:
        conversation.messages.append(
            ChatMessage(
                message_id=message_id or self._next_id("msg"),
                role=role,
                text=text,
                occurred_at=self._clock(),
                command_event_id=command_event_id,
            )
        )

    def _complete(
        self, conversation: _Conversation, command: Command, correlation_id: str
    ) -> CommandResult:
        cursor = self._publish_snapshot(conversation, command, correlation_id, "idle")
        result = CompletedCommandResult(
            schema_version=1,
            event_id=command.event_id,
            correlation_id=correlation_id,
            status="completed",
            visit_id=conversation.visit_id,
            conversation_id=conversation.conversation_id,
            cursor=cursor,
        )
        self._publish_status(conversation, command, correlation_id, result)
        return result

    def _publish_snapshot(
        self,
        conversation: _Conversation,
        command: Command,
        correlation_id: str,
        process_status: ProcessStatus,
    ) -> int:
        conversation.process_status = process_status
        return self._publish(
            conversation,
            lambda cursor: SnapshotUpdated(
                **self._envelope(conversation, command, correlation_id, cursor),
                event_type="snapshot.updated",
                snapshot=self._snapshot(conversation, cursor, process_status),
            ),
        )

    def _publish_status(
        self,
        conversation: _Conversation,
        command: Command,
        correlation_id: str,
        result: CommandResult,
    ) -> None:
        self._publish(
            conversation,
            lambda cursor: CommandStatusChanged(
                **self._envelope(conversation, command, correlation_id, cursor),
                event_type="command.status_changed",
                result=result,
            ),
        )

    def _publish(
        self,
        conversation: _Conversation,
        build: Callable[[int], StreamEvent],
        *,
        pause: float = 0.0,
    ) -> int:
        cursor = conversation.cursor + 1
        conversation.events.append(_LoggedEvent(build(cursor), pause))
        conversation.cursor = cursor
        if self.retained_events is not None:
            del conversation.events[: -self.retained_events]
        return cursor

    def _envelope(
        self, conversation: _Conversation, command: Command, correlation_id: str, cursor: int
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": self._next_id("stream"),
            "conversation_id": conversation.conversation_id,
            "command_event_id": command.event_id,
            "correlation_id": correlation_id,
            "occurred_at": self._clock(),
            "cursor": cursor,
        }

    def _snapshot(
        self, conversation: _Conversation, cursor: int, process_status: ProcessStatus
    ) -> RestaurantSnapshot:
        owner = conversation.owner
        memories = list(self._memories.get(owner.actor_id, [])) if owner.authenticated else []
        customer = CustomerSnapshot(
            presented_name=conversation.presented_name,
            party_size=1,
            preferences=list(conversation.preferences),
            restrictions=list(conversation.restrictions),
        )
        pending = []
        if customer.presented_name is None:
            pending.append(PendingField.CUSTOMER_NAME)
        if customer.party_size is None:
            pending.append(PendingField.PARTY_SIZE)
        return RestaurantSnapshot(
            schema_version=1,
            visit_id=conversation.visit_id,
            conversation_id=conversation.conversation_id,
            identity=owner,
            cursor=cursor,
            messages=list(conversation.messages),
            customer=customer,
            order_draft=OrderDraft(),
            pending_fields=pending,
            memory=MemoryView(memories=memories),
            process_status=process_status,
            allowed_actions=self._allowed_actions(conversation, memories),
        )

    def _allowed_actions(
        self, conversation: _Conversation, memories: list[VisibleMemory]
    ) -> list[Action]:
        actions = [Action.ARRIVE]
        if conversation.turns < self.max_turns:
            actions.append(Action.SEND_MESSAGE)
        if conversation.owner.authenticated:
            actions.append(Action.READ_MEMORY)
            if memories:
                actions.extend((Action.CORRECT_MEMORY, Action.DELETE_MEMORY))
            actions.append(Action.CLEAR_MEMORY)
        return actions

    def _owned(self, identity: ActorContext, conversation_id: str) -> _Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise self._client_error(ErrorCode.NOT_FOUND, "No encuentro esa conversación.")
        if conversation.owner.actor_id != identity.actor_id:
            raise self._client_error(ErrorCode.FORBIDDEN, "No tienes acceso a esta conversación.")
        return conversation

    @staticmethod
    def _pending(command: Command, correlation_id: str) -> PendingCommandResult:
        return PendingCommandResult(
            schema_version=1,
            event_id=command.event_id,
            correlation_id=correlation_id,
            status="pending",
        )

    @staticmethod
    def _failed(
        command: Command, correlation_id: str, code: ErrorCode, message: str
    ) -> FailedCommandResult:
        return FailedCommandResult(
            schema_version=1,
            event_id=command.event_id,
            correlation_id=correlation_id,
            status="failed",
            error=PublicError(
                code=code, message=message, correlation_id=correlation_id, recovery="none"
            ),
        )

    def _client_error(
        self,
        code: ErrorCode,
        message: str,
        *,
        recovery: Literal["none", "fetch_snapshot"] = "none",
    ) -> BffClientError:
        return BffClientError(
            PublicError(
                code=code,
                message=message,
                correlation_id=self._next_id("corr"),
                recovery=recovery,
            )
        )

    def _next_id(self, prefix: str) -> str:
        number = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = number
        return f"{prefix}_{number}"


class FakeBffClient:
    """BffClient bound to one identity by the adapter; commands never carry it."""

    def __init__(self, restaurant: FakeRestaurant, identity: ActorContext) -> None:
        self._restaurant = restaurant
        self._identity = identity

    @property
    def identity(self) -> ActorContext:
        return self._identity

    @property
    def active_visit_id(self) -> str | None:
        return self._restaurant.active_visit_id(self._identity)

    @property
    def simulated(self) -> bool:
        return True

    async def submit(self, command: Command) -> CommandResult:
        return self._restaurant.submit(self._identity, command)

    async def get_result(self, event_id: str) -> CommandResult:
        return self._restaurant.get_result(self._identity, event_id)

    async def get_snapshot(self, conversation_id: str) -> RestaurantSnapshot:
        return self._restaurant.get_snapshot(self._identity, conversation_id)

    def events(
        self, conversation_id: str, *, after_cursor: int
    ) -> AsyncIterator[StreamEvent]:
        return self._restaurant.stream(self._identity, conversation_id, after_cursor)
