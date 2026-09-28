"""Facade of the Burgos inn: a studded oak door under a round stone arch at night.

Only the standard library is used, so the markup can be rendered and previewed
without installing anything. The Streamlit view embeds these strings as-is and
drives the door with the CSS classes ``cerrada``, ``llama`` and ``abriendo``.
"""

from __future__ import annotations

import math
import random

VIEW_W, VIEW_H = 1600, 1000
DOOR_LEFT, DOOR_WIDTH = 650, 300
DOOR_RADIUS = DOOR_WIDTH // 2
DOOR_CX = DOOR_LEFT + DOOR_RADIUS
DOOR_RIGHT = DOOR_LEFT + DOOR_WIDTH
SPRING_Y = 555
ARCH_TOP = SPRING_Y - DOOR_RADIUS
SILL_Y = 860
STEP_FRONT_Y = SILL_Y + 24
STREET_Y = STEP_FRONT_Y + 20
ARCH_OUTER = 236
WALL_TOP = 262
LANTERN_X, LANTERN_Y = 1150, SPRING_Y - 64
MORTAR = 'stroke="#1a130d" stroke-opacity=".4" stroke-width="2.4"'

DOORWAY = (
    f"M{DOOR_LEFT},{SILL_Y} V{SPRING_Y} "
    f"A{DOOR_RADIUS},{DOOR_RADIUS} 0 0 1 {DOOR_RIGHT},{SPRING_Y} V{SILL_Y} Z"
)
LEFT_LEAF = (
    f"M{DOOR_LEFT},{SILL_Y} V{SPRING_Y} "
    f"A{DOOR_RADIUS},{DOOR_RADIUS} 0 0 1 {DOOR_CX},{ARCH_TOP} V{SILL_Y} Z"
)
RIGHT_LEAF = (
    f"M{DOOR_RIGHT},{SILL_Y} V{SPRING_Y} "
    f"A{DOOR_RADIUS},{DOOR_RADIUS} 0 0 0 {DOOR_CX},{ARCH_TOP} V{SILL_Y} Z"
)


def _defs() -> str:
    return f"""
<defs>
  <linearGradient id="m-cielo" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#070b18"/>
    <stop offset="1" stop-color="#1a2240"/>
  </linearGradient>
  <radialGradient id="m-muro" cx="{DOOR_CX + 110}" cy="{SPRING_Y + 30}" r="900"
      gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#d6c195"/>
    <stop offset=".3" stop-color="#9a8664"/>
    <stop offset=".62" stop-color="#4c4033"/>
    <stop offset="1" stop-color="#1b1612"/>
  </radialGradient>
  <radialGradient id="m-dovela" cx="{DOOR_CX + 60}" cy="{SPRING_Y}" r="420"
      gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#ead9b4"/>
    <stop offset="1" stop-color="#a8936f"/>
  </radialGradient>
  <radialGradient id="m-interior" cx="{DOOR_CX}" cy="{SILL_Y - 110}" r="430"
      gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#ffe6a6"/>
    <stop offset=".45" stop-color="#f1ad48"/>
    <stop offset="1" stop-color="#6b3714"/>
  </radialGradient>
  <linearGradient id="m-madera" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#6f4a2d"/>
    <stop offset="1" stop-color="#462b19"/>
  </linearGradient>
  <linearGradient id="m-lumbre" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#000" stop-opacity=".28"/>
    <stop offset="1" stop-color="#ffcf7a" stop-opacity=".12"/>
  </linearGradient>
  <radialGradient id="m-resplandor">
    <stop offset="0" stop-color="#ffd27a" stop-opacity=".62"/>
    <stop offset=".4" stop-color="#f2a93f" stop-opacity=".2"/>
    <stop offset="1" stop-color="#f2a93f" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="m-derrame" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffd27a" stop-opacity=".8"/>
    <stop offset="1" stop-color="#ffd27a" stop-opacity="0"/>
  </linearGradient>
  <radialGradient id="m-vineta" cx="{DOOR_CX + 60}" cy="{SPRING_Y + 40}" r="960"
      gradientUnits="userSpaceOnUse">
    <stop offset=".32" stop-color="#000" stop-opacity="0"/>
    <stop offset="1" stop-color="#000" stop-opacity=".8"/>
  </radialGradient>
  <clipPath id="m-clip-izq"><path d="{LEFT_LEAF}"/></clipPath>
  <clipPath id="m-clip-der"><path d="{RIGHT_LEAF}"/></clipPath>
</defs>"""


