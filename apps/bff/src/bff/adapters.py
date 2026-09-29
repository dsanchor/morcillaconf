"""Waiter and seating adapters selected by configuration."""

from __future__ import annotations

from restaurant_contracts.memory_store import DurableMemoryRepository

from bff.config import BffSettings
from bff.seating import McpSeatingGateway, SeatingGateway
from bff.waiter import RemoteWaiter, WaiterPort


def create_seating(settings: BffSettings) -> SeatingGateway | None:
    if settings.seating_mcp_url is None:
        return None
    return McpSeatingGateway(
        str(settings.seating_mcp_url), timeout_seconds=settings.seating_mcp_timeout_seconds
    )


def create_waiter(
    settings: BffSettings,
    memory_store: DurableMemoryRepository,
    seating: SeatingGateway | None = None,
) -> WaiterPort:
    if settings.bff_waiter == "scripted":
        from bff.local_waiter import LocalWaiter
        from bff.scripted import ScriptedWaiterAgent

        agent = ScriptedWaiterAgent(
            delay_seconds=settings.bff_scripted_delay_seconds, seating=seating
        )
        return LocalWaiter(
            agent,
            mode="scripted",
            max_turns=settings.waiter_max_turns,
            memory_store=memory_store,
        )
    assert settings.waiter_agent_url is not None
    return RemoteWaiter(
        str(settings.waiter_agent_url),
        timeout_seconds=settings.waiter_agent_timeout_seconds,
    )
