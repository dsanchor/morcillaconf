"""Barra v1 end to end without Foundry: drinks served from the bar, billed and paid.

The cashier stack of ./scripts/test-e2e-seating.sh: the scripted waiter talks
to the seating MCP, to dsanchor's A2A kitchen app with a scripted chef and to
the real A2A cashier app, priced from the versioned carta. Its bar runs the
real code (carta parser, name matcher and allergen rule) on that same carta
instead of the knowledge base. The view's HTTP client drives the BFF exactly
as the browser does.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import pytest

from frontend.http_client import HttpBffClient
from frontend.visit import VisitSession

from restaurant_contracts.application import Action
from restaurant_contracts.bar import BarRound
from restaurant_contracts.cashier import Bill, PaymentMethod, Receipt

BFF_URL = os.environ.get("E2E_CASHIER_BFF_URL")
pytestmark = pytest.mark.skipif(not BFF_URL, reason="E2E_CASHIER_BFF_URL is not set")
GOODBYE = "Pago recibido, ¡muchas gracias por venir! Os acompaño a la puerta. ¡Hasta pronto!"


def _seated(name: str) -> VisitSession:
    visit = VisitSession(HttpBffClient.open(BFF_URL, name, timeout=30))
    visit.arrive()
    visit.send_message("Hola, somos dos")
    assert visit.allows(Action.DECIDE_TABLE)
    visit.decide_table("confirmed")
    assert visit.snapshot.seating.status == "seated"
    return visit


def _turn(visit: VisitSession) -> list:
    """The messages of the latest customer message, in order."""

    last = next(message for message in reversed(visit.snapshot.messages) if message.role == "user")
    return [message for message in visit.snapshot.messages if message.command_event_id == last.command_event_id]


def _bill(visit: VisitSession) -> Bill:
    visit.send_message("La cuenta, por favor")
    cashier = next(message for message in _turn(visit) if message.role == "cashier")
    assert isinstance(cashier.cashier.result, Bill)
    return cashier.cashier.result


def test_drinks_are_served_from_the_carta_billed_and_paid() -> None:
    visit = _seated("Ainhoa")

    visit.send_message("Ponme dos cañas")

    request, bar, reply = _turn(visit)
    assert (request.role, bar.role, reply.role) == ("user", "bar", "assistant")
    served = bar.bar.result
    assert isinstance(served, BarRound)
    assert [(item.carta_id, item.name, item.quantity) for item in served.served] == [
        ("cana-de-cerveza", "Caña de cerveza", 2),
    ]
    assert served.round_id in visit.snapshot.served_orders
    labels = [step.label for step in request.activity]
    assert "Barra: sirve las bebidas" in labels and "Foundry IQ: bebidas de la carta" in labels

    for message, reason in (
        ("Ponme un agua", "En la carta hay varias: Agua con gas o Agua sin gas."),
        ("Ponme una caña, soy celíaco", "Contiene cereales con gluten según la carta y has indicado celiaquía."),
        ("Ponme una coca-cola", "No está en la carta."),
    ):
        visit.send_message(message)
        [rejected] = next(item for item in _turn(visit) if item.role == "bar").bar.result.rejected
        assert rejected.reason == reason, message

    bill = _bill(visit)
    assert [(line.carta_id, line.quantity, line.unit_price) for line in bill.lines] == [
        ("cana-de-cerveza", 2, Decimal("2.50")),
    ]
    assert bill.total == Decimal("5.00") and visit.snapshot.pending_bill.bill_id == bill.bill_id

    # Another round voids the presented bill; the new one includes it.
    visit.send_message("Ponme otra caña")
    assert visit.snapshot.pending_bill is None and not visit.allows(Action.DECIDE_PAYMENT)
    renewed = _bill(visit)
    assert renewed.total == Decimal("7.50") and renewed.bill_id != bill.bill_id

    visit.decide_payment(PaymentMethod.CARD)

    *_, paid, goodbye = visit.snapshot.messages
    receipt = paid.cashier.result
    assert isinstance(receipt, Receipt) and receipt.amount == Decimal("7.50")
    assert goodbye.text == GOODBYE and visit.snapshot.visit_closed


def test_a_mixed_order_goes_to_the_kitchen_and_the_bar_and_is_billed_once_served() -> None:
    visit = _seated("Unai")

    visit.send_message("Ponme una morcilla a la brasa y un agua con gas")

    roles = [message.role for message in _turn(visit)]
    assert roles == ["user", "kitchen", "bar", "assistant"]
    kitchen, bar = (message for message in _turn(visit) if message.role in ("kitchen", "bar"))
    assert [line.name for line in kitchen.kitchen.order.lines] == ["morcilla a la brasa"]
    assert [item.carta_id for item in bar.bar.result.served] == ["agua-con-gas"]
    deadline = time.monotonic() + 20
    while kitchen.kitchen.result.order_id not in visit.snapshot.served_orders:
        assert time.monotonic() < deadline, "The waiter did not serve the cooked dishes"
        time.sleep(0.5)
        visit.refresh_service()

    bill = _bill(visit)
    assert [(line.carta_id, line.line_total) for line in bill.lines] == [
        ("morcilla-de-burgos-a-la-brasa", Decimal("8.50")),
        ("agua-con-gas", Decimal("2.20")),
    ]
    assert bill.total == Decimal("10.70")
    # Leave the table free for the other end-to-end flows on the same MCP.
    assert visit.exit()