def _sky() -> str:
    rng = random.Random(11)
    stars = []
    for _ in range(26):
        x = rng.randint(20, VIEW_W - 20)
        y = rng.randint(18, WALL_TOP - 60)
        if 1080 < x < 1360 and y > 60:
            continue
        stars.append(
            f'<circle cx="{x}" cy="{y}" r="{rng.uniform(0.9, 2.1):.1f}" '
            f'fill="#efe6cf" fill-opacity="{rng.uniform(0.35, 0.85):.2f}"/>'
        )
    return (
        f'<rect width="{VIEW_W}" height="{WALL_TOP + 20}" fill="url(#m-cielo)"/>'
        f'<g class="estrellas">{"".join(stars)}</g>'
    )


def _spire(cx: int, tip_y: int) -> str:
    base_y = WALL_TOP - 96
    half = 32
    tower = f'<rect x="{cx - half}" y="{base_y}" width="{half * 2}" height="{WALL_TOP - base_y}"/>'
    spire = f'<path d="M{cx - half + 5},{base_y} L{cx},{tip_y} L{cx + half - 5},{base_y} Z"/>'
    pinnacles = "".join(
        f'<path d="M{x - 6},{base_y} L{x},{base_y - 26} L{x + 6},{base_y} Z"/>'
        for x in (cx - half + 4, cx + half - 4)
    )
    tracery = []
    for row in range(5):
        y = base_y - 20 - row * 22
        width = (half - 9) * (y - tip_y) / (base_y - tip_y)
        if width < 6:
            break
        tracery.append(
            f'<rect x="{cx - width / 2:.1f}" y="{y}" width="{width:.1f}" height="8" rx="3"/>'
        )
    window = f'<rect x="{cx - 8}" y="{base_y + 18}" width="16" height="30" rx="8"/>'
    return (
        f'<g fill="#1f2745">{tower}{spire}{pinnacles}</g>'
        f'<g fill="#11182e">{"".join(tracery)}{window}</g>'
    )


def _cathedral() -> str:
    nave = f'<rect x="1182" y="{WALL_TOP - 60}" width="78" height="60" fill="#1c2441"/>'
    rose = f'<circle cx="1221" cy="{WALL_TOP - 36}" r="12" fill="#11182e"/>'
    return f'<g class="catedral">{nave}{rose}{_spire(1150, 44)}{_spire(1292, 56)}</g>'


def _eave() -> str:
    bumps = "".join(
        f"M{x},{WALL_TOP - 16} a18,13 0 0 1 36,0 Z " for x in range(-18, VIEW_W + 18, 36)
    )
    return (
        f'<rect y="{WALL_TOP - 18}" width="{VIEW_W}" height="24" fill="#1d1510"/>'
        f'<path d="{bumps}" fill="#4f271b" stroke="#24120c" stroke-width="2"/>'
    )


def _wall() -> str:
    rng = random.Random(7)
    blocks = []
    course = 66
    for row, y in enumerate(range(WALL_TOP, STREET_Y, course)):
        x = -rng.randint(20, 140) if row % 2 else -rng.randint(90, 200)
        while x < VIEW_W:
            width = rng.randint(170, 250)
            shade = rng.uniform(0.0, 0.16)
            blocks.append(
                f'<rect x="{x}" y="{y}" width="{width}" height="{course}" '
                f'fill="#000" fill-opacity="{shade:.3f}"/>'
            )
            x += width
    return (
        f'<rect y="{WALL_TOP}" width="{VIEW_W}" height="{VIEW_H - WALL_TOP}" fill="url(#m-muro)"/>'
        f'<g {MORTAR}>{"".join(blocks)}</g>'
        f'<rect width="{VIEW_W}" height="{VIEW_H}" fill="url(#m-vineta)"/>'
    )


