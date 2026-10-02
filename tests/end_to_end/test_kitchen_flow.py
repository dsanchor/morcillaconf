"""The kitchen end to end without Foundry or the knowledge base.

The same stack as the seating end to end (./scripts/test-e2e-seating.sh): the
standalone waiter has no knowledge base, so the chef answers that it cannot
consult the carta. The view must show that answer as the kitchen's own message,
typed, between the customer's order and the waiter's reply.
"""

from __future__ import annotations

import os

import pytest

from frontend.http_client import HttpBffClient
from frontend.visit import VisitSession

from restaurant_contracts.kitchen import KitchenFailure, KitchenFailureCode

BFF_URL = os.environ.get("E2E_BFF_URL")
pytestmark = pytest.mark.skipif(not BFF_URL, reason="E2E_BFF_URL is not set")


def test_an_order_reaches_the_kitchen_and_its_answer_comes_before_the_waiter() -> None:
    visit = VisitSession(HttpBffClient.open(BFF_URL, "Koldo", timeout=30))
    visit.arrive()
    visit.send_message("Pido una morcilla a la brasa y un agua con gas")

    *_, order, kitchen, reply = visit.snapshot.messages
    assert (order.role, kitchen.role, reply.role) == ("user", "kitchen", "assistant")
    assert kitchen.command_event_id == order.command_event_id == reply.command_event_id
    report = kitchen.kitchen
    assert [line.name for line in report.order.lines] == ["morcilla a la brasa", "agua con gas"]
    assert isinstance(report.result, KitchenFailure)
    assert report.result.code is KitchenFailureCode.KITCHEN_NOT_CONFIGURED
    assert kitchen.text.startswith("Cocina no ha podido preparar el plan del pedido.")
    assert reply.text == "Cocina no ha podido revisar el pedido ahora mismo."

    visit.send_message("Gracias")
    assert [message.role for message in visit.snapshot.messages[-2:]] == ["user", "assistant"]
