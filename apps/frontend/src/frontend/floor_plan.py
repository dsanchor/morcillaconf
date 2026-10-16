"""Top-down plan of the dining room: tables, the bar, the groups, the customer and the waiter.

The till stands inside the room by the exit door, at the bottom left: the
cashier behind a small counter, lit while a bill waits for card or cash.

Standard library and the public contracts only. The waiter's position is a CSS
state of the ``planta`` wrapper: ``barra`` (resting), ``llegando`` (walks to
the door once) and ``atendiendo`` (standing by the customer). Once seated,
the waiter follows the customer's walk and stays beside their seat.

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

PLAN_W, PLAN_H = 1280, 340
ROOM_BOTTOM = PLAN_H - 24
ROOM_RIGHT = PLAN_W - 24
DOOR_GAP = (80, 168)
CUSTOMER = (124, PLAN_H - 76)
WAITER_AT_BAR = (414, 156)
WAITER_AT_DOOR = (192, PLAN_H - 68)
ROUND_TABLES = ((760, 244), (1040, 244), (760, 104), (1040, 104))
LONG_TABLE = (480, 74, 46, 200)
BAR_COUNTER = (62, 42, 324, 34)
STOOLS = tuple((82 + index * 40, 102) for index in range(8))
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
CASHIER_BODY = "#1f5b3f"
# The cashier stands against the left wall, facing the room, just above the door.
CASHIER = (42, PLAN_H - 159)
TILL_COUNTER = (58, PLAN_H - 190, 20, 62)
# Behind the customer at the door, where companions gather before walking in.
COMPANION_OFFSETS = ((-26, 34), (26, 34), (-52, 14), (52, 14), (0, 56), (-52, 50), (52, 50))
# The first aisle runs between the bar and the six-seat table.
AISLE_Y = 130
WALK_SECONDS = 3.6
SERVE_SECONDS = 2.6
# The pass: the hatch in the right wall through which the kitchen hands out dishes.
PASS = (PLAN_W - 56, 258)
# Keep the tray clear of the pass's wall, not just the waiter's centre.
WAITER_AT_PASS = (ROOM_RIGHT - 44, PASS[1])
# The hatch's span in the right wall; the kitchen opens its left wall at the same height.
PASS_OPENING = (228, 288)
SERVE_MIDPOINT = (WAITER_AT_PASS[0], 192)
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
    left = (count + 1) // 2
    rows = ((x - 24, left), (x + width + 24, count - left))
    chairs = []
    for column_x, seats in rows:
        for index in range(seats):
            chairs.append((column_x, y + height * (index + 1) / (seats + 1), True))
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
    drawn = "".join(
        f'<g transform="rotate({math.degrees(math.atan2(y - cy, x - cx)) + 90:.1f},{x},{y})">'
        f'{_chair(x, y, False)}</g>'
        for x, y, _ in chairs
    )
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
    x, y, width, height = BAR_COUNTER
    counter = (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="6" '
        'fill="#4a3322" stroke="#8a6644" stroke-width="2"/>'
        f'<path d="M{x + 8},{y + height} H{x + width - 8}" fill="none" '
        'stroke="#b08457" stroke-width="3"/>'
    )
    return counter + "".join(f'<circle cx="{x}" cy="{y}" r="11" {SEAT}/>' for x, y in stools)


def _walls() -> str:
    gap_left, gap_right = DOOR_GAP
    pass_top, pass_bottom = PASS_OPENING
    return (
        f'<path d="M{gap_right},{ROOM_BOTTOM} H{ROOM_RIGHT} V{pass_bottom} M{ROOM_RIGHT},{pass_top} V24 H24 V{ROOM_BOTTOM} H{gap_left}" '
        'fill="none" stroke="#cdba95" stroke-width="10" stroke-linecap="square"/>'
        f'<path d="M{gap_left},{ROOM_BOTTOM} V{ROOM_BOTTOM - (gap_right - gap_left)}" stroke="#cdba95" '
        'stroke-width="3"/>'
        f'<path d="M{gap_right},{ROOM_BOTTOM} A{gap_right - gap_left},{gap_right - gap_left} 0 0 0 '
        f'{gap_left},{ROOM_BOTTOM - (gap_right - gap_left)}" fill="none" stroke="#cdba95" '
        'stroke-opacity=".45" stroke-width="2" stroke-dasharray="5 6"/>'
    )


def _cashier_station(pending: bool, active: bool = False) -> str:
    """The till by the door: the cashier, a small counter and the till on it."""

    x, y = CASHIER
    left, top, width, height = TILL_COUNTER
    css = "caja pendiente" if pending else "caja"
    if active:
        css += " trabajando"
    status = "en actividad" if active else "cuenta pendiente de pago" if pending else "en espera"
    return (
        f'<g class="{css}" aria-label="Caja: {status}">'
        f'<circle class="halo-caja" cx="{x + 6}" cy="{y}" r="38" fill="url(#p-halo)"/>'
        f'<rect x="{left}" y="{top}" width="{width}" height="{height}" rx="3" {WOOD}/>'
        f'<rect x="{left + 2.5}" y="{top + 16}" width="{width - 5}" height="24" rx="2" '
        'fill="#1c1410" stroke="#8a6644" stroke-width="1.5"/>'
        f'<rect class="pantalla-caja" x="{left + 5}" y="{top + 19}" width="{width - 10}" '
        'height="7" rx="1"/>'
        f'<g class="cajero" transform="translate({x},{y}) rotate(90)">'
        f"{_person(CASHIER_BODY, collar=True)}</g>"
        f'<text class="rotulo-caja" x="{left + width / 2}" y="{top - 9}" '
        'text-anchor="middle" aria-hidden="true">caja</text>'
        "</g>"
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


def _motion_path(
    x0: float, y0: float, xm: float, ym: float, x1: float, y1: float
) -> str:
    return (
        f"--recorrido:path('M {x0} {y0} Q {x0} {ym} {xm} {ym} "
        f"Q {x1} {ym} {x1} {y1}');"
    )


def _customer_motion_path(
    x0: float, y0: float, xm: float, ym: float, x1: float, y1: float
) -> str:
    left, _, width, _ = LONG_TABLE
    lower_aisle = ROOM_BOTTOM - 22
    if x1 == left + width + 24:
        corridor = left + width + 84
        return (
            f"--recorrido:path('M {x0} {y0} Q {x0} {lower_aisle} {corridor - 20} {lower_aisle} "
            f"Q {corridor} {lower_aisle} {corridor} {lower_aisle - 20} L {corridor} {y1 + 20} "
            f"Q {corridor} {y1} {corridor - 20} {y1} L {x1} {y1}');"
        )
    if ym < AISLE_Y:
        return (
            f"--recorrido:path('M {x0} {y0} Q {x0} {AISLE_Y} 400 {AISLE_Y} "
            f"Q 420 {AISLE_Y} 420 {AISLE_Y - 20} L 420 {ym + 20} "
            f"Q 420 {ym} 440 {ym} Q {x1} {ym} {x1} {y1}');"
        )
    return _motion_path(x0, y0, xm, ym, x1, y1)


def _walk_style(
    seat: tuple[float, float, bool], index: int, elapsed: float
) -> str:
    doors = [CUSTOMER] + [(CUSTOMER[0] + dx, CUSTOMER[1] + dy) for dx, dy in COMPANION_OFFSETS]
    x0, y0 = doors[min(index, len(doors) - 1)]
    x1, y1, _ = seat
    xm = 200 if x1 < 420 else 420
    left, top, width, height = LONG_TABLE
    if x1 > left + width:
        aisle_y = 42 if y1 < top + height / 2 else ROOM_BOTTOM - 22
    elif left <= x1 <= left + width and y1 > top + height:
        aisle_y = ROOM_BOTTOM - 22
    else:
        aisle_y = AISLE_Y
    return (
        f"--x0:{x0}px;--y0:{y0}px;--xm:{xm}px;--ym:{aisle_y}px;"
        f"--x1:{x1}px;--y1:{y1}px;"
        f"{_customer_motion_path(x0, y0, xm, aisle_y, x1, y1)}"
        f"animation-delay:{index * 0.12 - elapsed:.2f}s"
    )


def _walking_group(walk: _Walk, name: str) -> str:
    """The customer and companions: at the door, then walking to their seats.

    The animation is CSS only. ``elapsed`` becomes a negative delay, so a
    re-render continues the walk instead of starting it again.
    """

    parts = []
    for index, (x1, y1, _) in enumerate(walk.seats):
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
        parts.append(
            f'<g class="{css} caminando" transform="translate({x1},{y1})" '
            f'style="{_walk_style(walk.seats[index], index, walk.elapsed)}">'
            f"{_person(CUSTOMER_BODY)}{label}</g>"
        )
    return "".join(parts)


def _room_layers(
    room: RoomView, seating: SeatingView | None
) -> tuple[str, str, list[str], list[tuple[float, float, bool]], _Slot | None]:
    """Places, other groups, aria descriptions, the customer's own seats and their place."""

    places, groups, described = [], [], []
    own: list[tuple[float, float, bool]] = []
    own_slot: _Slot | None = None
    proposal = seating.proposal if seating is not None and seating.status == "proposed" else None
    for slot in _slots(room):
        place = slot.place
        places.append(_place_markup(slot))
        if place.kind == "table":
            if place.state == "free":
                continue
            if place.mine and place.state == "occupied":
                own = _slot_seats(slot)
                own_slot = slot
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
        if mine_seated and not own:
            own, own_slot = mine_seated, slot
        taken = len(others_held) + len(others) + len(mine_held) + len(mine_seated)
        described.append(f"{place.label}: {taken} de {place.capacity} puestos ocupados")
    return "".join(places), "".join(groups), described, own, own_slot


