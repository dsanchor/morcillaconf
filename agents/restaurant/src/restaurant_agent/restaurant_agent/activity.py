"""Collect the steps of one waiter turn for the «bajo el capó» panel.

Tools, middleware and the A2A transports (the cashier relays its own steps)
report steps without knowing who listens: the remote service installs a sink for the turn in a context variable.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from restaurant_contracts.activity import ActivityComponent, ActivityStep

ActivitySink = Callable[[ActivityStep], None]
_SINK: ContextVar[ActivitySink | None] = ContextVar("waiter_activity_sink", default=None)


def report(step: ActivityStep) -> None:
    sink = _SINK.get()
    if sink is not None:
        sink(step)


@contextmanager
def recording(sink: ActivitySink) -> Iterator[None]:
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


class Tracked:
    """A step reported as running on entry and as done or failed on exit.

    It fails when an exception leaves it or when ``failed`` is set inside.
    """

    def __init__(self, component: ActivityComponent, label: str, detail: str | None = None) -> None:
        self.step = ActivityStep(
            step_id=f"{component}-{uuid.uuid4().hex[:10]}",
            component=component,
            label=label[:160],
            detail=detail[:200] if detail else None,
            status="running",
        )
        self.detail = detail
        self.failed = False
        self._started = 0.0

    def __enter__(self) -> "Tracked":
        self._started = time.monotonic()
        report(self.step)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        report(
            self.step.model_copy(
                update={
                    "status": "failed" if exc_type or self.failed else "done",
                    "detail": self.detail[:200] if self.detail else None,
                    "duration_ms": int((time.monotonic() - self._started) * 1000),
                }
            )
        )


def tracked(component: ActivityComponent, label: str, detail: str | None = None) -> Tracked:
    return Tracked(component, label, detail)


def instant(component: ActivityComponent, label: str, detail: str | None = None) -> None:
    report(
        ActivityStep(
            step_id=f"{component}-{uuid.uuid4().hex[:10]}",
            component=component,
            label=label[:160],
            detail=detail[:200] if detail else None,
        )
    )


TOOL_STEPS: dict[str, tuple[ActivityComponent, str]] = {
    "seating_get_seating_availability": ("mcp", "MCP mesas: consulta disponibilidad"),
    "seating_hold_seating": ("mcp", "MCP mesas: bloquea un sitio"),
    "seating_confirm_seating": ("mcp", "MCP mesas: confirma el sitio"),
    "seating_confirm_solo_seating": ("mcp", "MCP mesas: confirma el sitio"),
    "knowledge_base_retrieve": ("foundry_iq", "Foundry IQ: consulta carta y recetario"),
    "pedir_a_cocina": ("cocina", "A2A: envía la comanda a cocina"),
    # The waiter serves the drinks himself: no other agent takes part.
    "servir_bebidas": ("camarero", "Barra: sirve las bebidas"),
    "pedir_la_cuenta": ("caja", "Caja A2A: envía la cuenta"),
}


class ActivityToolMiddleware(FunctionMiddleware):
    """Report every tool call of the waiter as one timed step."""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        name = context.function.name
        component, label = TOOL_STEPS.get(name, ("camarero", f"Herramienta {name}"))
        with tracked(component, label):
            await call_next()
