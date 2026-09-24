"""Structured contracts exchanged by the phase-one waiter."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restaurant_agent.memory.contracts import MemoryCandidate, MemoryIntent


class PendingField(StrEnum):
    """Customer information that the waiter still needs."""

    CUSTOMER_NAME = "customer_name"
    PARTY_SIZE = "party_size"


class DraftItemStatus(StrEnum):
    """Verification state for items before menu and inventory tools exist."""

    UNVERIFIED = "unverified"


class CustomerSnapshot(BaseModel):
    """Customer data explicitly supplied in the current conversation."""

    model_config = ConfigDict(extra="forbid")

    presented_name: str | None = Field(default=None, min_length=1, max_length=100)
    party_size: int | None = Field(default=1, ge=1, le=20)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    restrictions: list[str] = Field(default_factory=list, max_length=20)


class OrderItemDraft(BaseModel):
    """Unverified item mentioned by the customer."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=20)
    notes: list[str] = Field(default_factory=list, max_length=20)
    status: DraftItemStatus = DraftItemStatus.UNVERIFIED


class OrderDraft(BaseModel):
    """Current order proposal, not a confirmed order."""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemDraft] = Field(default_factory=list, max_length=50)


class SessionState(BaseModel):
    """Ephemeral state for one active conversation."""

    model_config = ConfigDict(extra="forbid")

    customer: CustomerSnapshot = Field(default_factory=CustomerSnapshot)
    order_draft: OrderDraft = Field(default_factory=OrderDraft)
    turn_count: int = Field(default=0, ge=0)


class WaiterModelResult(BaseModel):
    """Full structured snapshot returned by the model for one turn."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=2_000)
    customer: CustomerSnapshot
    order_draft: OrderDraft = Field(default_factory=OrderDraft)
    pending_fields: list[PendingField] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(
        default_factory=list,
        max_length=20,
    )
    memory_intent: MemoryIntent = Field(
        description=(
            "Use reuse_latest_order when the customer semantically asks for "
            "their usual or habitual order, even without naming its products."
        ),
    )
    remembered_memories: list[MemoryCandidate] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def pending_fields_match_customer_snapshot(self) -> "WaiterModelResult":
        expected = set()
        if self.customer.presented_name is None:
            expected.add(PendingField.CUSTOMER_NAME)
        if self.customer.party_size is None:
            expected.add(PendingField.PARTY_SIZE)
        if set(self.pending_fields) != expected:
            raise ValueError(
                "pending_fields must contain exactly the missing customer fields"
            )
        return self


class WaiterResponse(BaseModel):
    """Application response returned to a CLI or future BFF."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    correlation_id: str
    turn_number: int = Field(ge=1)
    reply: str
    customer: CustomerSnapshot
    order_draft: OrderDraft
    pending_fields: list[PendingField]
    remembered_memories: list[MemoryCandidate] = Field(default_factory=list)
