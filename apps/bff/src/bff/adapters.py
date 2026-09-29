"""Waiter and seating adapters selected by configuration.

The Foundry part is deliberately thin: it only builds the existing waiter
agent. Everything else is shared with the scripted waiter. Seating is on only
when SEATING_MCP_URL is set; the gateway hides MCP and its transport.
"""

from __future__ import annotations

from restaurant_agent.memory.store import DurableMemoryRepository
from restaurant_agent.seating_gateway import McpSeatingGateway, SeatingGateway

from bff.config import BffSettings
from bff.scripted import ScriptedWaiterAgent
from bff.waiter import LocalWaiter


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
) -> LocalWaiter:
    if settings.bff_waiter == "scripted":
        agent = ScriptedWaiterAgent(
            delay_seconds=settings.bff_scripted_delay_seconds, seating=seating
        )
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
