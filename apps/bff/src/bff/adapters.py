"""Waiter adapters selected by configuration.

The BFF never talks to the seating MCP: the waiter does, and reports its
seating back with every call.
"""

from __future__ import annotations

from restaurant_contracts.memory_store import DurableMemoryRepository

from bff.config import BffSettings
from bff.waiter import RemoteWaiter, WaiterPort


def create_waiter(
    settings: BffSettings,
    memory_store: DurableMemoryRepository,
) -> WaiterPort:
    if settings.bff_waiter == "scripted":
        from bff.local_waiter import LocalWaiter
        from bff.scripted import ScriptedWaiterAgent
        from bff.scripted_seating import ScriptedSeating

        seating = ScriptedSeating() if settings.bff_scripted_seating else None
        agent = ScriptedWaiterAgent(
            delay_seconds=settings.bff_scripted_delay_seconds, seating=seating
        )
        return LocalWaiter(
            agent,
            mode="scripted",
            max_turns=settings.waiter_max_turns,
            memory_store=memory_store,
            seating=seating,
        )
    assert settings.waiter_agent_url is not None
    return RemoteWaiter(
        str(settings.waiter_agent_url),
        timeout_seconds=settings.waiter_agent_timeout_seconds,
    )
