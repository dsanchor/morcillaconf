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
    _memory_store: DurableMemoryRepository,
) -> WaiterPort:
    assert settings.waiter_agent_url is not None
    return RemoteWaiter(
        str(settings.waiter_agent_url),
        timeout_seconds=settings.waiter_agent_timeout_seconds,
    )
