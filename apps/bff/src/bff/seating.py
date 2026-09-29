"""BFF access to the seating MCP: hold, confirm, cancel and room map.

The BFF and its scripted waiter use this gateway; the model never sees it.
Each call
opens its own Streamable HTTP session under a timeout, so an MCP restart never
leaves a broken shared connection behind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

Preference = Literal["table", "bar", "any"]

_ERROR_CODE = re.compile(r"\b(no_seating|expired|not_found|conflict|idempotency_conflict): ")


class SeatingGatewayError(RuntimeError):
    """The seating service refused the operation."""


class SeatingUnavailable(SeatingGatewayError):
    """The seating service could not be reached in time."""


class NoSeatingAvailable(SeatingGatewayError):
    """No place fits the group right now."""


class SeatingConflict(SeatingGatewayError):
    """The assignment is not in a state that allows the operation."""


class SeatingExpired(SeatingConflict):
    """The hold expired before the operation."""


class SeatingNotFound(SeatingConflict):
    """The assignment does not exist for this visit."""


class SeatingIdempotencyConflict(SeatingGatewayError):
    """An idempotency key was reused with another request."""


_ERRORS: dict[str, type[SeatingGatewayError]] = {
    "no_seating": NoSeatingAvailable,
    "expired": SeatingExpired,
    "not_found": SeatingNotFound,
    "conflict": SeatingConflict,
    "idempotency_conflict": SeatingIdempotencyConflict,
}


def seat_positions(seat_ids: Any) -> list[int]:
    """Return bar-stool positions from ``<prefix>-NN`` seat identifiers."""

    positions = []
    for seat_id in seat_ids or ():
        suffix = str(seat_id).rsplit("-", 1)[-1]
        if suffix.isdigit():
            positions.append(int(suffix))
    return positions


def stools_text(positions: list[int]) -> str:
    if not positions:
        return "la barra"
    if len(positions) == 1:
        return f"la barra, puesto {positions[0]}"
    return f"la barra, puestos {positions[0]} a {positions[-1]}"


@dataclass(frozen=True)
class SeatingAssignment:
    assignment_id: str
    visit_id: str
    resource_id: str
    resource_kind: Literal["table", "bar"]
    seat_ids: tuple[str, ...]
    party_size: int
    status: str
    expires_at: datetime | None
    version: int
    resource_label: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeatingAssignment:
        expires = data.get("expires_at")
        return cls(
            assignment_id=str(data["assignment_id"]),
            visit_id=str(data["visit_id"]),
            resource_id=str(data["resource_id"]),
            resource_kind=data["resource_kind"],
            seat_ids=tuple(str(seat) for seat in data.get("seat_ids") or ()),
            party_size=int(data["party_size"]),
            status=str(data["status"]),
            expires_at=datetime.fromisoformat(expires) if expires else None,
            version=int(data["version"]),
            resource_label=str(data.get("resource_label") or ""),
        )


@dataclass(frozen=True)
class RoomSeatState:
    seat_id: str
    position: int
    state: Literal["free", "held", "occupied"]
    mine: bool


@dataclass(frozen=True)
class RoomResource:
    resource_id: str
    kind: Literal["table", "bar"]
    label: str
    capacity: int
    display_order: int
    state: Literal["free", "held", "occupied"]
    party_size: int | None
    mine: bool
    seats: tuple[RoomSeatState, ...]

    def positions(self, seat_ids: tuple[str, ...]) -> list[int]:
        by_id = {seat.seat_id: seat.position for seat in self.seats}
        return [by_id[seat_id] for seat_id in seat_ids if seat_id in by_id]


@dataclass(frozen=True)
class SeatingRoom:
    layout_id: str
    resources: tuple[RoomResource, ...]
    visit: SeatingAssignment | None

    def resource(self, resource_id: str) -> RoomResource | None:
        return next((item for item in self.resources if item.resource_id == resource_id), None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeatingRoom:
        visit = data.get("visit")
        return cls(
            layout_id=str(data.get("layout_id", "")),
            resources=tuple(
                RoomResource(
                    resource_id=str(item["resource_id"]),
                    kind=item["kind"],
                    label=str(item["label"]),
                    capacity=int(item["capacity"]),
                    display_order=int(item["display_order"]),
                    state=item["state"],
                    party_size=item.get("party_size"),
                    mine=bool(item.get("mine")),
                    seats=tuple(
                        RoomSeatState(
                            seat_id=str(seat["seat_id"]),
                            position=int(seat["position"]),
                            state=seat["state"],
                            mine=bool(seat.get("mine")),
                        )
                        for seat in item.get("seats") or ()
                    ),
                )
                for item in data.get("resources") or ()
            ),
            visit=SeatingAssignment.from_dict(visit) if isinstance(visit, dict) else None,
        )


class SeatingGateway(Protocol):
    async def hold(
        self, *, visit_id: str, party_size: int, preference: Preference, idempotency_key: str
    ) -> SeatingAssignment: ...

    async def confirm(
        self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str
    ) -> SeatingAssignment: ...

    async def cancel(
        self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str
    ) -> SeatingAssignment: ...

    async def room(self, visit_id: str = "") -> SeatingRoom: ...


def error_from_text(text: str) -> SeatingGatewayError:
    """Map an MCP tool error to a typed error by its stable code."""

    match = _ERROR_CODE.search(text)
    if match is None:
        return SeatingGatewayError("The seating service rejected the request")
    return _ERRORS[match.group(1)](match.group(1))


class McpSeatingGateway:
    """Calls the seating MCP over Streamable HTTP, one short session per call."""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._url = url
        self._timeout = timeout_seconds

    async def hold(
        self, *, visit_id: str, party_size: int, preference: Preference, idempotency_key: str
    ) -> SeatingAssignment:
        data = await self._call(
            "hold_seating",
            {
                "visit_id": visit_id,
                "party_size": party_size,
                "preference": preference,
                "idempotency_key": idempotency_key,
            },
        )
        return SeatingAssignment.from_dict(data)

    async def confirm(
        self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str
    ) -> SeatingAssignment:
        return SeatingAssignment.from_dict(
            await self._call(
                "confirm_seating",
                {
                    "assignment_id": assignment_id,
                    "visit_id": visit_id,
                    "expected_version": expected_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )

    async def cancel(
        self, *, assignment_id: str, visit_id: str, expected_version: int, idempotency_key: str
    ) -> SeatingAssignment:
        return SeatingAssignment.from_dict(
            await self._call(
                "cancel_seating_hold",
                {
                    "assignment_id": assignment_id,
                    "visit_id": visit_id,
                    "expected_version": expected_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )

    async def room(self, visit_id: str = "") -> SeatingRoom:
        return SeatingRoom.from_dict(await self._call("get_seating_map", {"visit_id": visit_id}))

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import anyio
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        try:
            with anyio.fail_after(self._timeout):
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    async with streamable_http_client(self._url, http_client=http) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool(tool, arguments)
        except Exception as exc:
            raise SeatingUnavailable(
                f"Seating service unavailable ({type(exc).__name__})"
            ) from exc
        text = "".join(getattr(item, "text", "") for item in result.content)
        if result.isError:
            raise error_from_text(text)
        data = result.structuredContent
        if not isinstance(data, dict) or set(data) == {"result"}:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SeatingGatewayError("The seating service answered without JSON") from exc
        if not isinstance(data, dict):
            raise SeatingGatewayError("The seating service answered an unexpected shape")
        return data
