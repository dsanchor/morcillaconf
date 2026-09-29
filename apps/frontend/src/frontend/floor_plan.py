"""Top-down plan of the dining room: tables, the bar, the groups, the customer and the waiter.

Standard library and the public contracts only. The waiter's position is a CSS
state of the ``planta`` wrapper: ``barra`` (resting), ``llegando`` (walks to
the door once) and ``atendiendo`` (standing by the customer).

Without a room (seating off) the plan is the decorative room of phase 3. With
a ``RoomView`` the places are drawn by display order: the first four tables
take the four round tables, the fifth the long table, and the bar's stools
follow the counter. Each table draws as many chairs as its capacity (round
tables up to 6, the long table up to 6, the bar up to 8 stools). Places beyond
those slots are not drawn.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from html import escape

from restaurant_contracts.seating import RoomPlace, RoomView, SeatingView

PLAN_W, PLAN_H = 1000, 340
DOOR_GAP = (80, 168)
CUSTOMER = (124, 264)
WAITER_AT_BAR = (560, 124)
WAITER_AT_DOOR = (192, 272)
ROUND_TABLES = ((300, 104), (300, 244), (462, 174), (858, 246))
LONG_TABLE = (582, 206, 200, 46)
STOOLS = tuple((x, 88) for x in (628, 674, 720, 766, 812, 858)) + ((910, 116), (910, 160))
WAITER_STATES = ("barra", "llegando", "atendiendo")
WAITER_LABELS = {
    "barra": "el camarero, en la barra",
    "llegando": "el camarero se acerca a la entrada",
    "atendiendo": "el camarero, atendiéndote",
}
WOOD = 'fill="#6b4a2f" stroke="#9a7550" stroke-width="2"'
SEAT = 'fill="#1f150e" stroke="#8a6644" stroke-width="2.5"'
CUSTOMER_BODY = "#8c1c2b"
GUEST_BODY = "#7b6553"
# Behind the customer at the door, where companions gather before walking in.
COMPANION_OFFSETS = ((-26, 34), (26, 34), (-52, 14), (52, 14), (0, 56), (-52, 50), (52, 50))
# The group walks along the aisle between the left tables before sitting.
AISLE_Y = 174
WALK_SECONDS = 3.6
ROUND_SLOTS = 4
MAX_ROUND_CHAIRS = 6
MAX_LONG_CHAIRS = 6


def _defs() -> str:
    return """
<defs>
  <pattern id="p-baldosa" width="34" height="34" patternUnits="userSpaceOnUse">
    <rect width="34" height="34" fill="#39221a"/>
    <path d="M0,0 H34 M0,0 V34" stroke="#4b2d21" stroke-width="2"/>
  </pattern>
  <radialGradient id="p-halo">
    <stop offset="0" stop-color="#f2b04a" stop-opacity=".34"/>
    <stop offset="1" stop-color="#f2b04a" stop-opacity="0"/>
  </radialGradient>
