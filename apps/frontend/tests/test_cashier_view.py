"""Caja v1 in the view: the cashier's bubble, its buttons, the till on the plan and the farewell."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

from restaurant_contracts.application import COMMAND_ADAPTER, Action, ActorContext, ChatMessage
from restaurant_contracts.cashier import (
    BillView,
    CashierFailureCode,
    CashierReport,
    PaymentMethod,
    ServedLine,
    cashier_failure,
)

from frontend.fake_cashier import GOODBYE, NOTHING_SERVED, PAY_WITH_BUTTONS, SERVE_FIRST, fake_bill, fake_receipt
from frontend.fake_client import FakeRestaurant
from frontend.floor_plan import CASHIER, DOOR_GAP, floor_plan_svg
from frontend.markup import (
    CASHIER_LABEL,
    COMPONENT_LABELS,
    activity_markup,
    bill_card_markup,
    cashier_row,
    conversation_markup,
)
from frontend.stylesheets import base_stylesheet
from frontend.visit import ConversationView, VisitSession

AT = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
LINES = [
    ServedLine(order_id="ko_1", line=1, carta_id="morcilla-de-burgos-a-la-brasa",
               name="Morcilla de Burgos a la brasa", quantity=1),
    ServedLine(order_id="ko_1", line=2, carta_id="croquetas-de-morcilla", name="Croquetas de morcilla", quantity=2),
]
BILL = fake_bill("bill_1", LINES)
RECEIPT = fake_receipt(BILL, PaymentMethod.CARD, "evt_1", "pay_1", AT)


def _message(report: CashierReport, message_id: str = "m_c") -> ChatMessage:
    return ChatMessage(
        message_id=message_id, role="cashier", text=report.text, occurred_at=AT,
        command_event_id="cmd_1", cashier=report,
    )


def _parse(markup: str) -> ET.Element:
    return ET.fromstring(markup.replace("&nbsp;", " "))


def _text(element: ET.Element) -> str:
    return "".join(element.itertext())


# The bubble


def test_the_bill_reads_like_a_ticket_with_exact_euros() -> None:
    root = _parse(cashier_row(_message(BILL), state="pending"))

    assert root.get("class") == "msg caja pending"
    bubble = root.find("div[@class='burbuja cuenta-caja']")
    assert (bubble.get("role"), bubble.get("aria-label")) == ("group", CASHIER_LABEL)
    lines = [_text(item) for item in bubble.iter("li")]
    assert lines == [
        "1 × Morcilla de Burgos a la brasa8,50 €",
        "2 × Croquetas de morcilla9,00 € cada una18,00 €",
    ]
    text = _text(bubble)
    assert "Total26,50 €" in text
    assert "Solo platos de cocina; las bebidas todavía no se cobran." in text
    assert "Pendiente de pago." in text
    assert text.endswith("Fuentes: carta de la casa (versión 1)")


@pytest.mark.parametrize(("state", "said"), [("paid", "Pagada."), ("void", "Anulada: pedid la cuenta otra vez.")])
def test_a_bill_no_longer_pending_says_what_became_of_it(state, said) -> None:
    root = _parse(cashier_row(_message(BILL), state=state))
    assert root.get("class") == f"msg caja {state}"
    assert said in _text(root)
    assert "Pendiente de pago." not in _text(root)


def test_the_receipt_and_the_failures_speak_in_the_same_bubble() -> None:
    receipt = _text(_parse(cashier_row(_message(RECEIPT))))
    assert receipt.startswith("Pagado con tarjetaTotal26,50 €")
    assert "Referencia pay_1 · 06/10/2026 21:30 UTC" in receipt

    priced = CashierReport(
        bill_id="bill_1", request=BILL.request,
        result=cashier_failure("bill_1", CashierFailureCode.PRICE_MISSING), text="Caja no ha podido.",
    )
    assert _text(_parse(cashier_row(_message(priced)))).startswith(
        "Caja no ha podido preparar la cuenta.Caja no encuentra en la carta"
    )
    paying = CashierReport(
        bill_id="bill_1", result=cashier_failure("bill_1", CashierFailureCode.TIMEOUT), text="Caja no ha podido.",
    )
    assert _text(_parse(cashier_row(_message(paying)))).startswith("Caja no ha podido cobrar.")


def test_the_till_is_the_cashiers_icon_not_the_waiter_or_the_toque() -> None:
    icon = _parse(cashier_row(_message(BILL))).find("svg")
    assert icon.get("class") == "icono" and icon.get("aria-hidden") == "true"
    assert len(icon.findall("rect")) == 3
    assert not icon.findall("ellipse")


def test_the_conversation_places_the_bill_between_the_request_and_the_reply() -> None:
    messages = (
        ChatMessage(message_id="m1", role="user", text="La cuenta, por favor", occurred_at=AT, command_event_id="c"),
        _message(BILL, "m2"),
        ChatMessage(message_id="m3", role="assistant", text="Aquí tenéis.", occurred_at=AT, command_event_id="c"),
        _message(RECEIPT, "m4"),
    )
    pending = conversation_markup(ConversationView(messages=messages[:3], pending_bill="bill_1"))
    assert pending.index("La cuenta, por favor") < pending.index("msg caja pending") < pending.index("Aquí tenéis.")
    paid = conversation_markup(ConversationView(messages=messages))
    assert "msg caja paid" in paid and "Pagado con tarjeta" in paid
    voided = conversation_markup(ConversationView(messages=messages[:3]))
    assert "msg caja void" in voided


def test_the_cashier_speaks_white_on_pine_green() -> None:
    css = base_stylesheet()
    pino = re.search(r"--pino: (#[0-9a-f]{6});", css).group(1)
    bubble = re.search(r"\.msg\.caja \.burbuja \{([^}]*)\}", css).group(1)
    assert "background: var(--pino)" in bubble and "color: #ffffff" in bubble

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    # Readable when projected, like the chef's azulejo.
    assert 1.05 / (luminance(pino) + 0.05) >= 7


def test_the_pending_bill_card_and_the_activity_name_the_cashier() -> None:
    card = _text(_parse(bill_card_markup(BillView.of(BILL.result))))
    assert card == "Total 26,50 €Elige cómo pagar."
    assert COMPONENT_LABELS["caja"] == "Caja"
    paid = ChatMessage.model_validate({
        **_message(RECEIPT).model_dump(),
        "activity": [{"step_id": "c1", "component": "caja", "label": "Caja: cobra con tarjeta", "duration_ms": 40}],
    })
    panel = _text(_parse(activity_markup([paid])))
    assert panel == "Cobro en cajaCaja40 msCaja: cobra con tarjeta"


# The till on the plan


def test_the_cashier_waits_by_the_exit_door_and_lights_up_with_a_bill() -> None:
    quiet = _parse(floor_plan_svg("Ana", "atendiendo"))
    till = next(element for element in quiet.iter() if element.get("class") == "caja")
    figure = next(element for element in till.iter() if element.get("class") == "cajero")
    x, y = CASHIER
    assert figure.get("transform") == f"translate({x},{y}) rotate(90)"
    # Inside the room, left of the door and above it: the bottom-left corner.
    assert 24 < x < DOOR_GAP[0] and 150 < y < 316
    assert "la caja, junto a la puerta." in quiet.get("aria-label")

    lit = _parse(floor_plan_svg("Ana", "atendiendo", bill_pending=True))
    assert any(element.get("class") == "caja pendiente" for element in lit.iter())
    assert "tu cuenta pendiente de pago" in lit.get("aria-label")
    css = base_stylesheet()
    assert ".planta .caja.pendiente .halo-caja { opacity: 1;" in css
    assert re.search(r"prefers-reduced-motion[^@]*\.planta \.caja\.pendiente \.halo-caja", css)


# The simulated cashier


def _client(restaurant: FakeRestaurant):
    return restaurant.client(ActorContext(actor_id="Ana", authenticated=True))


def _visit(restaurant: FakeRestaurant) -> VisitSession:
    visit = VisitSession(_client(restaurant))
    visit.arrive()
    return visit


def test_the_simulated_cashier_bills_only_served_dishes_and_closes_the_visit() -> None:
    restaurant = FakeRestaurant(serve_seconds=0)
    visit = _visit(restaurant)
    visit.send_message("La cuenta, por favor")
    assert visit.snapshot.messages[-1].text == NOTHING_SERVED

    visit.send_message("Pido una morcilla a la brasa y unas croquetas de morcilla")
    visit.send_message("La cuenta, por favor")
    *_, request, bill, reply = visit.snapshot.messages
    assert (request.role, bill.role, reply.role) == ("user", "cashier", "assistant")
    assert bill.cashier.result.total == Decimal("17.50")
    assert visit.snapshot.pending_bill.bill_id == bill.cashier.bill_id
    assert visit.allows(Action.DECIDE_PAYMENT)
    assert [step.label for step in request.activity][-2:] == [
        "Caja A2A: envía la cuenta", "Caja: consulta precios en la carta",
    ]
    visit.send_message("Pago con tarjeta, la cuenta")
    assert visit.snapshot.messages[-1].text == PAY_WITH_BUTTONS

    visit.decide_payment(PaymentMethod.CASH)

    *_, receipt, goodbye = visit.snapshot.messages
    assert receipt.cashier.result.method is PaymentMethod.CASH
    assert goodbye.text == GOODBYE
    assert visit.snapshot.visit_closed and visit.snapshot.pending_bill is None
    assert visit.snapshot.allowed_actions == [Action.ARRIVE]
    count = len(visit.snapshot.messages)
    visit.decide_payment(PaymentMethod.CARD)
    assert len(visit.snapshot.messages) == count


def test_the_simulated_cashier_waits_for_the_pass_and_new_dishes_void_the_bill() -> None:
    now = [0.0]
    restaurant = FakeRestaurant(serve_seconds=5, monotonic=lambda: now[0])
    visit = _visit(restaurant)
    visit.send_message("Pido una morcilla a la brasa")
    visit.send_message("La cuenta, por favor")
    assert visit.snapshot.messages[-1].text == SERVE_FIRST
    now[0] = 5
    visit.send_message("La cuenta, por favor")
    bill_id = visit.snapshot.pending_bill.bill_id

    visit.send_message("Pido unas croquetas de morcilla")

    assert visit.snapshot.pending_bill is None and not visit.allows(Action.DECIDE_PAYMENT)
    assert "msg caja void" in conversation_markup(visit.view())
    assert bill_id not in {message.cashier.bill_id for message in visit.snapshot.messages if message.cashier} - {bill_id}


def test_a_payment_sends_the_pending_bill_with_the_chosen_method() -> None:
    restaurant = FakeRestaurant(serve_seconds=0)
    visit = _visit(restaurant)
    visit.decide_payment(PaymentMethod.CARD)
    assert visit.pending is None and visit.cards == []
    visit.send_message("Pido una morcilla a la brasa")
    visit.send_message("La cuenta, por favor")
    sent = []
    submit = visit._client.submit

    async def spy(command):
        sent.append(command)
        return await submit(command)

    visit._client.submit = spy
    visit.decide_payment(PaymentMethod.CARD)

    [command] = sent
    assert command.event_type == "payment.confirmation_decided"
    assert (command.payload.bill_id, command.payload.version, command.payload.method) == (
        visit.snapshot.messages[-2].cashier.bill_id, 1, PaymentMethod.CARD,
    )
    assert COMMAND_ADAPTER.validate_json(command.model_dump_json()) == command


# The whole view

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "frontend" / "app.py"


@pytest.fixture
def app(monkeypatch) -> AppTest:
    import frontend.config

    monkeypatch.setenv("FRONTEND_BFF_CLIENT", "fake")
    monkeypatch.setenv("FRONTEND_FAKE_PAUSE_SECONDS", "0")
    monkeypatch.setenv("FRONTEND_FAREWELL_SECONDS", "30")
    monkeypatch.setattr(frontend.config, "FakeRestaurant", partial(FakeRestaurant, serve_seconds=0))
    return AppTest.from_file(str(APP), default_timeout=15).run()


def _markup(at: AppTest) -> str:
    return "\n".join(element.value for element in at.markdown)


def _say(at: AppTest, text: str) -> AppTest:
    return at.chat_input(key="redactor").set_value(text).run()


def test_the_bill_bubble_its_buttons_and_the_farewell_back_to_the_door(app) -> None:
    app.text_input(key="name").input("Ana")
    at = app.button(key="enter").click().run().run()
    at = _say(at, "Venimos tres")
    at = next(button for button in at.button if button.label == "Confirmar").click().run()
    at = _say(at, "Pido una morcilla a la brasa y unas croquetas de morcilla").run()
    at = _say(at, "La cuenta, por favor")
    assert not at.exception
    markup = _markup(at)
    conversation = next(element.value for element in at.markdown if 'class="mensajes"' in element.value)
    request = conversation.rindex("La cuenta, por favor")
    bill = conversation.index('class="msg caja pending"')
    reply = conversation.index("Aquí tenéis la cuenta.")
    assert request < bill < reply
    assert "Total 17,50 €" in markup and 'class="caja pendiente"' in markup
    labels = [button.label for button in at.button if (button.key or "").startswith("pagar-")]
    assert labels == ["Tarjeta", "Efectivo"]

    at = next(button for button in at.button if button.label == "Tarjeta").click().run()

    assert not at.exception
    markup = _markup(at)
    assert "Pagado con tarjeta" in markup and GOODBYE.split("!")[0] in markup
    assert not [button for button in at.button if (button.key or "").startswith("pagar-")]
    assert at.session_state["stage"] == "inside"
    at.session_state["farewell_seconds"] = 0
    at = at.run()
    assert at.session_state["stage"] == "outside"
    assert 'class="escena cerrada"' in _markup(at)
    at.text_input(key="name").input("Ana")
    at = at.button(key="enter").click().run().run()
    markup = _markup(at)
    assert "Pagado con tarjeta" not in markup and "Hombre, Ana" in markup
