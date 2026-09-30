"""Simulated room for the fake BFF: the seating service's rules, in memory.

One group per table (best fit by capacity), contiguous bar stools that leave
the smallest gap, holds that expire, confirmation and rejection with a
versioned proposal, and an anonymous room view. It lives with its
``FakeRestaurant``, so it simulates one browser session: parallel customers
need the real BFF (``FRONTEND_BFF_CLIENT=http``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from restaurant_contracts.seating import (
    RoomPlace,
    RoomSeat,
    RoomView,
    SeatingPlace,
    SeatingProposal,
    SeatingView,
)

LAYOUT = (
    ("table-01", "table", "Mesa 1", 2, 10),
    ("table-02", "table", "Mesa 2", 2, 20),
    ("table-03", "table", "Mesa 3", 4, 30),
    ("table-04", "table", "Mesa 4", 4, 40),
    ("table-05", "table", "Mesa 5", 6, 50),
    ("bar", "bar", "Barra", 8, 100),
)
Preference = Literal["table", "bar", "any"]

_NUMBERS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}
_PARTY = re.compile(
    r"\b(?:somos|venimos|seremos)\s+(?P<count>\d{1,2}|" + "|".join(_NUMBERS) + r")\b",
    re.IGNORECASE,
)
_BAR = re.compile(r"\bbarra\b", re.IGNORECASE)
_TABLE = re.compile(r"\bmesa\b", re.IGNORECASE)
_YES = re.compile(r"^\W*(?:s[ií]|vale|ok|de acuerdo|confirm\w*)\b", re.IGNORECASE)


def party_size(text: str) -> int | None:
    match = _PARTY.search(text)
    if match is None:
        return None
    raw = match["count"].casefold()
    return min(max(int(raw) if raw.isdigit() else _NUMBERS[raw], 1), 20)


def stools_text(seats: list[int]) -> str:
    if len(seats) == 1:
        return f"la barra, puesto {seats[0]}"
    return f"la barra, puestos {seats[0]} a {seats[-1]}"


@dataclass
class _Hold:
    owner: str
    place_id: str
    kind: Literal["table", "bar"]
    label: str
    capacity: int
    seats: list[int]
    party_size: int
    proposal_id: str
    version: int
    expires_at: datetime | None
    status: Literal["held", "occupied"] = "held"
    seated_at: datetime | None = None

    def place(self) -> SeatingPlace:
        return SeatingPlace(
            place_id=self.place_id,
            kind=self.kind,
            label=self.label,
            capacity=self.capacity,
            seats=self.seats if self.kind == "bar" else [],
        )

    def text(self) -> str:
        return stools_text(self.seats) if self.kind == "bar" else f"la {self.label}"


class FakeRoom:
    def __init__(self, clock: Callable[[], datetime], *, hold_minutes: int = 5) -> None:
        self._clock = clock
        self._hold = timedelta(minutes=hold_minutes)
        self._holds: dict[str, _Hold] = {}
        self._decided: dict[str, str] = {}
        self._numbers = 0

    # Views

    def seating(self, owner: str) -> SeatingView:
        hold = self._holds.get(owner)
        if hold is None:
            return SeatingView()
        if hold.status == "occupied":
            return SeatingView(
                status="seated",
                place=hold.place(),
                party_size=hold.party_size,
                seated_at=hold.seated_at or self._clock(),
            )
        return SeatingView(
            status="proposed",
            proposal=SeatingProposal(
                proposal_id=hold.proposal_id,
                version=hold.version,
                place=hold.place(),
                party_size=hold.party_size,
                expires_at=hold.expires_at or self._clock(),
            ),
        )

    def view(self, owner: str) -> RoomView:
        live = self._live()
        places = []
        for place_id, kind, label, capacity, order in LAYOUT:
            groups = [hold for hold in live if hold.place_id == place_id]
            if kind == "table":
                group = groups[0] if groups else None
                places.append(
                    RoomPlace(
                        place_id=place_id,
                        kind=kind,
                        label=label,
                        capacity=capacity,
                        display_order=order,
                        state="free" if group is None else group.status,
                        party_size=None if group is None else group.party_size,
                        mine=group is not None and group.owner == owner,
                    )
                )
                continue
            by_seat = {seat: hold for hold in groups for seat in hold.seats}
            seats = [
                RoomSeat(
                    position=position,
                    state=by_seat[position].status if position in by_seat else "free",
                    mine=position in by_seat and by_seat[position].owner == owner,
                )
                for position in range(1, capacity + 1)
            ]
            free = any(seat.state == "free" for seat in seats)
            places.append(
                RoomPlace(
                    place_id=place_id,
                    kind=kind,
                    label=label,
                    capacity=capacity,
                    display_order=order,
                    state="free" if free else "occupied",
                    mine=any(seat.mine for seat in seats),
                    seats=seats,
                )
            )
        return RoomView(
            schema_version=1, seating_enabled=True, generated_at=self._clock(), places=places
        )

    # The scripted waiter

    def converse(self, owner: str, text: str, size: int, party_known: bool) -> str | None:
        """Hold like the real waiter's tool call; returns the waiter's note."""

        own = self._holds.get(owner)
        said_party, bar, table = party_size(text) is not None, _BAR.search(text), _TABLE.search(text)
        if own is not None and own.status == "held" and not (said_party or bar or table):
            # Writing instead of pressing a button: the hold stays and the
            # buttons come back, like the real waiter.
            if _YES.search(text):
                return f"Para confirmar hay que pulsar «Confirmar»; {own.text()} sigue reservada para vosotros."
            return None
        if not (said_party or bar or table):
            return None
        if own is not None and own.status == "occupied":
            return f"Ya estáis sentados en {own.text()}."
        if table and not said_party and not party_known:
            return "¿Cuántos sois?"
        preference: Preference = "bar" if bar else "table" if table else "any"
        hold = self.hold(owner, size, preference)
        if hold is None:
            if preference == "table":
                return f"No queda ninguna mesa libre para {size}. Si queréis, os busco sitio en la barra."
            return f"Lo siento, ahora mismo no hay sitio para {size}."
        buttons = "Confirmadlo o rechazadlo con los botones."
        if hold.kind == "bar":
            lead = f"No queda mesa libre para {size}; os propongo {hold.text()}." if preference == "any" else f"Os propongo {hold.text()}."
            return f"{lead} {buttons}"
        return f"Os propongo {hold.text()} para {size}. {buttons}"

    # Operations

    def hold(self, owner: str, size: int, preference: Preference) -> _Hold | None:
        current = self._holds.get(owner)
        if current is not None and current.status == "occupied":
            return None
        choice = self._choose(size, preference, ignore=current)
        if choice is None:
            return None
        place_id, kind, label, capacity, seats = choice
        self._numbers += 1
        hold = _Hold(
            owner=owner,
            place_id=place_id,
            kind=kind,
            label=label,
            capacity=capacity,
            seats=seats,
            party_size=size,
            proposal_id=f"prop_{self._numbers}",
            version=1,
            expires_at=self._clock() + self._hold,
        )
        self._holds[owner] = hold
        return hold

    def decide(self, owner: str, proposal_id: str, version: int, decision: str) -> tuple[str, str]:
        """Returns (outcome, text): seated, rejected, expired, stale, none or decided."""

        previous = self._decided.get(proposal_id)
        if previous is not None:
            return ("repeated", "") if previous == decision else ("decided", "Esa propuesta ya está decidida.")
        hold = self._holds.get(owner)
        if hold is None or hold.status != "held":
            return "none", "No tengo ninguna propuesta de sitio pendiente para ti."
        if hold.proposal_id != proposal_id or hold.version != version:
            return "stale", "Esa propuesta ya no está vigente."
        text = hold.text()
        if decision == "rejected":
            del self._holds[owner]
            self._decided[proposal_id] = decision
            return "rejected", f"Sin problema, dejo libre {text}. ¿Preferís otro sitio?"
        if hold.expires_at is not None and hold.expires_at <= self._clock():
            del self._holds[owner]
            self._decided[proposal_id] = "expired"
            return "expired", f"La reserva de {text} ha caducado y ya está libre. Si queréis, pedidme sitio otra vez."
        hold.status = "occupied"
        hold.version += 1
        hold.expires_at = None
        hold.seated_at = self._clock()
        self._decided[proposal_id] = decision
        return "seated", f"¡Estupendo! Os acompaño a {text}. ¿Qué queréis tomar?"

    def leave(self, owner: str) -> str | None:
        """/new: refuse while seated, cancel a pending proposal."""

        hold = self._holds.get(owner)
        if hold is None:
            return None
        if hold.status == "occupied":
            return f"Ya estáis sentados en {hold.text()}. Podréis empezar otra visita cuando se libere."
        del self._holds[owner]
        return None

    def drop_expired(self, owner: str) -> None:
        hold = self._holds.get(owner)
        if hold is not None and hold.status == "held" and hold.expires_at is not None and hold.expires_at <= self._clock():
            del self._holds[owner]

    def _live(self) -> list[_Hold]:
        now = self._clock()
        return [
            hold
            for hold in self._holds.values()
            if hold.status == "occupied" or (hold.expires_at is not None and hold.expires_at > now)
        ]

    def _choose(self, size: int, preference: Preference, *, ignore: _Hold | None):
        live = [hold for hold in self._live() if hold is not ignore]
        busy = {hold.place_id for hold in live}
        if preference in ("table", "any"):
            tables = sorted(
                (capacity, order, place_id, label)
                for place_id, kind, label, capacity, order in LAYOUT
                if kind == "table" and capacity >= size and place_id not in busy
            )
            if tables:
                capacity, _, place_id, label = tables[0]
                return place_id, "table", label, capacity, []
        if preference in ("bar", "any"):
            for place_id, kind, label, capacity, _ in LAYOUT:
                if kind != "bar":
                    continue
                taken = {seat for hold in live if hold.place_id == place_id for seat in hold.seats}
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
                    return place_id, "bar", label, capacity, fits[0][2][:size]
        return None