</defs>"""


def _chair(x: float, y: float, vertical: bool) -> str:
    width, height = (20, 28) if vertical else (28, 20)
    return (
        f'<rect x="{x - width / 2}" y="{y - height / 2}" width="{width}" '
        f'height="{height}" rx="5" {SEAT}/>'
    )


def _round_chairs(cx: int, cy: int, count: int) -> list[tuple[float, float, bool]]:
    """Chair centres around a round table; two chairs face each other across it."""

    if count == 2:
        return [(cx - 54, cy, True), (cx + 54, cy, True)]
    chairs = []
    for index in range(count):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        x, y = cx + 54 * math.cos(angle), cy + 54 * math.sin(angle)
        chairs.append((round(x, 1), round(y, 1), abs(math.cos(angle)) > 0.7))
    return chairs


def _long_chairs(count: int) -> list[tuple[float, float, bool]]:
    x, y, width, height = LONG_TABLE
    top = (count + 1) // 2
    rows = ((y - 24, top), (y + height + 24, count - top))
    chairs = []
    for row_y, seats in rows:
        for index in range(seats):
            chairs.append((x + width * (index + 1) / (seats + 1), row_y, False))
    return chairs


def table_number(label: str) -> str | None:
    """«3» from the layout label «Mesa 3»; None when the label has no number."""

    match = re.search(r"(\d+)\s*$", label)
    return match.group(1) if match else None


def _number(x: float, y: float, number: str | None) -> str:
    if not number:
        return ""
    return (
        f'<text class="numero-mesa" x="{x}" y="{y}" text-anchor="middle" '
        f'dominant-baseline="central" aria-hidden="true">{escape(number)}</text>'
    )


def _round_table(
    cx: int,
    cy: int,
    chairs: list[tuple[float, float, bool]] | None = None,
    extra: str = "",
    number: str | None = None,
) -> str:
    chairs = chairs if chairs is not None else _round_chairs(cx, cy, 4)
    drawn = "".join(_chair(x, y, vertical) for x, y, vertical in chairs)
    return (
        f"{drawn}<circle{extra} cx=\"{cx}\" cy=\"{cy}\" r=\"34\" {WOOD}/>"
        f'<circle cx="{cx}" cy="{cy}" r="25" fill="none" stroke="#81603f" stroke-width="1.5"/>'
        f"{_number(cx, cy, number)}"
    )


def _long_table(
    chairs: list[tuple[float, float, bool]] | None = None,
    extra: str = "",
    number: str | None = None,
) -> str:
    x, y, width, height = LONG_TABLE
    chairs = chairs if chairs is not None else _long_chairs(6)
    drawn = "".join(_chair(cx, cy, vertical) for cx, cy, vertical in chairs)
    return (
        f'{drawn}<rect{extra} x="{x}" y="{y}" width="{width}" height="{height}" rx="6" {WOOD}/>'
        f"{_number(x + width / 2, y + height / 2, number)}"
    )


def _bar(stools: tuple[tuple[int, int], ...] = STOOLS) -> str:
    counter = (
        '<path d="M600,24 H976 V196 H934 V66 H600 Z" fill="#4a3322" '
        'stroke="#8a6644" stroke-width="2"/>'
        '<path d="M600,66 H934 V196" fill="none" stroke="#b08457" stroke-width="3"/>'
    )
    return counter + "".join(f'<circle cx="{x}" cy="{y}" r="11" {SEAT}/>' for x, y in stools)


def _walls() -> str:
    gap_left, gap_right = DOOR_GAP
    return (
        f'<path d="M{gap_right},316 H976 V24 H24 V316 H{gap_left}" fill="none" '
        'stroke="#cdba95" stroke-width="10" stroke-linecap="square"/>'
        f'<path d="M{gap_left},316 V{316 - (gap_right - gap_left)}" stroke="#cdba95" '
        'stroke-width="3"/>'
        f'<path d="M{gap_right},316 A{gap_right - gap_left},{gap_right - gap_left} 0 0 0 '
        f'{gap_left},{316 - (gap_right - gap_left)}" fill="none" stroke="#cdba95" '
        'stroke-opacity=".45" stroke-width="2" stroke-dasharray="5 6"/>'
    )


def _person(body: str, *, tray: bool = False, collar: bool = False) -> str:
    parts = [f'<ellipse rx="21" ry="13" fill="{body}" stroke="#6b5a4a" stroke-width="1.5"/>']
    if collar:
        parts.append('<path d="M-7,-10 L0,0 L7,-10 Z" fill="#f1e8d6"/>')
    parts.append('<circle r="10" fill="#ead7b3"/>')
    if tray:
        parts.append(
            '<path d="M13,-4 L22,-9" stroke="#15110f" stroke-width="7" stroke-linecap="round"/>'
            '<circle cx="27" cy="-11" r="9" fill="#bdb4a7" stroke="#6f675d" stroke-width="2"/>'
        )
    return "".join(parts)


@dataclass(frozen=True)
class _Slot:
    """Where a place is drawn: its outline markup and its seat centres."""

    place: RoomPlace
    seats: list[tuple[float, float, bool]]
    outline: tuple[str, ...]


def _slots(room: RoomView) -> list[_Slot]:
    slots: list[_Slot] = []
    tables = sorted((p for p in room.places if p.kind == "table"), key=lambda p: (p.display_order, p.place_id))
    for index, place in enumerate(tables[: ROUND_SLOTS + 1]):
        if index < ROUND_SLOTS:
            cx, cy = ROUND_TABLES[index]
            slots.append(_Slot(place, _round_chairs(cx, cy, min(place.capacity, MAX_ROUND_CHAIRS)), ("round", str(cx), str(cy))))
        else:
            slots.append(_Slot(place, _long_chairs(min(place.capacity, MAX_LONG_CHAIRS)), ("long",)))
    bars = sorted((p for p in room.places if p.kind == "bar"), key=lambda p: (p.display_order, p.place_id))
    if bars:
        bar = bars[0]
        slots.append(_Slot(bar, [(x, y, False) for x, y in STOOLS[: bar.capacity]], ("bar",)))
    return slots


def _seat_marks(seats: list[tuple[float, float, bool]], css: str) -> str:
    return "".join(
        f'<circle class="{css}" cx="{x}" cy="{y}" r="15"/>' for x, y, _ in seats
    )


def _figures(seats: list[tuple[float, float, bool]], body: str, css: str) -> str:
    return "".join(
        f'<g class="{css}" transform="translate({x},{y})">{_person(body)}</g>' for x, y, _ in seats
    )


def _slot_seats(slot: _Slot, *, mine: bool | None = None, states: tuple[str, ...] = ("held", "occupied"), party: int | None = None) -> list[tuple[float, float, bool]]:
    """Seats of the slot taken by a group; tables fill their first chairs."""

    place = slot.place
    if place.kind == "bar":
        return [
            slot.seats[seat.position - 1]
            for seat in place.seats
            if seat.position <= len(slot.seats) and seat.state in states and (mine is None or seat.mine == mine)
        ]
    return slot.seats[: min(party or place.party_size or 0, len(slot.seats))]


def _place_markup(slot: _Slot) -> str:
    """Wood, chairs and the reserved or proposed outline of one place."""

    place = slot.place
    table_css = ""
    if place.kind == "table" and place.state == "held":
        table_css = ' class="mesa propia"' if place.mine else ' class="mesa reservada"'
    number = table_number(place.label)
    if slot.outline[0] == "round":
        return _round_table(int(slot.outline[1]), int(slot.outline[2]), slot.seats, table_css, number)
    if slot.outline[0] == "long":
        return _long_table(slot.seats, table_css, number)
    return _bar(tuple((int(x), int(y)) for x, y, _ in slot.seats))


@dataclass(frozen=True)
class _Walk:
    """Seated group of the customer: seats, and the walk from the door if it is still playing."""

    seats: list[tuple[float, float, bool]]
    elapsed: float | None


def _walking_group(walk: _Walk, name: str) -> str:
    """The customer and companions: at the door, then walking to their seats.

    The animation is CSS only. ``elapsed`` becomes a negative delay, so a
    re-render continues the walk instead of starting it again.
    """

    parts = []
    doors = [CUSTOMER] + [(CUSTOMER[0] + dx, CUSTOMER[1] + dy) for dx, dy in COMPANION_OFFSETS]
    for index, (x1, y1, _) in enumerate(walk.seats):
        x0, y0 = doors[min(index, len(doors) - 1)]
        css = "comensal cliente-propio" if index == 0 else "comensal acompanante"
        label = ""
        if index == 0:
            shown = name if len(name) <= 16 else f"{name[:15]}…"
            label = f'<text y="-24" text-anchor="middle">{escape(shown)}</text>'
        if walk.elapsed is None:
            parts.append(
                f'<g class="{css}" transform="translate({x1},{y1})">{_person(CUSTOMER_BODY)}{label}</g>'
            )
            continue
        delay = index * 0.12 - walk.elapsed
        xm = 200 if x1 < 380 else 380
        parts.append(
            f'<g class="{css} caminando" transform="translate({x1},{y1})" '
            f'style="--x0:{x0}px;--y0:{y0}px;--xm:{xm}px;--ym:{AISLE_Y}px;'
            f'--x1:{x1}px;--y1:{y1}px;animation-delay:{delay:.2f}s">'
            f"{_person(CUSTOMER_BODY)}{label}</g>"
        )
    return "".join(parts)


def _room_layers(room: RoomView, seating: SeatingView | None) -> tuple[str, str, list[str], list[tuple[float, float, bool]]]:
    """Places, other groups, aria descriptions and the customer's own seats."""

    places, groups, described = [], [], []
    own: list[tuple[float, float, bool]] = []
    proposal = seating.proposal if seating is not None and seating.status == "proposed" else None
    for slot in _slots(room):
        place = slot.place
        places.append(_place_markup(slot))
        if place.kind == "table":
            if place.state == "free":
                continue
            if place.mine and place.state == "occupied":
                own = _slot_seats(slot)
                described.append(f"{place.label}: tu grupo")
            elif place.mine:
                size = proposal.party_size if proposal and proposal.place.place_id == place.place_id else place.party_size
                groups.append(_seat_marks(_slot_seats(slot, party=size), "asiento-propio"))
                described.append(f"{place.label}: tu propuesta para {size}")
            elif place.state == "held":
                groups.append(_seat_marks(slot.seats, "asiento-reservado"))
                described.append(f"{place.label}: reservada")
            else:
                groups.append(_figures(_slot_seats(slot), GUEST_BODY, "grupo"))
                described.append(f"{place.label}: ocupada por un grupo de {place.party_size}")
            continue
        others_held = _slot_seats(slot, mine=False, states=("held",))
        others = _slot_seats(slot, mine=False, states=("occupied",))
        mine_held = _slot_seats(slot, mine=True, states=("held",))
        mine_seated = _slot_seats(slot, mine=True, states=("occupied",))
        groups.append(_seat_marks(others_held, "asiento-reservado"))
        groups.append(_figures(others, GUEST_BODY, "grupo"))
        groups.append(_seat_marks(mine_held, "asiento-propio"))
        own = own or mine_seated
        taken = len(others_held) + len(others) + len(mine_held) + len(mine_seated)
        described.append(f"{place.label}: {taken} de {place.capacity} puestos ocupados")
    return "".join(places), "".join(groups), described, own


