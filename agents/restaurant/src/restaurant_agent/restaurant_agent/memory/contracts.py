"""Contracts for durable memory, separate from active session state."""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ConsentStatus(StrEnum):
    """Current permission to persist durable memories."""

    GRANTED = "granted"
    REVOKED = "revoked"


class MemoryKind(StrEnum):
    """Category used to keep preferences and restrictions distinct."""

    PREFERENCE = "preference"
    RESTRICTION = "restriction"


class MemoryIntent(StrEnum):
    """How the current message intends to use durable memory."""

    NONE = "none"
    REUSE_LATEST_ORDER = "reuse_latest_order"


class MemoryCandidate(BaseModel):
    """Non-binding memory proposed from the current customer message."""

    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    value: str = Field(min_length=1, max_length=200)


ORDER_PREFERENCE_PREFIX = "Preferencia de pedido: "


def summarize_order_preference(
    item_names: Iterable[str],
) -> MemoryCandidate | None:
    """Summarize draft items as one bounded, non-binding preference."""

    unique_names: list[str] = []
    seen: set[str] = set()
    for item_name in item_names:
        normalized_name = " ".join(item_name.split())
        comparison_key = normalized_name.casefold()
        if normalized_name and comparison_key not in seen:
            seen.add(comparison_key)
            unique_names.append(normalized_name)

    if not unique_names:
        return None

    available_length = 200 - len(ORDER_PREFERENCE_PREFIX)
    summary = ", ".join(unique_names)
    if len(summary) > available_length:
        summary = f"{summary[: available_length - 3].rstrip(' ,')}..."
    return MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        value=f"{ORDER_PREFERENCE_PREFIX}{summary}",
    )


class MemoryConsent(BaseModel):
    """Auditable consent state for one authenticated identity."""

    model_config = ConfigDict(extra="forbid")

    actor_id: str
    status: ConsentStatus
    source: str
    recorded_at: datetime
    includes_sensitive_restrictions: bool = True


class DurableMemoryRecord(BaseModel):
    """A consented but non-binding memory with provenance."""

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

    consent: MemoryConsent | None
    memories: list[DurableMemoryRecord]
    order_history: list[CompletedOrderHistory] = Field(default_factory=list)
