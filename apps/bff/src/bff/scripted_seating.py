"""In-memory seating for the scripted waiter: tests and offline development only.

It never talks to the seating MCP. It plays the waiter's seating side with the
same rules and the same report as the real agent: one group per table (the
smallest that fits), contiguous bar stools that leave the smallest gap, holds
that expire, a confirmation that waits for the customer's button decision, and
a message written while it is pending that keeps the hold and shows the
buttons again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from restaurant_contracts.waiter import SeatingReport

DEMO_LAYOUT = (
    ("table-01", "table", "Mesa 1", 2, 10),
    ("table-02", "table", "Mesa 2", 2, 20),
    ("table-03", "table", "Mesa 3", 4, 30),
    ("table-04", "table", "Mesa 4", 4, 40),
    ("table-05", "table", "Mesa 5", 6, 50),
    ("bar", "bar", "Barra", 8, 100),
)
REPLIES = {
    "confirmed": "¡Estupendo! Os acompaño a {place}. ¿Qué queréis tomar?",
    "rejected": "Sin problema, dejo libre {place}. ¿Preferís otro sitio?",
    "expired": (
        "La reserva de {place} ha caducado y ya está libre. "
        "Si queréis, pedidme sitio otra vez."
    ),
}
Preference = Literal["table", "bar", "any"]


class ScriptedSeatingUnavailable(RuntimeError):
    """The simulated seating service is down."""


class ScriptedNoPendingDecision(RuntimeError):
    """No confirmation waits for this decision."""


@dataclass
class _Hold:
    visit_id: str
    number: int
    resource_id: str
    kind: str
    label: str
    capacity: int
    seats: list[int]
    party_size: int
    status: str
    expires_at: datetime | None
    version: int = 1
    seated_at: datetime | None = None

    @property
    def token(self) -> str:
        return hashlib.sha256(f"scripted-{self.number}".encode()).hexdigest()[:24]

    def place_text(self) -> str:
        if self.kind == "bar":
            if len(self.seats) == 1:
                return f"la barra, puesto {self.seats[0]}"
            return f"la barra, puestos {self.seats[0]} a {self.seats[-1]}"
        return f"la {self.label}"


class ScriptedSeating:
    """In-memory seating authority used only by internal tests."""

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        *,
        hold_minutes: int = 5,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._hold = timedelta(minutes=hold_minutes)
        self._holds: list[_Hold] = []
        self._outcomes: dict[str, dict[str, str]] = {}
        self._numbers = 0
        self.available = True

    # Test helpers

    def reset(self) -> None:
        self._holds.clear()

    def expire_all(self) -> None:
        for hold in self._holds:
            if hold.status == "held":
                hold.expires_at = self._clock() - timedelta(seconds=1)

    def holds(self, visit_id: str) -> list[_Hold]:
        return [hold for hold in self._live() if hold.visit_id == visit_id]

    # The waiter's side

    def own(self, visit_id: str) -> _Hold | None:
        self._check()
        return next((hold for hold in self._live() if hold.visit_id == visit_id), None)

    def available_kinds(self, party_size: int) -> list[str]:
        """Seating kinds currently able to accept the party."""

        self._check()
        return [
            kind
            for kind in ("table", "bar")
            if self._choose(party_size, kind, ignore=None) is not None
        ]

    def hold(self, visit_id: str, party_size: int, preference: Preference) -> _Hold | None:
        """Hold a place; a new request replaces the visit's pending hold."""

        self._check()
        current = self.own(visit_id)
        if current is not None and current.status == "occupied":
            return None
        if (
            current is not None
            and current.party_size == party_size
            and preference in ("any", current.kind)
        ):
            # The same request again: the same hold, like the MCP's idempotency.
            return current
        choice = self._choose(party_size, preference, ignore=current)
        if choice is None:
            return None
        if current is not None:
            self._holds.remove(current)
        resource_id, kind, label, capacity, seats = choice
        self._numbers += 1
        hold = _Hold(
            visit_id=visit_id,
            number=self._numbers,
            resource_id=resource_id,
            kind=kind,
            label=label,
            capacity=capacity,
            seats=seats,
            party_size=party_size,
            status="held",
            expires_at=self._clock() + self._hold,
        )
        self._holds.append(hold)
        self._outcomes.pop(visit_id, None)
        return hold

    def decide(self, visit_id: str, token: str, approved: bool) -> tuple[str, str]:
        """Answer the paused confirmation; returns the outcome and the fixed reply."""

        self._check()
        pending = next(
            (hold for hold in self._holds if hold.visit_id == visit_id and hold.status == "held"),
            None,
        )
        if pending is None or pending.token != token:
            raise ScriptedNoPendingDecision("There is no seating confirmation waiting")
        place = pending.place_text()
        self._holds.remove(pending)
        if pending.expires_at is not None and pending.expires_at <= self._clock():
            self._outcomes[visit_id] = {"decision": "expired", "place": pending.label}
            return "expired", REPLIES["expired"].format(place=place)
        if not approved:
            self._outcomes[visit_id] = {"decision": "rejected", "place": pending.label}
            return "rejected", REPLIES["rejected"].format(place=place)
        pending.status = "occupied"
        pending.version += 1
        pending.expires_at = None
        pending.seated_at = self._clock()
        self._holds.append(pending)
        self._outcomes[visit_id] = {"decision": "confirmed", "place": pending.label}
        return "confirmed", REPLIES["confirmed"].format(place=place)

    def awaiting(self, visit_id: str) -> bool:
        hold = self.own(visit_id)
        return hold is not None and hold.status == "held"

    def report(self, visit_id: str) -> SeatingReport:
        self._check()
        own = self.own(visit_id)
        report: dict[str, Any] = {
            "status": "none",
            "last_outcome": self._outcomes.get(visit_id),
            "room": self._room(),
        }
        if own is not None:
            report.update(
                status="proposed" if own.status == "held" else "seated",
                awaiting_decision=own.status == "held",
                token=own.token,
                place={
                    "place_id": own.resource_id,
                    "kind": own.kind,
                    "label": own.label,
                    "capacity": own.capacity,
                    "seats": own.seats if own.kind == "bar" else [],
                },
                party_size=own.party_size,
                version=own.version if own.status == "held" else None,
                expires_at=own.expires_at,
                seated_at=own.seated_at,
            )
        return SeatingReport.model_validate(report)

    # Rules

    def _check(self) -> None:
        if not self.available:
            raise ScriptedSeatingUnavailable("The seating service could not be reached")

    def _live(self) -> list[_Hold]:
        now = self._clock()
        return [
            hold
            for hold in self._holds
            if hold.status == "occupied" or (hold.expires_at is not None and hold.expires_at > now)
        ]

    def _room(self) -> list[dict[str, Any]]:
        live = self._live()
        places = []
        for resource_id, kind, label, capacity, order in DEMO_LAYOUT:
            groups = [hold for hold in live if hold.resource_id == resource_id]
            place: dict[str, Any] = {
                "place_id": resource_id,
                "kind": kind,
                "label": label,
                "capacity": capacity,
                "display_order": order,
                "state": "free",
                "seats": [],
            }
            if kind == "table":
                if groups:
                    place.update(
                        state=groups[0].status,
                        party_size=groups[0].party_size,
                        expires_at=groups[0].expires_at,
                    )
            else:
                by_seat = {seat: hold for hold in groups for seat in hold.seats}
                place["seats"] = [
                    {
                        "position": position,
                        "state": by_seat[position].status if position in by_seat else "free",
                        "expires_at": by_seat[position].expires_at if position in by_seat else None,
                    }
                    for position in range(1, capacity + 1)
                ]
                if len(by_seat) == capacity:
                    place["state"] = "occupied"
            places.append(place)
        return places

    def _choose(self, size: int, preference: Preference, *, ignore: _Hold | None):
        live = [hold for hold in self._live() if hold is not ignore]
        busy = {hold.resource_id for hold in live}
        if preference in ("table", "any"):
            tables = sorted(
                (capacity, order, resource_id, label)
                for resource_id, kind, label, capacity, order in DEMO_LAYOUT
                if kind == "table" and capacity >= size and resource_id not in busy
            )
            if tables:
                capacity, _, resource_id, label = tables[0]
                return resource_id, "table", label, capacity, []
        if preference in ("bar", "any"):
            for resource_id, kind, label, capacity, _ in DEMO_LAYOUT:
                if kind != "bar":
                    continue
                taken = {seat for hold in live if hold.resource_id == resource_id for seat in hold.seats}
                runs, run = [], []
                for position in range(1, capacity + 1):
                    if position in taken:
                        if run:
                            runs.append(run)
                        run = []
                    else:
                        run.append(position)
                if run:
                    runs.append(run)
                fits = sorted((len(run) - size, run[0], run) for run in runs if len(run) >= size)
                if fits:
                    return resource_id, "bar", label, capacity, fits[0][2][:size]
        return None