def floor_plan_svg(
    customer_name: str | None,
    waiter: str = "barra",
    *,
    room: RoomView | None = None,
    seating: SeatingView | None = None,
    walk_elapsed: float | None = None,
) -> str:
    """Plan with the customer, the waiter in the given state and, with a room, every group.

    ``walk_elapsed`` (seconds since the group was seated in this browser) plays
    the walk to the seats once; ``None`` draws the group already seated.
    """

    if waiter not in WAITER_STATES:
        raise ValueError(f"Unknown waiter state: {waiter}")
    waiter_x, waiter_y = WAITER_AT_DOOR if waiter == "atendiendo" else WAITER_AT_BAR
    customer = ""
    live = room is not None and room.seating_enabled
    label = "Plano del comedor" if live else "Plano del comedor: mesas 1 a 5 y barra, vacías"
    tables = ""
    groups = ""
    own_seats: list[tuple[float, float, bool]] = []
    if live:
        tables, groups, described, own_seats = _room_layers(room, seating)
        if described:
            label += ": " + "; ".join(escape(item) for item in described)
    else:
        # Decorative room: the drawing's slots are numbered 1 to 5.
        tables = "".join(
            _round_table(cx, cy, number=str(index))
            for index, (cx, cy) in enumerate(ROUND_TABLES, start=1)
        ) + _long_table(number=str(len(ROUND_TABLES) + 1))
    seated = bool(own_seats) and seating is not None and seating.status == "seated"
    if customer_name and seated:
        playing = walk_elapsed if walk_elapsed is not None and 0 <= walk_elapsed < WALK_SECONDS else None
        customer = f'<g class="grupo-propio">{_walking_group(_Walk(own_seats, playing), customer_name)}</g>'
        label += f"; {escape(customer_name)} y su grupo, sentados"
    elif customer_name:
        shown = customer_name if len(customer_name) <= 16 else f"{customer_name[:15]}…"
        cx, cy = CUSTOMER
        customer = (
            f'<g class="cliente" transform="translate({cx},{cy})">'
            '<circle r="44" fill="url(#p-halo)"/>'
            f"{_person(CUSTOMER_BODY)}"
            f'<text y="-30" text-anchor="middle">{escape(shown)}</text>'
            "</g>"
        )
        label += f"; {escape(customer_name)} en la entrada"
    label += f"; {WAITER_LABELS[waiter]}."
    bar = "" if live else _bar()
    return (
        f'<svg class="planta {waiter}" viewBox="0 0 {PLAN_W} {PLAN_H}" role="img" '
        f'aria-label="{label}">'
        f"{_defs()}"
        '<rect x="24" y="24" width="952" height="292" fill="url(#p-baldosa)"/>'
        f"{bar}{tables}{groups}{_walls()}{customer}"
        f'<g class="camarero" transform="translate({waiter_x},{waiter_y})">'
        f"{_person('#15110f', tray=True, collar=True)}</g>"
        "</svg>"
    )


def waiter_icon_svg() -> str:
    """The waiter seen from above with his tray, as in the plan, for the conversation."""

    return (
        '<svg class="icono" viewBox="-25 -30 70 52" aria-hidden="true" focusable="false">'
        f"{_person('#15110f', tray=True, collar=True)}</svg>"
    )


def customer_icon_svg() -> str:
    """The customer seen from above, as in the plan, for the identity."""

    return (
        '<svg class="icono" viewBox="-24 -24 48 48" aria-hidden="true" focusable="false">'
        f"{_person('#8c1c2b')}</svg>"
    )