def _plate_spot(slot: _Slot, seat: tuple[float, float, bool]) -> tuple[float, float]:
    """Where a dish rests in front of a seat: on the table or on the counter."""

    x, y, _ = seat
    if slot.outline[0] == "round":
        cx, cy = int(slot.outline[1]), int(slot.outline[2])
        return round(x + (cx - x) * 0.58, 1), round(y + (cy - y) * 0.58, 1)
    if slot.outline[0] == "long":
        left, _, width, _ = LONG_TABLE
        return left + 12 if x < left else left + width - 12, y
    _, top, _, height = BAR_COUNTER
    return x, top + height / 2


def _plates(slot: _Slot, seats: list[tuple[float, float, bool]], count: int) -> str:
    plates = []
    for index in range(min(count, 2 * len(seats))):
        x, y = _plate_spot(slot, seats[index % len(seats)])
        if slot.outline[0] == "long":
            y += 9 * (index // len(seats))
        else:
            x += 9 * (index // len(seats))
        plates.append(
            f'<g class="plato-servido" transform="translate({x},{y})">'
            '<circle r="8" fill="#f1e8d6" stroke="#6f675d" stroke-width="1.5"/>'
            '<circle r="4.5" fill="#8c1c2b"/></g>'
        )
    return "".join(plates)


def _pass_hatch() -> str:
    x, _ = PASS
    top, bottom = PASS_OPENING
    return (
        f'<rect x="{ROOM_RIGHT - 7}" y="{top}" width="14" height="{bottom - top}" rx="2" fill="#4a3322" '
        'stroke="#b08457" stroke-width="2"/>'
        f'<text class="pase" x="{x - 2}" y="{bottom + 16}" text-anchor="middle" aria-hidden="true">pase</text>'
    )


def _waiter_offset(
    slot: _Slot, seat: tuple[float, float, bool]
) -> tuple[float, float]:
    x, y, _ = seat
    if slot.outline[0] == "long":
        return (-48 if x < LONG_TABLE[0] else 48), 0
    if slot.outline[0] == "bar":
        return 0, 32
    cx, cy = float(slot.outline[1]), float(slot.outline[2])
    # The upper aisle is too close to the wall to stand above the customer.
    if cy < LONG_TABLE[1] + LONG_TABLE[3] / 2:
        return -32, 16
    distance = math.hypot(x - cx, y - cy)
    return round(32 * (x - cx) / distance, 1), round(32 * (y - cy) / distance, 1)


@dataclass(frozen=True)
class _Bounds:
    left: float
    top: float
    right: float
    bottom: float

    def contains(self, x: float, y: float) -> bool:
        return self.left < x < self.right and self.top < y < self.bottom


def _waiter_obstacles(slots: list[_Slot], name: str | None, own_seats: list[tuple[float, float, bool]]) -> list[_Bounds]:
    obstacles = []
    for slot in slots:
        if slot.outline[0] == "round":
            cx, cy = float(slot.outline[1]), float(slot.outline[2])
            obstacles.append(_Bounds(cx - 35.5, cy - 35.5, cx + 35.5, cy + 35.5))
        else:
            x, y, width, height = LONG_TABLE if slot.outline[0] == "long" else BAR_COUNTER
            obstacles.append(_Bounds(x - 1, y - 1, x + width + 1, y + height + 1))
        for x, y, vertical in slot.seats:
            if slot.outline[0] == "round":
                angle = math.radians(round(math.degrees(math.atan2(y - cy, x - cx)) + 90, 1))
                half_width = 15.25 * abs(math.cos(angle)) + 11.25 * abs(math.sin(angle))
                half_height = 15.25 * abs(math.sin(angle)) + 11.25 * abs(math.cos(angle))
            elif slot.outline[0] == "long":
                half_width, half_height = (11.25, 15.25) if vertical else (15.25, 11.25)
            else:
                half_width = half_height = 12.25
            # Reserve space for a seated person or reservation mark even at an empty chair.
            half_width, half_height = max(half_width, 22), max(half_height, 16.25)
            obstacles.append(_Bounds(x - half_width, y - half_height, x + half_width, y + half_height))
    x, y = CASHIER
    obstacles.append(_Bounds(x - 14, y - 22, x + 14, y + 22))
    x, y, width, height = TILL_COUNTER
    obstacles.append(_Bounds(x - 1, y - 1, x + width + 1, y + height + 1))
    obstacles.append(_Bounds(ROOM_RIGHT - 8, PASS_OPENING[0] - 1, ROOM_RIGHT + 8, PASS_OPENING[1] + 1))
    if name and own_seats:
        x, y, _ = own_seats[0]
        half_width = min(len(name), 16) * 9
        obstacles.append(_Bounds(x - half_width, y - 44, x + half_width, y - 20))
    return obstacles


def _free_waiter_position(
    seat: tuple[float, float, bool], obstacles: list[_Bounds]
) -> tuple[float, float]:
    # Expand obstacles by the complete silhouette, including the tray, and a 3px gap.
    forbidden = [
        _Bounds(box.left - 37 - 3, box.top - 14 - 3, box.right + 22 + 3, box.bottom + 21 + 3)
        for box in obstacles
    ]
    limits = _Bounds(24 + 5 + 22 + 2, 24 + 5 + 21 + 2, ROOM_RIGHT - 5 - 37 - 2, ROOM_BOTTOM - 5 - 14 - 2)
    cx, cy, _ = seat
    xs = {cx, limits.left, limits.right} | {value for box in forbidden for value in (box.left, box.right)}
    ys = {cy, limits.top, limits.bottom} | {value for box in forbidden for value in (box.top, box.bottom)}
    # The nearest free point lies on an obstacle edge or on the customer's axis.
    candidates = sorted(
        ((x, y) for x in xs for y in ys if limits.left <= x <= limits.right and limits.top <= y <= limits.bottom),
        key=lambda point: ((point[0] - cx) ** 2 + (point[1] - cy) ** 2, abs(point[1] - cy), point),
    )
    for x, y in candidates:
        if not any(box.contains(x, y) for box in forbidden):
            return x, y
    raise ValueError("No free position for the waiter inside the dining room")


def floor_plan_svg(
    customer_name: str | None,
    waiter: str = "barra",
    *,
    room: RoomView | None = None,
    seating: SeatingView | None = None,
    walk_elapsed: float | None = None,
    served_dishes: int = 0,
    serve_elapsed: float | None = None,
    bill_pending: bool = False,
    cashier_active: bool = False,
) -> str:
    """Plan with the customer, the waiter in the given state and, with a room, every group.

    ``walk_elapsed`` (seconds since the group was seated in this browser) plays
    the walk to the seats once; ``None`` draws the group already seated.
    ``served_dishes`` rest on the customer's table; ``serve_elapsed`` plays the
    waiter's walk from the pass with the tray once, before they appear.
    ``bill_pending`` lights the till while a bill waits for card or cash.
    ``cashier_active`` highlights the cashier while its A2A call is running.
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
    own_slot: _Slot | None = None
    if live:
        tables, groups, described, own_seats, own_slot = _room_layers(room, seating)
        if described:
            label += ": " + "; ".join(escape(item) for item in described)
    else:
        # Decorative room: the drawing's slots are numbered 1 to 5.
        tables = "".join(
            _round_table(cx, cy, number=str(index))
            for index, (cx, cy) in enumerate(ROUND_TABLES, start=1)
        ) + _long_table(number=str(len(ROUND_TABLES) + 1))
    seated = bool(own_seats) and seating is not None and seating.status == "seated"
    playing = walk_elapsed if walk_elapsed is not None and 0 <= walk_elapsed < WALK_SECONDS else None
    waiter_offset = (0, 0)
    if seated and own_slot is not None and room is not None:
        waiter = "atendiendo"
        waiter_offset = _waiter_offset(own_slot, own_seats[0])
        waiter_x, waiter_y = _free_waiter_position(
            own_seats[0], _waiter_obstacles(_slots(room), customer_name, own_seats)
        )
    if customer_name and seated:
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
    label += f"; {WAITER_LABELS[waiter]}"
    label += (
        "; la caja, junto a la puerta, con tu cuenta pendiente de pago."
        if bill_pending
        else "; la caja, junto a la puerta."
    )
    if cashier_active:
        label += " Caja está atendiendo tu cuenta."
    bar = "" if live else _bar()
    serving = (
        seated
        and own_slot is not None
        and served_dishes > 0
        and serve_elapsed is not None
        and 0 <= serve_elapsed < SERVE_SECONDS
    )
    plates = ""
    if seated and own_slot is not None and served_dishes and not serving:
        plates = _plates(own_slot, own_seats, served_dishes)
        label += " Tu comida está servida en la mesa."
    if serving:
        x1, y1 = waiter_x, waiter_y
        (x0, y0), (xm, ym) = WAITER_AT_PASS, SERVE_MIDPOINT
        if own_slot.outline[0] == "long":
            lower_aisle = ROOM_BOTTOM - 22
            path = (
                f"--recorrido:path('M {x0} {y0} L {x0} {lower_aisle - 20} "
                f"Q {x0} {lower_aisle} {x0 - 20} {lower_aisle} "
                f"L {x1 + 20} {lower_aisle} Q {x1} {lower_aisle} "
                f"{x1} {lower_aisle - 20} L {x1} {y1}');"
            )
        elif own_slot.outline[0] == "bar":
            path = (
                f"--recorrido:path('M {x0} {y0} L {x0} {ym + 20} "
                f"Q {x0} {ym} {x0 - 20} {ym} L 430 {ym} "
                f"Q 410 {ym} 410 {ym - 20} L 410 {y1 + 20} "
                f"Q 410 {y1} 390 {y1} L {x1} {y1}');"
            )
        else:
            bend_y = ym + (20 if y1 > ym else -20)
            path = (
                f"--recorrido:path('M {x0} {y0} L {x0} {ym + 20} "
                f"Q {x0} {ym} {x0 - 20} {ym} L {x1 + 20} {ym} "
                f"Q {x1} {ym} {x1} {bend_y} L {x1} {y1}');"
            )
        waiter_markup = (
            f'<g class="camarero sirviendo" transform="translate({x1},{y1})" '
            f'style="--x0:{x0}px;--y0:{y0}px;--xm:{xm}px;--ym:{ym}px;'
            f'--x1:{x1}px;--y1:{y1}px;'
            f'{path}'
            f'animation-delay:{-serve_elapsed:.2f}s">'
            f"{_person('#15110f', tray=True, collar=True)}</g>"
        )
        label += " El camarero te trae la comida desde el pase."
    elif customer_name and seated and playing is not None:
        x1, y1, _ = own_seats[0]
        dx, dy = waiter_offset
        waiter_markup = (
            f'<g class="camarero acompanando" transform="translate({x1},{y1})" '
            f'style="{_walk_style(own_seats[0], 0, playing)}">'
            f'<g class="posicion-camarero" transform="translate({waiter_x - x1},{waiter_y - y1})" '
            f'style="--dx0:{dx}px;--dy0:{dy}px;--dx1:{waiter_x - x1}px;--dy1:{waiter_y - y1}px">'
            f"{_person('#15110f', tray=True, collar=True)}</g></g>"
        )
    else:
        waiter_markup = (
            f'<g class="camarero" transform="translate({waiter_x},{waiter_y})">'
            f"{_person('#15110f', tray=True, collar=True)}</g>"
        )
    return (
        f'<svg class="planta {waiter}" viewBox="0 0 {PLAN_W} {PLAN_H}" role="img" '
        f'aria-label="{label}">'
        f"{_defs()}"
        f'<rect x="24" y="24" width="{PLAN_W - 48}" height="{PLAN_H - 48}" fill="url(#p-baldosa)"/>'
        f"{bar}{tables}{plates}{groups}{_cashier_station(bill_pending, cashier_active)}"
        f"{_walls()}{_pass_hatch()}{customer}"
        f"{waiter_markup}"
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