def _arch() -> str:
    count = 15
    stones = []
    for index in range(count):
        start = math.pi - index * math.pi / count
        end = math.pi - (index + 1) * math.pi / count

        def point(radius: float, angle: float) -> str:
            return (
                f"{DOOR_CX + radius * math.cos(angle):.1f},"
                f"{SPRING_Y - radius * math.sin(angle):.1f}"
            )

        stones.append(
            f'<path d="M{point(DOOR_RADIUS, start)} L{point(ARCH_OUTER, start)} '
            f"A{ARCH_OUTER},{ARCH_OUTER} 0 0 1 {point(ARCH_OUTER, end)} "
            f"L{point(DOOR_RADIUS, end)} "
            f'A{DOOR_RADIUS},{DOOR_RADIUS} 0 0 0 {point(DOOR_RADIUS, start)} Z"/>'
        )
    jambs = []
    height = (SILL_Y - SPRING_Y) / 4
    for index, width in enumerate((100, 68, 100, 68)):
        y = SPRING_Y + index * height
        jambs.append(
            f'<rect x="{DOOR_LEFT - width}" y="{y:.1f}" width="{width}" height="{height:.1f}"/>'
            f'<rect x="{DOOR_RIGHT}" y="{y:.1f}" width="{width}" height="{height:.1f}"/>'
        )
    return f'<g fill="url(#m-dovela)" {MORTAR}>{"".join(stones)}{"".join(jambs)}</g>'


def _ham(x: int, y: int, scale: float) -> str:
    return (
        f'<g transform="translate({x},{y}) scale({scale})">'
        '<path d="M0,-60 V-8" stroke="#3a1d10" stroke-width="3"/>'
        '<rect x="-5" y="-12" width="10" height="16" rx="3"/>'
        '<path d="M0,2 C20,10 34,46 30,86 C26,116 -12,122 -20,96 '
        'C-28,66 -14,24 0,2 Z"/>'
        '<path d="M-19,74 C-8,90 14,90 28,72" fill="none" stroke="#a0643a" '
        'stroke-opacity=".55" stroke-width="5" stroke-linecap="round"/>'
        "</g>"
    )


def _interior() -> str:
    floor = (
        f'<path d="M{DOOR_LEFT},{SILL_Y - 70} H{DOOR_RIGHT} V{SILL_Y} H{DOOR_LEFT} Z" '
        'fill="#7a4217" fill-opacity=".45"/>'
    )
    hams = _ham(752, SPRING_Y - 70, 0.9) + _ham(846, SPRING_Y - 44, 0.78)
    return (
        f'<path d="{DOORWAY}" fill="url(#m-interior)"/>'
        f"{floor}"
        f'<g fill="#3b1d10" fill-opacity=".82">{hams}</g>'
    )


def _leaf(side: str) -> str:
    is_left = side == "izq"
    shape = LEFT_LEAF if is_left else RIGHT_LEAF
    edge = DOOR_LEFT if is_left else DOOR_CX
    hinge = DOOR_LEFT if is_left else DOOR_RIGHT
    step = 1 if is_left else -1
    planks = " ".join(f"M{edge + offset},{ARCH_TOP - 10} V{SILL_Y}" for offset in (30, 60, 90, 120))
    grain = " ".join(f"M{edge + offset},{ARCH_TOP} V{SILL_Y}" for offset in (14, 47, 76, 103, 136))
    straps = []
    for y in (SPRING_Y + 58, SPRING_Y + 176, SPRING_Y + 270):
        start = hinge if is_left else hinge - 124
        tip = hinge + step * 124
        straps.append(
            f'<rect x="{start}" y="{y}" width="124" height="14" rx="3"/>'
            f'<circle cx="{tip}" cy="{y + 7}" r="9"/>'
        )
        straps.extend(
            f'<circle cx="{hinge + step * (16 + 24 * k)}" cy="{y + 7}" r="3.6" fill="#4d443e"/>'
            for k in range(5)
        )
    arched = (44, 78, 112) if is_left else (38, 72, 106)
    studs = "".join(
        f'<circle cx="{edge + x}" cy="{y}" r="3.3"/>'
        for y in (SPRING_Y + 118, SPRING_Y + 228)
        for x in (24, 58, 92, 126)
    ) + "".join(f'<circle cx="{edge + x}" cy="{SPRING_Y - 50}" r="3.3"/>' for x in arched)
    knocker = (
        ""
        if is_left
        else f'<circle cx="{DOOR_CX + 28}" cy="{SPRING_Y + 140}" r="7"/>'
        f'<circle cx="{DOOR_CX + 28}" cy="{SPRING_Y + 164}" r="17" fill="none" '
        'stroke="#1d1816" stroke-width="5"/>'
    )
    return (
        f'<g class="hoja hoja-{side}">'
        f'<path d="{shape}" fill="url(#m-madera)"/>'
        f'<g clip-path="url(#m-clip-{side})">'
        f'<path d="{planks}" stroke="#23150b" stroke-opacity=".6" stroke-width="3"/>'
        f'<path d="{grain}" stroke="#9b7049" stroke-opacity=".16" stroke-width="1.5"/>'
        f'<rect x="{edge}" y="{ARCH_TOP}" width="{DOOR_RADIUS}" height="{SILL_Y - ARCH_TOP}" '
        'fill="url(#m-lumbre)"/>'
        "</g>"
        f'<g fill="#1d1816">{"".join(straps)}</g>'
        f'<g fill="#39312c">{studs}</g>'
        f'<g fill="#1d1816">{knocker}</g>'
        f'<path d="{shape}" fill="none" stroke="#140b06" stroke-width="4"/>'
        "</g>"
    )


