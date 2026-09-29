"""In-memory seating gateway with the seating service's rules.

Exclusive tables with best fit, bar runs with the smallest gap, one active
place per visit (a new hold replaces a pending one), expiry, idempotency and
an anonymous room map. The rules themselves are tested in services/mcp.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from restaurant_agent.seating_gateway import (
    NoSeatingAvailable,
    RoomResource,
    RoomSeatState,
    SeatingAssignment,
    SeatingConflict,
    SeatingExpired,
    SeatingIdempotencyConflict,
    SeatingNotFound,
    SeatingRoom,
    SeatingUnavailable,
)

DEMO = (
    ("table-01", "table", "Mesa 1", 2, 10),
    ("table-02", "table", "Mesa 2", 2, 20),
    ("table-03", "table", "Mesa 3", 4, 30),
    ("table-04", "table", "Mesa 4", 4, 40),
    ("table-05", "table", "Mesa 5", 6, 50),
    ("bar", "bar", "Barra", 8, 100),
)
ACTIVE = ("held", "occupied")


class FakeSeatingGateway:
    def __init__(self, clock: Callable[[], datetime], *, hold_minutes: int = 5) -> None:
        self._clock = clock
        self._hold = timedelta(minutes=hold_minutes)
        self.assignments: dict[str, SeatingAssignment] = {}
        self._keys: dict[tuple[str, str], tuple[str, str]] = {}
        self.available = True
        self.calls: list[str] = []

    # Test helpers

    def reset(self) -> None:
        self.assignments.clear()
        self._keys.clear()

    def expire_all(self) -> None:
        for key, item in self.assignments.items():
            if item.status == "held":
                self.assignments[key] = replace(item, status="expired", version=item.version + 1)

    def active(self, visit_id: str) -> SeatingAssignment | None:
        return next(
            (a for a in self.assignments.values() if a.visit_id == visit_id and a.status in ACTIVE),
            None,
        )

    # Gateway

    async def hold(self, *, visit_id, party_size, preference, idempotency_key):
        self._enter("hold")
        cached = self._cached("hold", idempotency_key, f"{visit_id}|{party_size}|{preference}")
        if cached:
            return cached
        current = self.active(visit_id)
        if current is not None and current.status == "occupied":
            raise SeatingConflict("conflict")
        choice = self._choose(party_size, preference, ignore=current)
        if choice is None:
            raise NoSeatingAvailable("no_seating")
        if current is not None:
            self.assignments[current.assignment_id] = replace(current, status="replaced", version=current.version + 1)
        resource_id, kind, label, seats = choice
        assignment = SeatingAssignment(
            assignment_id=f"seat_{len(self.assignments) + 1}",
            visit_id=visit_id,
            resource_id=resource_id,
            resource_kind=kind,
            seat_ids=tuple(f"bar-seat-{position:02d}" for position in seats),
            party_size=party_size,
            status="held",
            expires_at=self._clock() + self._hold,
            version=1,
            resource_label=label,
        )
        self.assignments[assignment.assignment_id] = assignment
        self._keys[("hold", idempotency_key)] = (self._digest(f"{visit_id}|{party_size}|{preference}"), assignment.assignment_id)
        return assignment

    async def confirm(self, *, assignment_id, visit_id, expected_version, idempotency_key):
        return self._change("confirm", "occupied", assignment_id, visit_id, expected_version, idempotency_key)

    async def cancel(self, *, assignment_id, visit_id, expected_version, idempotency_key):
        return self._change("cancel", "cancelled", assignment_id, visit_id, expected_version, idempotency_key)

    async def room(self, visit_id: str = "") -> SeatingRoom:
        self._enter("room")
        resources = []
        for resource_id, kind, label, capacity, order in DEMO:
            groups = [a for a in self.assignments.values() if a.resource_id == resource_id and a.status in ACTIVE]
            if kind == "table":
                state = "occupied" if any(a.status == "occupied" for a in groups) else "held" if groups else "free"
                resources.append(RoomResource(
                    resource_id, kind, label, capacity, order, state,
                    sum(a.party_size for a in groups) or None,
                    bool(visit_id) and any(a.visit_id == visit_id for a in groups), (),
                ))
            else:
                owners = {seat: a for a in groups for seat in a.seat_ids}
                seats = tuple(
                    RoomSeatState(
                        f"bar-seat-{position:02d}", position,
                        owners[f"bar-seat-{position:02d}"].status if f"bar-seat-{position:02d}" in owners else "free",
                        bool(visit_id) and f"bar-seat-{position:02d}" in owners and owners[f"bar-seat-{position:02d}"].visit_id == visit_id,
                    )
                    for position in range(1, capacity + 1)
                )
                free = sum(seat.state == "free" for seat in seats)
                state = "free" if free else "occupied"
                resources.append(RoomResource(resource_id, kind, label, capacity, order, state, None, any(s.mine for s in seats), seats))
        mine = [a for a in self.assignments.values() if a.visit_id == visit_id]
        return SeatingRoom("demo", tuple(resources), mine[-1] if visit_id and mine else None)

    # Internals

    def _enter(self, operation: str) -> None:
        self.calls.append(operation)
        if not self.available:
            raise SeatingUnavailable("down")
        now = self._clock()
        for key, item in list(self.assignments.items()):
            if item.status == "held" and item.expires_at is not None and item.expires_at <= now:
                self.assignments[key] = replace(item, status="expired", version=item.version + 1)

    @staticmethod
    def _digest(payload: str) -> str:
        return hashlib.sha256(payload.encode()).hexdigest()

    def _cached(self, operation: str, key: str, payload: str) -> SeatingAssignment | None:
        cached = self._keys.get((operation, key))
        if cached is None:
            return None
        if cached[0] != self._digest(payload):
            raise SeatingIdempotencyConflict("idempotency_conflict")
        return self.assignments[cached[1]]

    def _change(self, operation, status, assignment_id, visit_id, version, key):
        self._enter(operation)
        payload = f"{assignment_id}|{visit_id}|{version}"
        cached = self._cached(operation, key, payload)
        if cached:
            return cached
        item = self.assignments.get(assignment_id)
        if item is None or item.visit_id != visit_id:
            raise SeatingNotFound("not_found")
        if item.status == "expired":
            raise SeatingExpired("expired")
        if item.status != "held" or item.version != version:
            raise SeatingConflict("conflict")
        changed = replace(item, status=status, version=item.version + 1, expires_at=None)
        self.assignments[assignment_id] = changed
        self._keys[(operation, key)] = (self._digest(payload), assignment_id)
        return changed

    def _choose(self, size, preference, *, ignore):
        busy = {a.resource_id for a in self.assignments.values() if a.status in ACTIVE and a is not ignore}
        if preference in ("table", "any"):
            tables = sorted(
                (capacity, order, resource_id, label)
                for resource_id, kind, label, capacity, order in DEMO
                if kind == "table" and capacity >= size and resource_id not in busy
            )
            if tables:
                return tables[0][2], "table", tables[0][3], []
        if preference in ("bar", "any"):
            taken = {
                int(seat.rsplit("-", 1)[1])
                for a in self.assignments.values()
                if a.status in ACTIVE and a is not ignore
                for seat in a.seat_ids
            }
            runs, current = [], []
            for position in range(1, 9):
                if position in taken:
                    if current:
                        runs.append(current)
                    current = []
                else:
                    current.append(position)
            if current:
                runs.append(current)
            fits = sorted((len(run) - size, run[0], run) for run in runs if len(run) >= size)
            if fits:
                return "bar", "bar", "Barra", fits[0][2][:size]
        return None
