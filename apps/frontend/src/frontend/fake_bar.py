"""Simulated bar for the simulated waiter: a tiny fixed carta of drinks and no model.

The drinks of a message that starts with «Pido…» or «Ponme…» are served from
the bar with a round that follows the public contract, so the bar's bubble can
be seen and tested without the agent. «agua» alone is ambiguous, a celiac
gets no caña and anything else is not on the carta. Like the rest of the
simulated restaurant, it is presented as such and never imports agents,
Foundry or MCP.
"""

from __future__ import annotations

import re
import unicodedata

from restaurant_contracts.bar import (
    BarItem,
    BarReport,
    BarRequest,
    BarRound,
    RejectedDrink,
    ServedDrink,
)
from restaurant_contracts.kitchen import KitchenSource

_ORDER = re.compile(r"^\s*(?:pido|pedimos|ponme|ponnos)\s+(?P<items>.+)$", re.IGNORECASE | re.DOTALL)
_CELIAC = re.compile(r",?\s*(?:y\s+)?(?:soy|somos)\s+cel[ií]ac[oa]s?\b", re.IGNORECASE)
_SPLIT = re.compile(r",\s*|\s+y\s+")
_DRINK = re.compile(r"\b(?:agua|cana|cerveza|vino|tinto|mosto|refresco|coca|zumo)")
_QUANTITIES = {
    "un": 1, "una": 1, "uno": 1, "unos": 1, "unas": 1, "otra": 1, "otro": 1,
    "dos": 2, "tres": 3, "cuatro": 4,
}
GLUTEN = "cereales con gluten"
WATERS = ["Agua con gas", "Agua sin gas"]
# keyword, carta id, name, declared allergens
_CARTA = (
    ("agua con gas", "agua-con-gas", "Agua con gas", []),
    ("agua sin gas", "agua-sin-gas", "Agua sin gas", []),
    ("cana", "cana-de-cerveza", "Caña de cerveza", [GLUTEN]),
    ("cerveza", "cana-de-cerveza", "Caña de cerveza", [GLUTEN]),
    ("vino", "vino-tinto-ribera-del-duero", "Copa de vino tinto Ribera del Duero", ["sulfitos"]),
    ("tinto", "vino-tinto-ribera-del-duero", "Copa de vino tinto Ribera del Duero", ["sulfitos"]),
    ("mosto", "mosto-de-uva", "Mosto de uva", ["sulfitos"]),
)


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def is_drink(piece: str) -> bool:
    """Whether a piece of the order goes to the bar instead of the kitchen."""

    return _DRINK.search(_plain(piece)) is not None


def _item(number: int, piece: str) -> BarItem:
    words = piece.split()
    quantity = _QUANTITIES.get(words[0].casefold(), 1) if words else 1
    name = " ".join(words[1:]) if words and words[0].casefold() in _QUANTITIES else piece
    return BarItem(line=number, name=(name or piece)[:200], quantity=quantity)


def fake_bar_report(message: str, round_id: str) -> BarReport | None:
    """The simulated bar's round for the drinks of an order, or None when there are none."""

    match = _ORDER.match(message)
    if match is None:
        return None
    items = match["items"]
    restrictions = ["celiaquía"] if _CELIAC.search(items) else []
    pieces = [piece.strip(" .;¡!¿?") for piece in _SPLIT.split(_CELIAC.sub("", items))]
    drinks = [piece for piece in pieces if piece and is_drink(piece)][:20]
    if not drinks:
        return None
    request = BarRequest(
        round_id=round_id,
        items=[_item(number, piece) for number, piece in enumerate(drinks, 1)],
        restrictions=restrictions,
    )
    served: list[ServedDrink] = []
    rejected: list[RejectedDrink] = []
    for item in request.items:
        plain = _plain(item.name)
        drink = next((drink for drink in _CARTA if drink[0] in plain), None)

        def reject(reason: str, **fields: object) -> None:
            rejected.append(
                RejectedDrink(line=item.line, requested=item.name, quantity=item.quantity, reason=reason, **fields)
            )

        if "agua" in plain and "gas" not in plain:
            reject("En la carta hay varias: Agua con gas o Agua sin gas.", options=WATERS)
        elif drink is None:
            reject("No está en la carta.")
        elif restrictions and GLUTEN in drink[3]:
            reject(f"Contiene {GLUTEN} según la carta y has indicado celiaquía.", carta_id=drink[1])
        else:
            served.append(ServedDrink(line=item.line, carta_id=drink[1], name=drink[2], quantity=item.quantity))
    result = BarRound(
        round_id=round_id,
        served=served,
        rejected=rejected,
        sources=[KitchenSource(document="carta de la casa", version="1")],
    )
    text = "Barra (simulada)\n" + "\n".join(
        [f"- Servido: {item.quantity} × {item.name}" for item in served]
        + [f"- No servido: {item.quantity} × {item.requested}: {item.reason}" for item in rejected]
    )
    return BarReport(request=request, result=result, text=text)


def bar_reply(report: BarReport) -> str:
    """The simulated waiter's summary of the round, without changing it."""

    result = report.result
    parts = []
    if isinstance(result, BarRound) and result.served:
        parts.append("De la barra: " + ", ".join(f"{item.quantity} × {item.name}" for item in result.served) + ".")
    if isinstance(result, BarRound) and result.rejected:
        parts.append(
            "No he servido "
            + "; ".join(f"{item.requested} ({item.reason.rstrip('.')})" for item in result.rejected)
            + "."
        )
    return " ".join(parts) or "La barra no ha podido servir las bebidas."
