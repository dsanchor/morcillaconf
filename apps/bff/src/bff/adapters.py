"""Waiter adapters selected by BFF_WAITER.

The Foundry part is deliberately thin: it only builds the existing waiter
agent. Everything else is shared with the scripted waiter.
"""

from __future__ import annotations

from restaurant_agent.memory.store import DurableMemoryRepository

from bff.config import BffSettings
from bff.scripted import ScriptedWaiterAgent
from bff.waiter import LocalWaiter


def create_waiter(
    settings: BffSettings, memory_store: DurableMemoryRepository
) -> LocalWaiter:
    if settings.bff_waiter == "scripted":
        agent = ScriptedWaiterAgent(delay_seconds=settings.bff_scripted_delay_seconds)
    else:
        # Imported lazily: agent_framework.foundry is only needed for the real waiter.
        from restaurant_agent.agent import create_waiter_agent

        agent = create_waiter_agent(settings.agent_settings(), memory_store=memory_store)
    return LocalWaiter(
        agent,
        mode=settings.bff_waiter,
        max_turns=settings.waiter_max_turns,
        memory_store=memory_store,
    )
