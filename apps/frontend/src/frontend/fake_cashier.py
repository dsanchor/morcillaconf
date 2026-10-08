"""Simulated cashier for the simulated waiter: fixed carta prices and no model.

It prices the kitchen dishes and the bar drinks already served when the
customer asks for the bill, and records the payment chosen with the buttons,
following the public contract, so the cashier's bubble can be seen and tested
without the agent.
Like the rest of the simulated restaurant, it never imports agents, Foundry,
A2A or MCP.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from restaurant_contracts.cashier import (
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    CashierReport,
    CashierTask,
    PaymentMethod,
    Receipt,
    ServedLine,
    euros,
)

_BILL = re.compile(r"\b(?:la cuenta|cobra(?:me|nos)?|qu[eé] (?:te|os) debo)\b", re.IGNORECASE)
# The simulated carta's prices, as the real carta writes them.
PRICES = {
    "morcilla-de-burgos-a-la-brasa": Decimal("8.50"),
    "croquetas-de-morcilla": Decimal("9.00"),
    "agua-con-gas": Decimal("2.20"),
    "agua-sin-gas": Decimal("1.80"),
    "cana-de-cerveza": Decimal("2.50"),
    "vino-tinto-ribera-del-duero": Decimal("3.50"),
    "mosto-de-uva": Decimal("2.50"),
}
GOODBYE = "Pago recibido, ¡muchas gracias por venir! Os acompaño a la puerta. ¡Hasta pronto!"
PAY_WITH_BUTTONS = "La cuenta ya está en la mesa: pagad con «Tarjeta» o «Efectivo»."
SERVE_FIRST = "Primero os llevo lo que espera en el pase y después os traigo la cuenta."
NOTHING_SERVED = "Todavía no hay nada servido que cobrar."


def asks_for_the_bill(message: str) -> bool:
    return _BILL.search(message) is not None


def fake_bill(bill_id: str, lines: list[ServedLine]) -> CashierReport:
    """The simulated cashier's bill for the served lines, waiting for card or cash."""

    request = BillRequest(bill_id=bill_id, lines=lines)
    priced = [
        BillLine(
            **line.model_dump(),
            unit_price=PRICES[line.carta_id],
            line_total=PRICES[line.carta_id] * line.quantity,
        )
        for line in lines
    ]
    bill = Bill(
        bill_id=bill_id,
        lines=priced,
        total=sum((line.line_total for line in priced), Decimal(0)),
        sources=[BillSource(document="carta de la casa", version="1", detail="simulada")],
    )
    text = "Cuenta (simulada)\n" + "\n".join(
        f"- {line.quantity} × {line.name}: {euros(line.line_total)}" for line in priced
    ) + f"\nTotal: {euros(bill.total)}"
    return CashierReport(
        bill_id=bill_id,
        request=request,
        result=bill,
        text=text,
        task=CashierTask(task_id=f"task_{bill_id}", context_id=f"ctx_{bill_id}"),
    )


def fake_receipt(
    report: CashierReport, method: PaymentMethod, key: str, reference: str, paid_at: datetime
) -> CashierReport:
    """The simulated payment: always approved, as the real one in v1."""

    bill = report.result
    assert isinstance(bill, Bill)
    receipt = Receipt(
        bill_id=bill.bill_id,
        version=bill.version,
        method=method,
        amount=bill.total,
        reference=reference,
        paid_at=paid_at,
        idempotency_key=key,
    )
    return CashierReport(
        bill_id=bill.bill_id,
        result=receipt,
        text=f"Pagado con {method.value}: {euros(receipt.amount)}. Referencia {reference}.",
        task=report.task,
    )
