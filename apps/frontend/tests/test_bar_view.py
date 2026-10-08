"""Barra v1 in the view: the bar's bubble, its place in the conversation and the simulated bar."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

from restaurant_contracts.application import ActorContext, ChatMessage
from restaurant_contracts.bar import (
    BarFailureCode,
    BarItem,
    BarReport,
    BarRequest,
    BarRound,
    RejectedDrink,
    ServedDrink,
    bar_failure,
)
from restaurant_contracts.kitchen import KitchenSource

from frontend.fake_bar import fake_bar_report
from frontend.fake_client import FakeRestaurant
from frontend.fake_kitchen import fake_kitchen_report
from frontend.markup import (
    BAR_FAILED,
    BAR_LABEL,
    bar_row,
    cashier_icon_svg,
    chef_icon_svg,
    conversation_markup,
)
from frontend.stylesheets import base_stylesheet
from frontend.visit import ConversationView, VisitSession

AT = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)
REQUEST = BarRequest(
    round_id="bar_1",
    items=[BarItem(line=1, name="cañas", quantity=2), BarItem(line=2, name="agua")],
)
ROUND = BarRound(
    round_id="bar_1",
    served=[ServedDrink(line=1, carta_id="cana-de-cerveza", name="Caña de cerveza", quantity=2)],
    rejected=[RejectedDrink(
        line=2, requested="agua", quantity=1, reason="En la carta hay varias: Agua con gas o Agua sin gas.",
        options=["Agua con gas", "Agua sin gas"],
    )],
    sources=[KitchenSource(document="carta de la casa", version="1")],
)
SERVED = BarReport(request=REQUEST, result=ROUND, text="Barra")


def _message(report: BarReport, message_id: str = "m_b") -> ChatMessage:
    return ChatMessage(
        message_id=message_id, role="bar", text=report.text, occurred_at=AT, command_event_id="cmd_1", bar=report,
    )


def _parse(markup: str) -> ET.Element:
    return ET.fromstring(markup.replace("&nbsp;", " "))


def _text(element: ET.Element) -> str:
    return "".join(element.itertext())


# The bubble


def test_the_round_lists_what_was_served_and_what_not_with_its_reason() -> None:
    root = _parse(bar_row(_message(SERVED)))

    assert root.get("class") == "msg barra"
    bubble = root.find("div[@class='burbuja ronda-barra']")
    assert (bubble.get("role"), bubble.get("aria-label")) == ("group", BAR_LABEL)
    assert [_text(element) for element in bubble.findall("p")] == [
        "Barra", "Servido", "No servido", "Fuentes: carta de la casa (versión 1)",
    ]
    assert [_text(item) for item in bubble.iter("li")] == [
        "2 × Caña de cerveza",
        "1 × aguaEn la carta hay varias: Agua con gas o Agua sin gas.",
    ]


def test_a_failed_round_says_the_bar_served_nothing_and_why() -> None:
    failure = bar_failure("bar_1", BarFailureCode.KNOWLEDGE_UNAVAILABLE)
    root = _parse(bar_row(_message(BarReport(request=REQUEST, result=failure, text="La barra no ha podido."))))

    assert _text(root) == f"Barra{BAR_FAILED}La barra no puede consultar la carta ahora mismo."


def test_the_caña_is_the_bars_icon_not_the_toque_or_the_till() -> None:
    icon = _parse(bar_row(_message(SERVED))).find("svg")

    assert icon.get("class") == "icono" and icon.get("aria-hidden") == "true"
    assert len(icon.findall("circle")) == 3 and not icon.findall("rect")
    assert ET.tostring(icon) not in (ET.tostring(_parse(chef_icon_svg())), ET.tostring(_parse(cashier_icon_svg())))


def test_the_conversation_places_the_bar_after_the_chef_and_before_the_waiter() -> None:
    kitchen = fake_kitchen_report("Pido una morcilla a la brasa y un agua con gas", "ko_1")
    bar = fake_bar_report("Pido una morcilla a la brasa y un agua con gas", "bar_1")
    messages = (
        ChatMessage(message_id="m1", role="user", text="Pido una morcilla a la brasa y un agua con gas",
                    occurred_at=AT, command_event_id="c"),
        ChatMessage(message_id="m2", role="kitchen", text=kitchen.text, occurred_at=AT, command_event_id="c",
                    kitchen=kitchen),
        _message(bar, "m3"),
        ChatMessage(message_id="m4", role="assistant", text="Aquí tenéis.", occurred_at=AT, command_event_id="c"),
    )

    markup = conversation_markup(ConversationView(messages=messages))

    order = [markup.index(marker) for marker in ("msg cliente", "msg cocina", "msg barra", "Aquí tenéis.")]
    assert order == sorted(order)


def test_the_bar_speaks_white_on_toasted_amber() -> None:
    css = base_stylesheet()
    amber = re.search(r"--cerveza: (#[0-9a-f]{6});", css).group(1)
    lantern = re.search(r"--lumbre: (#[0-9a-f]{6});", css).group(1)
    bubble = re.search(r"\.msg\.barra \.burbuja \{([^}]*)\}", css).group(1)
    assert "background: var(--cerveza)" in bubble and "color: #ffffff" in bubble
    assert ".stApp .ronda-barra .detalle" in css and ".stApp .msg.barra { max-width: 100%; }" in css

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def blend(alpha: float, under: str) -> str:
        channels = [round(alpha * 255 + (1 - alpha) * int(under[i : i + 2], 16)) for i in (1, 3, 5)]
        return "#" + "".join(f"{channel:02x}" for channel in channels)

    def contrast(a: str, b: str) -> float:
        light, dark = sorted((luminance(a), luminance(b)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    assert contrast("#ffffff", amber) >= 6
    # The details are white at 86 %: still readable on the amber.
    assert contrast(blend(0.86, amber), amber) >= 4.5
    # Darker than the lantern amber, which stays for actions and state.
    assert luminance(amber) < luminance(lantern) / 4


# The simulated bar


def _visit(restaurant: FakeRestaurant) -> VisitSession:
    visit = VisitSession(restaurant.client(ActorContext(actor_id="Ana", authenticated=True)))
    visit.arrive()
    return visit


def test_the_simulated_bar_serves_at_once_and_its_drinks_are_billed() -> None:
    restaurant = FakeRestaurant(serve_seconds=0)
    visit = _visit(restaurant)

    visit.send_message("Ponme dos cañas")

    *_, request, bar, reply = visit.snapshot.messages
    assert (request.role, bar.role, reply.role) == ("user", "bar", "assistant")
    assert [(item.carta_id, item.quantity) for item in bar.bar.result.served] == [("cana-de-cerveza", 2)]
    assert visit.snapshot.served_orders == [bar.bar.result.round_id]
    assert [step.label for step in request.activity][-2:] == ["Barra: sirve las bebidas", "Foundry IQ: bebidas de la carta"]

    visit.send_message("La cuenta, por favor")
    assert visit.snapshot.messages[-2].cashier.result.total == Decimal("5.00")

    visit.send_message("Ponme otra caña")
    assert visit.snapshot.pending_bill is None
    visit.send_message("La cuenta, por favor")
    assert visit.snapshot.messages[-2].cashier.result.total == Decimal("7.50")


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Ponme un agua", "En la carta hay varias: Agua con gas o Agua sin gas."),
        ("Ponme una coca-cola", "No está en la carta."),
        ("Ponme una caña, soy celíaco", "Contiene cereales con gluten según la carta y has indicado celiaquía."),
    ],
)
def test_the_simulated_bar_never_serves_what_the_carta_does_not_back(message, reason) -> None:
    visit = _visit(FakeRestaurant(serve_seconds=0))

    visit.send_message(message)

    bar = visit.snapshot.messages[-2].bar
    assert bar.result.served == [] and bar.result.rejected[0].reason == reason
    assert visit.snapshot.served_orders == []


def test_a_mixed_order_is_billed_once_the_dishes_are_served() -> None:
    visit = _visit(FakeRestaurant(serve_seconds=0))

    visit.send_message("Pido una morcilla a la brasa y un agua con gas")
    roles = [message.role for message in visit.snapshot.messages if message.command_event_id == visit.snapshot.messages[-1].command_event_id]
    assert roles == ["user", "kitchen", "bar", "assistant"]
    visit.send_message("La cuenta, por favor")

    assert visit.snapshot.messages[-2].cashier.result.total == Decimal("10.70")


# The whole view

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "frontend" / "app.py"


@pytest.fixture
def app(monkeypatch) -> AppTest:
    import frontend.config

    monkeypatch.setenv("FRONTEND_BFF_CLIENT", "fake")
    monkeypatch.setenv("FRONTEND_FAKE_PAUSE_SECONDS", "0")
    monkeypatch.setattr(frontend.config, "FakeRestaurant", partial(FakeRestaurant, serve_seconds=0))
    return AppTest.from_file(str(APP), default_timeout=15).run()


def test_the_bar_bubble_sits_between_the_chef_and_the_waiter(app) -> None:
    app.text_input(key="name").input("Ana")
    at = app.button(key="enter").click().run().run()
    at = at.chat_input(key="redactor").set_value("Pido una morcilla a la brasa y un agua con gas").run()

    assert not at.exception
    conversation = next(element.value for element in at.markdown if 'class="mensajes"' in element.value)
    order = [
        conversation.rindex("Pido una morcilla a la brasa y un agua con gas"),
        conversation.index('class="msg cocina"'),
        conversation.index('class="msg barra"'),
        conversation.index("De la barra&#58; 1 × Agua con gas."),
    ]
    assert order == sorted(order)
    assert 'aria-label="Barra"' in conversation and "1 × Agua con gas" in conversation
