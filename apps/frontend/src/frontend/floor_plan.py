"""Top-down plan of the dining room: empty tables, the bar, the customer and the waiter.

Standard library only. The waiter's position is a CSS state of the ``planta``
wrapper: ``barra`` (resting), ``llegando`` (walks to the door once) and
``atendiendo`` (standing by the customer).
"""

from __future__ import annotations

from html import escape

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


def _round_table(cx: int, cy: int) -> str:
    chairs = (
        _chair(cx, cy - 54, False)
        + _chair(cx, cy + 54, False)
        + _chair(cx - 54, cy, True)
        + _chair(cx + 54, cy, True)
    )
    return (
        f"{chairs}<circle cx=\"{cx}\" cy=\"{cy}\" r=\"34\" {WOOD}/>"
        f'<circle cx="{cx}" cy="{cy}" r="25" fill="none" stroke="#81603f" stroke-width="1.5"/>'
    )


def _long_table() -> str:
    x, y, width, height = LONG_TABLE
    seats = (x + 34, x + 100, x + 166)
    chairs = "".join(
        _chair(seat, y - 24, False) + _chair(seat, y + height + 24, False) for seat in seats
    )
    return f'{chairs}<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="6" {WOOD}/>'


def _bar() -> str:
    counter = (
        '<path d="M600,24 H976 V196 H934 V66 H600 Z" fill="#4a3322" '
        'stroke="#8a6644" stroke-width="2"/>'
        '<path d="M600,66 H934 V196" fill="none" stroke="#b08457" stroke-width="3"/>'
    )
    stools = "".join(f'<circle cx="{x}" cy="{y}" r="11" {SEAT}/>' for x, y in STOOLS)
    return counter + stools


def _walls() -> str:
    gap_left, gap_right = DOOR_GAP
    return (
        f'<path d="M24,24 H976 V316 H{gap_right} M{gap_left},316 H24 Z" fill="none" '
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


def floor_plan_svg(customer_name: str | None, waiter: str = "barra") -> str:
    """Plan with the customer at the door and the waiter in the given state."""

    if waiter not in WAITER_STATES:
        raise ValueError(f"Unknown waiter state: {waiter}")
    waiter_x, waiter_y = WAITER_AT_DOOR if waiter == "atendiendo" else WAITER_AT_BAR
    customer = ""
    label = "Plano del comedor: mesas y barra vacías"
    if customer_name:
        shown = customer_name if len(customer_name) <= 16 else f"{customer_name[:15]}…"
        cx, cy = CUSTOMER
        customer = (
            f'<g class="cliente" transform="translate({cx},{cy})">'
            '<circle r="44" fill="url(#p-halo)"/>'
            f"{_person('#8c1c2b')}"
            f'<text y="-30" text-anchor="middle">{escape(shown)}</text>'
            "</g>"
        )
        label += f"; {escape(customer_name)} en la entrada"
    label += f"; {WAITER_LABELS[waiter]}."
    tables = "".join(_round_table(cx, cy) for cx, cy in ROUND_TABLES) + _long_table()
    return (
        f'<svg class="planta {waiter}" viewBox="0 0 {PLAN_W} {PLAN_H}" role="img" '
        f'aria-label="{label}">'
        f"{_defs()}"
        '<rect x="24" y="24" width="952" height="292" fill="url(#p-baldosa)"/>'
        f"{_bar()}{tables}{_walls()}{customer}"
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
