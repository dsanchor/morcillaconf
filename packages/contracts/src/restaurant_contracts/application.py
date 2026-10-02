"""Version-one commands and projections, independent of their transport."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from restaurant_contracts.activity import MAX_ACTIVITY_STEPS, ActivityStep
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft, PendingField
from restaurant_contracts.kitchen import RENDERED_TEXT_LIMIT, KitchenReport
from restaurant_contracts.memory import MemoryKind
from restaurant_contracts.seating import SeatingView

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
MESSAGE_TEXT_LIMIT = 2_000
MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MESSAGE_TEXT_LIMIT),
]
# A kitchen plan is longer than a chat message; the other roles keep their limit.
ConversationText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=RENDERED_TEXT_LIMIT),
]
MemoryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Cursor = Annotated[int, Field(strict=True, ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorContext(ContractModel):
    """Server-derived context, never part of a client command."""

    actor_id: Identifier
    authenticated: bool = Field(strict=True)


class Action(StrEnum):
    ARRIVE = "customer.arrived"
    SEND_MESSAGE = "conversation.message_sent"
    READ_MEMORY = "memory.read_requested"
    CORRECT_MEMORY = "memory.correction_requested"
    DELETE_MEMORY = "memory.deletion_requested"
    CLEAR_MEMORY = "memory.clear_requested"
    DECIDE_TABLE = "table.confirmation_decided"
    END_VISIT = "visit.end_requested"


class EmptyPayload(ContractModel):
    pass


class ArrivePayload(ContractModel):
    resume_visit_id: Identifier | None = None


class SendMessagePayload(ContractModel):
    message: MessageText


class CorrectMemoryPayload(ContractModel):
    memory_id: Identifier
    value: MemoryText


class DeleteMemoryPayload(ContractModel):
    memory_id: Identifier


class TableDecisionPayload(ContractModel):
    """Explicit, versioned answer to one seating proposal (table or bar)."""

    proposal_id: Identifier
    version: Annotated[int, Field(strict=True, ge=1)]
    decision: Literal["confirmed", "rejected"]


class CommandEnvelope(ContractModel):
    schema_version: Literal[1]
    event_id: Identifier
    occurred_at: AwareDatetime


class ArriveCommand(CommandEnvelope):
    event_type: Literal["customer.arrived"]
    payload: ArrivePayload


class ConversationCommand(CommandEnvelope):
    conversation_id: Identifier


class SendMessageCommand(ConversationCommand):
    event_type: Literal["conversation.message_sent"]
    payload: SendMessagePayload


class ReadMemoryCommand(ConversationCommand):
    event_type: Literal["memory.read_requested"]
    payload: EmptyPayload


class CorrectMemoryCommand(ConversationCommand):
    event_type: Literal["memory.correction_requested"]
    payload: CorrectMemoryPayload


class DeleteMemoryCommand(ConversationCommand):
    event_type: Literal["memory.deletion_requested"]
    payload: DeleteMemoryPayload


class ClearMemoryCommand(ConversationCommand):
    event_type: Literal["memory.clear_requested"]
    payload: EmptyPayload


class DecideTableCommand(ConversationCommand):
    event_type: Literal["table.confirmation_decided"]
    payload: TableDecisionPayload


class EndVisitCommand(ConversationCommand):
    event_type: Literal["visit.end_requested"]
    payload: EmptyPayload


Command = Annotated[
    ArriveCommand
    | SendMessageCommand
    | ReadMemoryCommand
    | CorrectMemoryCommand
    | DeleteMemoryCommand
    | ClearMemoryCommand
    | DecideTableCommand
    | EndVisitCommand,
    Field(discriminator="event_type"),
]
COMMAND_ADAPTER = TypeAdapter(Command)


class VisibleMemory(ContractModel):
    memory_id: Identifier
    kind: MemoryKind
    value: MemoryText
    source: Identifier
    recorded_at: AwareDatetime
    requires_reconfirmation: Literal[True] = True


class MemoryView(ContractModel):
    memories: list[VisibleMemory]

    @model_validator(mode="after")
    def memory_ids_are_unique(self) -> "MemoryView":
        ids = [memory.memory_id for memory in self.memories]
        if len(ids) != len(set(ids)):
            raise ValueError("Visible memory IDs must be unique")
        return self


class ChatMessage(ContractModel):
    """A line of the conversation: the customer, the waiter or the kitchen's plan."""

    message_id: Identifier
    role: Literal["user", "assistant", "kitchen"]
    text: ConversationText
    occurred_at: AwareDatetime
    command_event_id: Identifier
    # Left out of the JSON when absent, so customer and waiter messages keep
    # the exact shape that earlier versions read.
    kitchen: KitchenReport | None = Field(default=None, exclude_if=lambda value: value is None)
    # What the system did for this message (a customer's message or a serving).
    activity: list[ActivityStep] = Field(
        default_factory=list, max_length=MAX_ACTIVITY_STEPS, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def kitchen_messages_carry_their_report(self) -> "ChatMessage":
        if (self.role == "kitchen") != (self.kitchen is not None):
            raise ValueError("Kitchen messages, and only they, carry a kitchen report")
        if self.role != "kitchen" and len(self.text) > MESSAGE_TEXT_LIMIT:
            raise ValueError(
                f"Customer and waiter messages have at most {MESSAGE_TEXT_LIMIT} characters"
            )
        return self


class RestaurantSnapshot(ContractModel):
    """Confirmed projection: conversation, memory and the customer's own seating."""

    schema_version: Literal[1]
    visit_id: Identifier
    conversation_id: Identifier
    identity: ActorContext
    cursor: Cursor
    messages: list[ChatMessage]
    customer: CustomerSnapshot
    order_draft: OrderDraft
    pending_fields: list[PendingField]
    memory: MemoryView
    process_status: Literal["idle", "processing", "awaiting_customer"]
    allowed_actions: list[Action]
    seating: SeatingView = Field(default_factory=SeatingView)
    # Kitchen orders the waiter has already taken from the pass to the table.
    served_orders: list[Identifier] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def projection_is_consistent(self) -> "RestaurantSnapshot":
        expected = set()
        if self.customer.presented_name is None:
            expected.add(PendingField.CUSTOMER_NAME)
        if self.customer.party_size is None:
            expected.add(PendingField.PARTY_SIZE)
        if (
            set(self.pending_fields) != expected
            or len(self.pending_fields) != len(expected)
        ):
            raise ValueError("pending_fields must match missing customer data")
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("allowed_actions must not contain duplicates")
        if (
            Action.DECIDE_TABLE in self.allowed_actions
            and self.seating.status != "proposed"
        ):
            raise ValueError("A seating decision needs a pending proposal")
        ids = [message.message_id for message in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("Message IDs must be unique")
        if not self.identity.authenticated:
            if self.memory.memories:
                raise ValueError("Guests cannot have durable memory")
            if any(
                action
                not in (
                    Action.ARRIVE,
                    Action.SEND_MESSAGE,
                    Action.DECIDE_TABLE,
                    Action.END_VISIT,
                )
                for action in self.allowed_actions
            ):
                raise ValueError("Guests cannot manage durable memory")
        return self


class ErrorCode(StrEnum):
    INVALID_COMMAND = "invalid_command"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TURN_LIMIT_EXCEEDED = "turn_limit_exceeded"
    CURSOR_EXPIRED = "cursor_expired"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


class PublicError(ContractModel):
    code: ErrorCode
    message: MessageText
    correlation_id: Identifier
    recovery: Literal["none", "retry_same_command", "fetch_snapshot"]

    @model_validator(mode="after")
    def recovery_matches_error(self) -> "PublicError":
        if (self.code == ErrorCode.CURSOR_EXPIRED) != (
            self.recovery == "fetch_snapshot"
        ):
            raise ValueError("Expired cursors require a new snapshot")
        if self.recovery == "retry_same_command" and self.code != ErrorCode.UNAVAILABLE:
            raise ValueError("Only unavailable operations may advise a retry")
        return self


class CommandResultEnvelope(ContractModel):
    schema_version: Literal[1]
    event_id: Identifier
    correlation_id: Identifier


class PendingCommandResult(CommandResultEnvelope):
    status: Literal["pending"]


class CompletedCommandResult(CommandResultEnvelope):
    status: Literal["completed"]
    visit_id: Identifier
    conversation_id: Identifier
    cursor: Cursor


class FailedCommandResult(CommandResultEnvelope):
    status: Literal["failed"]
    error: PublicError

    @model_validator(mode="after")
    def error_matches_result(self) -> "FailedCommandResult":
        if self.error.correlation_id != self.correlation_id:
            raise ValueError("Error and command result correlation must match")
        if self.error.recovery != "none":
            raise ValueError("A terminal command failure cannot request transport recovery")
        return self


CommandResult = Annotated[
    PendingCommandResult | CompletedCommandResult | FailedCommandResult,
    Field(discriminator="status"),
]
COMMAND_RESULT_ADAPTER = TypeAdapter(CommandResult)


class StreamEventEnvelope(ContractModel):
    schema_version: Literal[1]
    event_id: Identifier
    conversation_id: Identifier
    command_event_id: Identifier
    correlation_id: Identifier
    occurred_at: AwareDatetime
    cursor: Annotated[int, Field(strict=True, gt=0)]


class SnapshotUpdated(StreamEventEnvelope):
    event_type: Literal["snapshot.updated"]
    snapshot: RestaurantSnapshot

    @model_validator(mode="after")
    def snapshot_matches_event(self) -> "SnapshotUpdated":
        if (
            self.snapshot.conversation_id != self.conversation_id
            or self.snapshot.cursor != self.cursor
        ):
            raise ValueError("Snapshot conversation and cursor must match the event")
        return self


class CommandStatusChanged(StreamEventEnvelope):
    event_type: Literal["command.status_changed"]
    result: CommandResult

    @model_validator(mode="after")
    def result_matches_event(self) -> "CommandStatusChanged":
        if (
            self.result.event_id != self.command_event_id
            or self.result.correlation_id != self.correlation_id
        ):
            raise ValueError("Command result must match event correlation")
        if isinstance(self.result, CompletedCommandResult) and (
            self.result.conversation_id != self.conversation_id
            or self.result.cursor > self.cursor
        ):
            raise ValueError("Completed result must refer to this conversation's past")
        return self


class ResponseTextDelta(StreamEventEnvelope):
    event_type: Literal["conversation.text_delta"]
    message_id: Identifier
    delta: Annotated[str, Field(min_length=1, max_length=2_000)]
    provisional: Literal[True]


StreamEvent = Annotated[
    SnapshotUpdated | CommandStatusChanged | ResponseTextDelta,
    Field(discriminator="event_type"),
]
STREAM_EVENT_ADAPTER = TypeAdapter(StreamEvent)
