"""Knowledge base results as ``summarize_retrieval`` writes them, for the tests."""

from __future__ import annotations

from decimal import Decimal

from restaurant_contracts.cashier import BillRequest, ServedLine

CARTA_LABEL = "documento de la casa: carta.md, tipo carta, versión 1"
RECIPES_LABEL = "documento de la casa: recetario.pdf, tipo recetario, versión 1"
WEB_LABEL = "fuente externa (web): Precios de bares de Burgos — https://example.com/precios"

MORCILLA = """### morcilla-de-burgos-a-la-brasa · Morcilla de Burgos a la brasa

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: brasa
- Precio: 8,50 € la ración de cuatro rodajas
- Descripción: morcilla de Burgos de arroz y cebolla, hecha en la casa y
  marcada a la brasa de encina hasta que la piel cruje.
- Contiene: ninguno de los 14.
"""
CROQUETAS = """### croquetas-de-morcilla · Croquetas de morcilla

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: fritos
- Precio: 9,00 € la ración de ocho croquetas
- Contiene: cereales con gluten (harina y pan rallado de trigo), leche, huevos.
"""


def block(ref: str, label: str, content: str) -> str:
    return f"[{ref}] Origen: {label}\n{content.strip()}"


def retrieval(*blocks: str) -> str:
    return "\n\n".join(blocks)


CARTA = retrieval(block("1", CARTA_LABEL, MORCILLA), block("2", CARTA_LABEL, CROQUETAS))
# The bar's drinks, in the carta's own «Barra: bebidas» section.
DRINKS = """## Barra: bebidas

Las bebidas se sirven en la barra y no pasan por ninguna partida de cocina.

### agua-con-gas · Agua con gas

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 2,20 € la botella de 50 cl
- Contiene: ninguno de los 14.

### cana-de-cerveza · Caña de cerveza

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 2,50 € la caña de 20 cl
- Contiene: cereales con gluten (cebada).

## Notas de la casa
"""


def served(carta_id: str, name: str, *, line: int = 1, order_id: str = "ko_1", quantity: int = 1) -> ServedLine:
    return ServedLine(order_id=order_id, line=line, carta_id=carta_id, name=name, quantity=quantity)


def request(*lines: ServedLine, bill_id: str = "bill_1") -> BillRequest:
    return BillRequest(bill_id=bill_id, lines=list(lines))


SERVED = request(
    served("morcilla-de-burgos-a-la-brasa", "Morcilla de Burgos a la brasa"),
    served("croquetas-de-morcilla", "Croquetas de morcilla", line=2),
)
TOTAL = Decimal("17.50")