def _lantern() -> str:
    x, y = LANTERN_X, LANTERN_Y
    return (
        '<g class="farol">'
        f'<circle class="resplandor" cx="{x}" cy="{y + 60}" r="270" fill="url(#m-resplandor)"/>'
        f'<path d="M{x - 94},{y} H{x} V{y + 14}" fill="none" stroke="#1b1614" stroke-width="7" '
        'stroke-linecap="round"/>'
        f'<path d="M{x - 78},{y} C{x - 60},{y + 32} {x - 34},{y + 40} {x - 14},{y + 24}" '
        'fill="none" stroke="#1b1614" stroke-width="5" stroke-linecap="round"/>'
        f'<path d="M{x - 24},{y + 14} H{x + 24} L{x + 13},{y + 28} H{x - 13} Z" fill="#1b1614"/>'
        f'<rect class="llama-farol" x="{x - 16}" y="{y + 28}" width="32" height="58" fill="#ffd27a"/>'
        f'<path d="M{x - 16},{y + 28} V{y + 86} M{x},{y + 28} V{y + 86} M{x + 16},{y + 28} '
        f'V{y + 86}" stroke="#1b1614" stroke-width="3"/>'
        f'<path d="M{x - 20},{y + 86} H{x + 20} L{x + 10},{y + 98} H{x - 10} Z" fill="#1b1614"/>'
        "</g>"
    )


def _step() -> str:
    rng = random.Random(3)
    cobbles = []
    for row, y in enumerate(range(STREET_Y + 6, VIEW_H, 26)):
        x = -rng.randint(0, 30) - (20 if row % 2 else 0)
        while x < VIEW_W:
            width = rng.randint(34, 52)
            cobbles.append(
                f'<rect x="{x + 3}" y="{y}" width="{width - 6}" height="20" rx="8" '
                f'fill-opacity="{rng.uniform(0.35, 0.8):.2f}"/>'
            )
            x += width
    return (
        f'<rect y="{STREET_Y}" width="{VIEW_W}" height="{VIEW_H - STREET_Y}" fill="#120e0b"/>'
        f'<g fill="#2a221c">{"".join(cobbles)}</g>'
        f'<rect x="556" y="{SILL_Y}" width="488" height="{STEP_FRONT_Y - SILL_Y}" '
        f'fill="#bba680" {MORTAR}/>'
        f'<rect x="544" y="{STEP_FRONT_Y}" width="512" height="{STREET_Y - STEP_FRONT_Y}" '
        f'fill="#5f503d" {MORTAR}/>'
        f'<path class="derrame" d="M{DOOR_LEFT},{SILL_Y} H{DOOR_RIGHT} L1090,{VIEW_H} H510 Z" '
        'fill="url(#m-derrame)"/>'
    )


def facade_svg() -> str:
    """Complete facade; the leaves open through the wrapper's CSS classes."""

    return (
        f'<svg class="fachada" viewBox="0 0 {VIEW_W} {VIEW_H}" '
        'preserveAspectRatio="xMidYMax slice" aria-hidden="true" focusable="false">'
        f"{_defs()}"
        '<g class="mundo">'
        f"{_sky()}{_cathedral()}{_eave()}{_wall()}{_arch()}"
        f'{_interior()}<g class="hojas">{_leaf("izq")}{_leaf("der")}</g>'
        f"{_step()}{_lantern()}"
        "</g></svg>"
    )


FACADE_STATES = ("cerrada", "llama", "abriendo")


def facade_html(state: str = "cerrada") -> str:
    """Entrance scene; ``llama`` rattles the locked door, ``abriendo`` walks in."""

    if state not in FACADE_STATES:
        raise ValueError(f"Unknown facade state: {state}")
    return f'<div class="escena {state}">{facade_svg()}</div>'
