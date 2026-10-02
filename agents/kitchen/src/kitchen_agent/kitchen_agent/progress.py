"""Report the kitchen's progress for the A2A task while it works.

The A2A executor installs an async sink for the order; the chef, the
specialists and the preparation report through it without knowing A2A.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from restaurant_contracts.activity import ActivityComponent, ActivityStep

ProgressSink = Callable[[ActivityStep], Awaitable[None]]
_SINK: ContextVar[ProgressSink | None] = ContextVar("kitchen_progress_sink", default=None)


def install(sink: ProgressSink | None) -> None:
    _SINK.set(sink)


class Step:
    """A kitchen step: reported running when started and finished with a detail."""

    def __init__(self, component: ActivityComponent, label: str, detail: str | None = None) -> None:
        self._step = ActivityStep(
            step_id=f"k-{component}-{uuid.uuid4().hex[:8]}",
            component=component,
            label=label[:160],
            detail=detail[:200] if detail else None,
            status="running",
        )
        self._started = time.monotonic()

    async def start(self) -> "Step":
        self._started = time.monotonic()
        await _emit(self._step)
        return self

    async def finish(self, detail: str | None = None, *, failed: bool = False) -> None:
        shown = detail or self._step.detail
        await _emit(
            self._step.model_copy(
                update={
                    "status": "failed" if failed else "done",
                    "detail": shown[:200] if shown else None,
                    "duration_ms": int((time.monotonic() - self._started) * 1000),
                }
            )
        )


async def done(component: ActivityComponent, label: str, detail: str | None = None) -> None:
    await Step(component, label, detail).finish(detail)


async def _emit(step: ActivityStep) -> None:
    sink = _SINK.get()
    if sink is not None:
        await sink(step)
