"""Contracts for durable memory, separate from active session state."""

from collections.abc import Iterable
from enum import StrEnum

from restaurant_contracts.memory import (
    CompletedOrderHistory,
    CompletedOrderItem,
    DurableMemoryRecord,
    MemoryCandidate,
    MemoryKind,
    MemorySnapshot,
)

__all__ = [
    "CompletedOrderHistory",
    "CompletedOrderItem",
    "DurableMemoryRecord",
    "MemoryCandidate",
    "MemoryIntent",
    "MemoryKind",
    "MemorySnapshot",
    "ORDER_PREFERENCE_PREFIX",
    "summarize_order_preference",
]


class MemoryIntent(StrEnum):
    """How the current message intends to use durable memory."""

    NONE = "none"
    REUSE_LATEST_ORDER = "reuse_latest_order"


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
