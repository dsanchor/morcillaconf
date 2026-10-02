"""Top-down kitchen scene aligned with the dining-room floor plan."""

from __future__ import annotations

from html import escape

from restaurant_contracts.kitchen import KitchenPlan

KITCHEN_W, KITCHEN_H = 500, 340
STATIONS = (
    ("Parrilla", ("brasa",), 130),
    ("Fritos", ("fritos",), 250),
    ("General", ("pinchos_frios",), 370),
)


def _defs() -> str:
    return (
        '<defs><pattern id="k-baldosa" width="34" height="34" patternUnits="userSpaceOnUse">'
        '<rect width="34" height="34" fill="#39221a"/>'
        '<path d="M0,0 H34 M0,0 V34" stroke="#4b2d21" stroke-width="2"/>'
        '</pattern><radialGradient id="k-halo">'
        '<stop offset="0" stop-color="#f2b04a" stop-opacity=".34"/>'
        '<stop offset="1" stop-color="#f2b04a" stop-opacity="0"/>'
        '</radialGradient></defs>'
    )


def _agent(name: str, x: int, y: int, *, chef: bool = False, busy: bool = False) -> str:
    body = "#8c1c2b" if chef else "#15110f"
    toque = (
        '<g class="toque" transform="translate(0,-14)">'
        '<circle cx="-7" r="7"/><circle cx="7" r="7"/><circle cy="-5" r="8"/>'
        '<rect x="-13" y="1" width="26" height="8" rx="2"/></g>'
        if chef
        else '<path d="M-8,-8 L0,2 L8,-8 Z" fill="#f1e8d6"/>'
    )
    return (
        f'<g class="agente-cocina {"chef" if chef else "especialista"}{" con-platos" if busy else ""}" '
        f'transform="translate({x},{y})">'
        '<circle class="halo-agente" r="32" fill="url(#k-halo)"/>'
        f'<ellipse rx="21" ry="13" fill="{body}" stroke="#6b5a4a" stroke-width="1.5"/>'
        f'{toque}<circle r="10" fill="#ead7b3"/>'
        # Specialists are named by their station's label; only the chef carries a name.
        + (f'<text y="-30" text-anchor="middle">{escape(name)}</text>' if chef else "")
        + '<circle class="estado-agente" cx="23" cy="8" r="5"/>'
        '</g>'
    )


def _station(
    label: str,
    station_ids: tuple[str, ...],
    x: int,
    plan: KitchenPlan | None,
) -> str:
    tasks = [] if plan is None else [
        task
        for station in plan.stations
        if station.station.value in station_ids
        for task in station.tasks
    ]
    estimates = {} if plan is None else {
        item.line: item.estimated_ready_seconds for item in plan.accepted
    }
    details = []
    for index, task in enumerate(tasks[:2]):
        shown = task.name if len(task.name) <= 17 else f"{task.name[:16]}…"
        details.append(
            f'<text class="plato-cocina" x="{x}" y="{278 + index * 15}" '
            f'text-anchor="middle">{escape(shown)} · {estimates[task.line]} s</text>'
        )
    if len(tasks) > 2:
        details.append(
            f'<text class="plato-cocina" x="{x}" y="308" text-anchor="middle">'
            f'+{len(tasks) - 2} más</text>'
        )
    return (
        f'<g class="partida"><rect x="{x - 51}" y="172" width="102" height="138" '
        'rx="6" fill="#6b4a2f" stroke="#9a7550" stroke-width="2"/>'
        f'<rect x="{x - 41}" y="182" width="82" height="68" rx="4" '
        'fill="#4a3322" stroke="#81603f" stroke-width="1.5"/>'
        f'<text class="nombre-partida" x="{x}" y="164" text-anchor="middle">{label}</text>'
        f'{_agent(label, x, 217, busy=bool(tasks))}{"".join(details)}</g>'
    )


def kitchen_plan_svg(active: bool, plan: KitchenPlan | None, served: bool = False) -> str:
    """Kitchen agents and their cooked dishes in the restaurant's visual language."""

    if active:
        state, status = "activa", "EN PREPARACIÓN"
    elif plan is None:
        state, status = "espera", "EN ESPERA"
    elif served:
        state, status = "servida", "SERVIDO"
    else:
        state, status = "lista", "EN EL PASE"
    # Served dishes have left the kitchen: the stations are clear again.
    shown = None if served else plan
    stations = "".join(
        _station(label, station_ids, x, shown)
        for label, station_ids, x in STATIONS
    )
    return (
        f'<svg class="planta cocina-plano {state}" viewBox="0 0 {KITCHEN_W} {KITCHEN_H}" '
        f'role="img" aria-label="Cocina: {status.lower()}">'
        f'{_defs()}<rect x="24" y="24" width="452" height="292" fill="url(#k-baldosa)"/>'
        '<rect x="72" y="112" width="356" height="38" rx="6" fill="#4a3322" '
        'stroke="#8a6644" stroke-width="2"/>'
        '<path d="M82,121 H418" stroke="#b08457" stroke-width="3"/>'
        '<path d="M24,316 V288 M24,228 V24 H476 V316" fill="none" stroke="#cdba95" '
        'stroke-width="10" stroke-linecap="square"/>'
        '<rect x="17" y="228" width="14" height="60" rx="2" fill="#4a3322" '
        'stroke="#b08457" stroke-width="2"/>'
        '<text class="titulo-cocina" x="42" y="54">Cocina</text>'
        '<rect class="marco-estado" x="350" y="35" width="118" height="27" rx="13.5"/>'
        f'<text class="estado-cocina" x="458" y="54" text-anchor="end">{status}</text>'
        f'{_agent("Chef", 250, 87, chef=True)}{stations}</svg>'
    )