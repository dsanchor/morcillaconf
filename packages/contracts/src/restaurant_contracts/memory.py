"""Non-binding memories shared with the waiter and public projection."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MemoryKind(StrEnum):
    """Category used to keep preferences and restrictions distinct."""

    PREFERENCE = "preference"
    RESTRICTION = "restriction"


class MemoryCandidate(BaseModel):
    """Non-binding memory proposed from the current customer message."""

    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    value: str = Field(min_length=1, max_length=200)


class DurableMemoryRecord(BaseModel):
    """A non-binding memory with provenance."""

    model_config = ConfigDict(extra="forbid")

    preference_id: str
    actor_id: str
    kind: MemoryKind
    value: str = Field(min_length=1, max_length=200)
    occurrence_count: int = Field(default=1, ge=1)
    source_conversation_id: str
    created_at: datetime
    updated_at: datetime
    requires_reconfirmation: bool = True


class CompletedOrderItem(BaseModel):
    """One item from an order that was actually completed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=20)


class CompletedOrderHistory(BaseModel):
    """Historical order contract; drafts never satisfy this contract."""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    actor_id: str
    items: list[CompletedOrderItem] = Field(min_length=1, max_length=50)
    completed_at: datetime


class MemorySnapshot(BaseModel):
    """Durable memory visible to the authenticated customer."""

    model_config = ConfigDict(extra="forbid")

    memories: list[DurableMemoryRecord]
    order_history: list[CompletedOrderHistory] = Field(default_factory=list)
