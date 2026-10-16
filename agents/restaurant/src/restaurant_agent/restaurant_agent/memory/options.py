"""Source-backed habitual-order options and deterministic draft validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from restaurant_contracts.customer import OrderDraft, OrderItemDraft
from restaurant_agent.memory.contracts import (
    DurableMemoryRecord,
    MemoryIntent,
    MemoryKind,
    ORDER_PREFERENCE_PREFIX,
)

if TYPE_CHECKING:
    from restaurant_agent.contracts import WaiterModelResult


class HabitualOrderSelectionError(ValueError):
    """The model selected an unusable or unauthorized memory option."""


class HabitualOrderOption(BaseModel):
    """An option identified by its durable memory record, not its rank."""

    model_config = ConfigDict(extra="forbid")

    option_id: str
    summary: str = Field(min_length=1, max_length=200)

    @property
    def is_complete(self) -> bool:
        return bool(self.summary.strip(" ,")) and not self.summary.rstrip().endswith("...")


def rank_habitual_order_options(
    memories: Sequence[DurableMemoryRecord],
) -> list[HabitualOrderOption]:
    """Rank by frequency and recency without exposing those metadata."""

    ranked = sorted(
        (
            memory
            for memory in memories
            if memory.kind is MemoryKind.PREFERENCE
            and memory.value.startswith(ORDER_PREFERENCE_PREFIX)
        ),
        key=lambda memory: (
            memory.occurrence_count,
            memory.updated_at,
            memory.preference_id,
        ),
        reverse=True,
    )
    return [
        HabitualOrderOption(
            option_id=memory.preference_id,
            summary=memory.value.removeprefix(ORDER_PREFERENCE_PREFIX),
        )
        for memory in ranked
    ]


def loaded_habitual_order_options(
    state: Mapping[str, object],
) -> list[HabitualOrderOption]:
    """Read the JSON-safe options loaded for this authenticated turn."""

    return TypeAdapter(list[HabitualOrderOption]).validate_python(
        state.get("habitual_order_options", [])
    )


def apply_habitual_order(
    result: WaiterModelResult,
    options: Sequence[HabitualOrderOption],
) -> None:
    """Materialize only a resolved exact repeat, preserving the waiter's reply."""

    selected_id = result.habitual_order_option_id
    if result.memory_intent is not MemoryIntent.REUSE_LATEST_ORDER:
        if selected_id is not None:
            raise HabitualOrderSelectionError(
                "A habitual-order option requires a repeat intent"
            )
        return

    selected = next(
        (option for option in options if option.option_id == selected_id), None
    )
    if selected_id is not None and selected is None:
        raise HabitualOrderSelectionError(
            "The habitual-order option is not in the current memory context"
        )
    if selected is None and len(options) == 1 and options[0].is_complete:
        selected = options[0]
    if selected is None:
        # No memory or an unresolved choice must not materialize invented items.
        result.order_draft = OrderDraft()
        return

    names = [name.strip() for name in selected.summary.split(",") if name.strip()]
    if not selected.is_complete:
        raise HabitualOrderSelectionError(
            "The habitual-order memory is incomplete; clarify the products"
        )
    result.order_draft = OrderDraft(
        items=[OrderItemDraft(name=name) for name in names]
    )
