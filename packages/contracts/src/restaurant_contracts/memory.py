"""Non-binding memories shared with the waiter and public projection."""

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
