"""Customer and unverified draft models shared with the initial waiter."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
