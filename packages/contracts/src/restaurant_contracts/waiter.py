"""Typed wire contract for invoking the restaurant waiter remotely."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .application import ActorContext
from .customer import CustomerSnapshot, OrderDraft
from .memory import DurableMemoryRecord, MemoryCandidate


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WaiterTurnRequest(WireModel):
    operation: Literal["take_turn"] = "take_turn"
    conversation_id: str = Field(min_length=1, max_length=200)
    actor: ActorContext
    presented_name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int = Field(ge=0)
    persisted_order_preferences: list[str] = Field(default_factory=list)
    session_json: str | None = None
    correlation_id: str = Field(min_length=1, max_length=200)
    visit_id: str | None = None
    seating_context: dict[str, Any] | None = None
    pending_assignment_id: str | None = None
    history_notes: list[str] = Field(default_factory=list)
    memories: list[DurableMemoryRecord] = Field(default_factory=list)


class WaiterTurnSuccess(WireModel):
    status: Literal["completed"] = "completed"
    reply: str = Field(min_length=1, max_length=2_000)
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int = Field(ge=1)
    persisted_order_preferences: list[str] = Field(default_factory=list)
    session_json: str | None = None
    seating_proposal: dict[str, Any] | None = None
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)


class WaiterTurnFailure(WireModel):
    status: Literal["failed"] = "failed"
    code: Literal[
        "turn_limit",
        "seating_unavailable",
        "unavailable",
        "invalid_response",
        "internal_error",
    ]
    message: str = Field(min_length=1, max_length=500)


WaiterTurnResponse = WaiterTurnSuccess | WaiterTurnFailure
WAITER_TURN_RESPONSE_ADAPTER = TypeAdapter(WaiterTurnResponse)
