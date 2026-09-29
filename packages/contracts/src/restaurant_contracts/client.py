"""Client boundary implemented later by fake and HTTP/SSE adapters."""

from collections.abc import AsyncIterator
from typing import Protocol

from restaurant_contracts.application import (
    Command,
    CommandResult,
    PublicError,
    RestaurantSnapshot,
    StreamEvent,
)
from restaurant_contracts.seating import RoomView


class BffClientError(RuntimeError):
    def __init__(self, error: PublicError) -> None:
        self.error = error
        super().__init__(error.message)


class BffClient(Protocol):
    """Authentication is bound by the adapter, never supplied by a command."""

    async def submit(self, command: Command) -> CommandResult: ...

    async def get_result(self, event_id: str) -> CommandResult: ...

    async def get_snapshot(self, conversation_id: str) -> RestaurantSnapshot: ...

    def events(
        self, conversation_id: str, *, after_cursor: int
    ) -> AsyncIterator[StreamEvent]: ...

    async def get_room(self, conversation_id: str) -> RoomView:
        """Anonymous room of the conversation's restaurant, for the plan."""
        ...
