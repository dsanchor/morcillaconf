"""Caja v1 end to end without Foundry: table, kitchen, bill, card payment, goodbye.

A second stack of ./scripts/test-e2e-seating.sh: the scripted waiter talks to
the seating MCP, to dsanchor's A2A kitchen app with a scripted chef and to the
real A2A cashier app, whose prices come from the versioned carta. The view's
HTTP client drives the BFF exactly as the browser does.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from frontend.http_client import HttpBffClient
from frontend.visit import VisitSession

from restaurant_contracts.application import COMMAND_ADAPTER, Action
from restaurant_contracts.cashier import Bill, PaymentMethod, Receipt

BFF_URL = os.environ.get("E2E_CASHIER_BFF_URL")
pytestmark = pytest.mark.skipif(not BFF_URL, reason="E2E_CASHIER_BFF_URL is not set")
GOODBYE = "Pago recibido, ¡muchas gracias por venir! Os acompaño a la puerta. ¡Hasta pronto!"


def _served(visit: VisitSession, orders: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while len(visit.snapshot.served_orders) < orders:
        if time.monotonic() > deadline:
            raise AssertionError("The waiter did not serve the cooked dishes")
        time.sleep(0.5)
        visit.refresh_service()


def test_the_served_dishes_are_billed_from_the_carta_paid_once_and_the_visit_closes() -> None:
    client = HttpBffClient.open(BFF_URL, "Irati", timeout=30)
    visit = VisitSession(client)
    visit.arrive()
    visit.send_message("Hola, somos dos")
    assert visit.allows(Action.DECIDE_TABLE)
    visit.decide_table("confirmed")
    assert visit.snapshot.seating.status == "seated"

    visit.send_message("La cuenta, por favor")
    assert visit.snapshot.messages[-1].text == "Todavía no hay platos de cocina servidos que cobrar."

    visit.send_message("Pido una morcilla a la brasa y unas croquetas de morcilla")
    kitchen = visit.snapshot.messages[-2].kitchen
    assert [item.carta_id for item in kitchen.result.accepted] == [
        "morcilla-de-burgos-a-la-brasa", "croquetas-de-morcilla",
    ]
    _served(visit, 1)

    visit.send_message("La cuenta, por favor")
    *_, request, cashier, reply = visit.snapshot.messages
    assert (request.role, cashier.role, reply.role) == ("user", "cashier", "assistant")
    assert cashier.command_event_id == request.command_event_id == reply.command_event_id
    bill = cashier.cashier.result
    assert isinstance(bill, Bill)
    assert [(line.carta_id, line.unit_price) for line in bill.lines] == [
        ("morcilla-de-burgos-a-la-brasa", Decimal("8.50")),
        ("croquetas-de-morcilla", Decimal("9.00")),
    ]
    assert bill.total == Decimal("17.50") and bill.currency == "EUR"
    assert cashier.cashier.task is not None
    assert visit.snapshot.pending_bill.bill_id == bill.bill_id
    labels = [step.label for step in request.activity]
    assert "Caja A2A: envía la cuenta" in labels and "Caja: consulta precios en la carta" in labels

    visit.send_message("Pago con tarjeta")
    assert visit.snapshot.pending_bill.bill_id == bill.bill_id

    visit.decide_payment(PaymentMethod.CARD)

    *_, paid, goodbye = visit.snapshot.messages
    receipt = paid.cashier.result
    assert isinstance(receipt, Receipt)
    assert (receipt.bill_id, receipt.method, receipt.amount) == (bill.bill_id, PaymentMethod.CARD, Decimal("17.50"))
    assert goodbye.text == GOODBYE
    assert visit.snapshot.visit_closed and visit.snapshot.seating.status == "none"
    assert visit.snapshot.allowed_actions == [Action.ARRIVE]

    # A second click, with another event id, is completed without a second charge.
    count = len(visit.snapshot.messages)
    again = COMMAND_ADAPTER.validate_python({
        "schema_version": 1, "event_id": "cmd_second_click", "occurred_at": datetime.now(UTC),
        "conversation_id": visit.snapshot.conversation_id, "event_type": "payment.confirmation_decided",
        "payload": {"bill_id": bill.bill_id, "version": 1, "method": "efectivo"},
    })
    result = asyncio.run(client.submit(again))
    assert result.status == "completed"
    snapshot = asyncio.run(client.get_snapshot(visit.snapshot.conversation_id))
    assert len(snapshot.messages) == count
