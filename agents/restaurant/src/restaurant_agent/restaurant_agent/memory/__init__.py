"""Consented durable memory for the restaurant agent."""

from restaurant_agent.config import Settings
from restaurant_agent.memory.context import DurableMemoryContextProvider
from restaurant_agent.memory.store import SQLiteMemoryStore


def create_memory_store(settings: Settings) -> SQLiteMemoryStore:
    """Build the configured local memory adapter."""

    if settings.memory_database_path is None:
        raise ValueError("MEMORY_DATABASE_PATH is required")
    return SQLiteMemoryStore(
        settings.memory_database_path,
        max_memories=settings.memory_max_items,
    )


__all__ = [
    "DurableMemoryContextProvider",
    "SQLiteMemoryStore",
    "create_memory_store",
]
