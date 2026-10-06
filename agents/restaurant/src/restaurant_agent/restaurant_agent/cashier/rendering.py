"""The cashier's answer as plain Spanish text, for the conversation and the waiter."""

from __future__ import annotations

from restaurant_contracts.cashier import (
    PAYMENT_LABELS,
    Bill,
    BillLine,
    BillSource,
    BillStage,
    CashierFailure,
    Receipt,
    euros,
)
from restaurant_contracts.kitchen import RENDERED_TEXT_LIMIT

GOODBYE = "Pago recibido, ¡muchas gracias por venir! Os acompaño a la puerta. ¡Hasta pronto!"
PAY_AGAIN = (
    "Caja no ha podido cobrar ahora mismo. Volved a pulsar «Tarjeta» o «Efectivo» "
    "en un momento."
)
BILL_CLOSED = "Esa cuenta ya no está abierta en caja. Pedidme la cuenta otra vez."


def render_text(result: Bill | Receipt | CashierFailure, *, paying: bool = False) -> str:
    if isinstance(result, CashierFailure):
        action = "cobrar" if paying else "preparar la cuenta"
        return f"Caja no ha podido {action}. {result.message}"
    if isinstance(result, Receipt):
        return receipt_text(result)
    lines = ["Cuenta"]
    lines.extend(_line(line) for line in result.lines)
    lines.append(f"Total: {euros(result.total)}")
    lines.append(result.note)
    if result.stage is BillStage.AWAITING_REVIEW:
        lines.append("Pendiente de revisión en caja antes de cobrar.")
    else:
        options = " o ".join(PAYMENT_LABELS[option].lower() for option in result.payment_options)
        lines.append(f"Pago: {options}.")
    lines.append("Fuentes: " + "; ".join(_source(source) for source in result.sources) + ".")
    text = "\n".join(lines)
    return text if len(text) <= RENDERED_TEXT_LIMIT else f"{text[: RENDERED_TEXT_LIMIT - 1]}…"


def receipt_text(receipt: Receipt) -> str:
    method = PAYMENT_LABELS[receipt.method].lower()
    when = receipt.paid_at.strftime("%d/%m/%Y %H:%M UTC")
    return (
        f"Pagado con {method}: {euros(receipt.amount)}.\n"
        f"Referencia {receipt.reference} · {when}"
    )


def _line(line: BillLine) -> str:
    return (
        f"- {line.quantity} × {line.name}: {euros(line.unit_price)} "
        f"= {euros(line.line_total)}"
    )


def _source(source: BillSource) -> str:
    details = [f"versión {source.version}" if source.version else "", source.detail or ""]
    described = ", ".join(detail for detail in details if detail)
    return f"{source.document} ({described})" if described else source.document
