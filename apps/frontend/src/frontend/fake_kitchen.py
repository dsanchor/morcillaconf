"""Simulated chef for the simulated waiter: a tiny fixed carta and no model.

It answers messages that start with «Pido…» or «Ponme…» with a kitchen report
that follows the public contract, so the chef's bubble can be seen and tested
without the agent. Like the rest of the simulated restaurant, it is presented
as such and never imports agents, Foundry or MCP.
"""

from __future__ import annotations

import re
import unicodedata

from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenSource,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)

_ORDER = re.compile(r"^\s*(?:pido|pedimos|ponme|ponnos)\s+(?P<items>.+)$", re.IGNORECASE | re.DOTALL)
_CELIAC = re.compile(r",?\s*(?:y\s+)?(?:soy|somos)\s+cel[ií]ac[oa]s?\b", re.IGNORECASE)
_SPLIT = re.compile(r",\s*|\s+y\s+")
_WITHOUT = re.compile(r"\s+(?=sin\s)", re.IGNORECASE)
_QUANTITIES = {"un": 1, "una": 1, "uno": 1, "unos": 1, "unas": 1, "dos": 2, "tres": 3, "cuatro": 4}
GLUTEN = "cereales con gluten"
# keyword, carta id, name, station, declared allergens
_CARTA = (
    ("croqueta", "croquetas-de-morcilla", "Croquetas de morcilla", KitchenStation.FRITOS, [GLUTEN, "leche", "huevos"]),
    ("morcilla", "morcilla-de-burgos-a-la-brasa", "Morcilla de Burgos a la brasa", KitchenStation.BRASA, []),
    ("agua", "agua-con-gas", "Agua con gas", KitchenStation.BARRA, []),
    ("cana", "cana-de-cerveza", "Caña de cerveza", KitchenStation.BARRA, [GLUTEN]),
)


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _line(number: int, piece: str) -> KitchenOrderLine:
    name, *modifications = _WITHOUT.split(piece)
    words = name.split()
    quantity = _QUANTITIES.get(words[0].casefold(), 1) if words else 1
    if words and words[0].casefold() in _QUANTITIES:
        name = " ".join(words[1:]) or name
    return KitchenOrderLine(
        line=number,
        name=name[:200],
        quantity=quantity,
        modifications=[modification[:300] for modification in modifications[:10]],
    )


def fake_kitchen_report(message: str, order_id: str) -> KitchenReport | None:
    """The simulated chef's answer to an order, or None when the message is not one."""

    match = _ORDER.match(message)
    if match is None:
        return None
    items = match["items"]
    restrictions = ["celiaquía"] if _CELIAC.search(items) else []
    pieces = [piece.strip(" .;¡!¿?") for piece in _SPLIT.split(_CELIAC.sub("", items))]
    lines = [_line(number, piece) for number, piece in enumerate((p for p in pieces if p), 1)][:20]
    if not lines:
        return None
    order = KitchenOrder(order_id=order_id, lines=lines, restrictions=restrictions)
    accepted: list[AcceptedItem] = []
    rejected: list[RejectedItem] = []
    for line in lines:
        dish = next((dish for dish in _CARTA if dish[0] in _plain(line.name)), None)
        requested = ", ".join([line.name, *line.modifications])[:300]
        if dish is None:
            rejected.append(RejectedItem(line=line.line, requested=requested, quantity=line.quantity, reason="No está en la carta."))
        elif restrictions and GLUTEN in dish[4]:
            rejected.append(
                RejectedItem(
                    line=line.line, requested=requested, quantity=line.quantity, carta_id=dish[1],
                    reason="Contiene cereales con gluten y has indicado celiaquía.",
                )
            )
        else:
            accepted.append(
                AcceptedItem(
                    line=line.line, carta_id=dish[1], name=dish[2], quantity=line.quantity,
                    station=dish[3], adaptations=line.modifications, allergens=dish[4],
                )
            )
    stations = [
        StationPlan(
            station=station,
            tasks=[
                StationTask(line=item.line, carta_id=item.carta_id, name=item.name, quantity=item.quantity)
                for item in accepted
                if item.station is station
            ],
        )
        for station in KitchenStation
        if any(item.station is station for item in accepted)
    ]
    plan = KitchenPlan(
        order_id=order_id,
        accepted=accepted,
        rejected=rejected,
        warnings=["La casa no ofrece platos certificados sin gluten."] if restrictions else [],
        stations=stations,
        sources=[KitchenSource(document="carta de la casa", version="1")],
    )
    text = "Plan de cocina (simulado)\n" + "\n".join(
        [f"- Aceptado: {item.quantity} × {item.name}" for item in accepted]
        + [f"- Rechazado: {item.quantity} × {item.requested}: {item.reason}" for item in rejected]
    )
    return KitchenReport(order=order, result=plan, text=text)


def kitchen_reply(report: KitchenReport) -> str:
    """The simulated waiter's summary of the chef's verdict, without changing it."""

    plan = report.result
    if not isinstance(plan, KitchenPlan):
        return "Cocina no ha podido revisar el pedido ahora mismo."
    parts = []
    if plan.accepted:
        parts.append("Cocina acepta " + ", ".join(f"{item.quantity} × {item.name}" for item in plan.accepted) + ".")
    if plan.rejected:
        parts.append(
            "No puede preparar "
            + "; ".join(f"{item.requested} ({item.reason.rstrip('.')})" for item in plan.rejected)
            + "."
        )
    return " ".join(parts)
