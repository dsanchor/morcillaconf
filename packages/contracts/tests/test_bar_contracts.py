import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from restaurant_contracts.application import ChatMessage
from restaurant_contracts.bar import (
    BAR_FAILURE_MESSAGES,
    BAR_RESULT_ADAPTER,
    BarFailureCode,
    BarItem,
    BarReport,
    BarRequest,
    BarRound,
    RejectedDrink,
    ServedDrink,
    bar_failure,
)
from restaurant_contracts.cashier import (
    BILL_NOTE,
    BillRequest,
    ServedLine,
    bill_misses,
    served_lines,
    supersedes_bill,
)
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenSource,
    KitchenStation,
    StationPlan,
    StationTask,
)
from restaurant_contracts.waiter import WAITER_TURN_RESPONSE_ADAPTER, WaiterTurnSuccess

AT = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)
CARTA = KitchenSource(document="carta de la casa", version="1")
CANA = ServedDrink(line=1, carta_id="cana-de-cerveza", name="Caña de cerveza", quantity=2)
AGUA = RejectedDrink(
    line=2, requested="agua", quantity=1,
    reason="En la carta hay varias: Agua con gas o Agua sin gas.",
    options=["Agua con gas", "Agua sin gas"],
)


def request(*names: tuple[str, int], round_id: str = "bar_1", restrictions: list[str] | None = None) -> BarRequest:
    return BarRequest(
        round_id=round_id,
        items=[BarItem(line=number, name=name, quantity=quantity) for number, (name, quantity) in enumerate(names, 1)],
        restrictions=restrictions or [],
    )


def bar(round_id: str = "bar_1", *, served: bool = True) -> BarReport:
    if served:
        result = BarRound(round_id=round_id, served=[CANA], rejected=[AGUA], sources=[CARTA])
    else:
        result = BarRound(round_id=round_id, rejected=[AGUA.model_copy(update={"line": 1, "quantity": 2}), AGUA])
    return BarReport(request=request(("cañas", 2), ("agua", 1), round_id=round_id), result=result, text="Barra")


def test_a_round_decides_every_drink_once_with_its_quantity_and_round_trips() -> None:
    report = bar()
    assert report.result.verdict == "partial"
    assert BarReport.model_validate_json(report.model_dump_json()) == report
    assert BAR_RESULT_ADAPTER.validate_json(report.result.model_dump_json()) == report.result
    assert TypeAdapter(BarReport).json_schema()
    with pytest.raises(ValidationError, match="decided once"):
        BarRound(round_id="bar_1", served=[CANA, CANA])
    with pytest.raises(ValidationError, match="both served and rejected"):
        BarRound(round_id="bar_1", served=[CANA], rejected=[AGUA.model_copy(update={"line": 1})])
    with pytest.raises(ValidationError, match="at least one"):
        BarRound(round_id="bar_1")
    with pytest.raises(ValidationError, match="every requested drink"):
        BarReport(request=request(("cañas", 2), ("agua", 1)), result=BarRound(round_id="bar_1", served=[CANA]), text="Barra")
    with pytest.raises(ValidationError, match="requested quantities"):
        BarReport(
            request=request(("cañas", 3), ("agua", 1)),
            result=BarRound(round_id="bar_1", served=[CANA], rejected=[AGUA]),
            text="Barra",
        )
    with pytest.raises(ValidationError, match="answers this round"):
        BarReport(request=request(("cañas", 2), ("agua", 1), round_id="bar_2"), result=report.result, text="Barra")


def test_a_request_is_only_the_drinks_and_the_declared_allergies() -> None:
    with pytest.raises(ValidationError, match="numbered"):
        BarRequest(round_id="bar_1", items=[BarItem(line=2, name="caña")])
    with pytest.raises(ValidationError):
        BarRequest(round_id="bar_1", items=[])
    with pytest.raises(ValidationError):
        BarRequest.model_validate({**request(("caña", 1)).model_dump(), "customer": {"presented_name": "Ana"}})
    with pytest.raises(ValidationError):
        ServedDrink(line=1, carta_id="Caña de cerveza", name="Caña de cerveza", quantity=1)


def test_a_failed_round_serves_nothing_and_says_why() -> None:
    failure = bar_failure("bar_1", BarFailureCode.NOT_CONFIGURED)
    assert failure.message == "La barra no puede consultar la carta: la base de conocimiento no está configurada."
    assert set(BAR_FAILURE_MESSAGES) == set(BarFailureCode)
    assert bar_failure("bar_1", BarFailureCode.CARTA_INCOMPLETE).message == (
        "La barra no ha podido leer entera la carta de bebidas."
    )
    report = BarReport(request=request(("caña", 1)), result=failure, text="La barra no ha podido servir.")
    assert BarReport.model_validate_json(report.model_dump_json()) == report
    assert served_lines([report], {"bar_1"}) == []
    assert not supersedes_bill(report)


def kitchen(order_id: str) -> KitchenReport:
    item = AcceptedItem(
        line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa",
        quantity=1, station=KitchenStation.BRASA,
    )
    plan = KitchenPlan(
        order_id=order_id, accepted=[item],
        stations=[StationPlan(station=KitchenStation.BRASA, tasks=[StationTask(
            line=1, carta_id=item.carta_id, name=item.name, quantity=1)])],
    )
    order = KitchenOrder(order_id=order_id, lines=[KitchenOrderLine(line=1, name="morcilla")])
    return KitchenReport(order=order, result=plan, text="Plan")


def test_served_drinks_are_billed_with_the_served_dishes_in_order() -> None:
    reports = [kitchen("ko_1"), bar("bar_1"), bar("bar_2", served=False), kitchen("ko_2")]
    lines = served_lines(reports, {"ko_1", "bar_1", "bar_2"})
    assert [(line.order_id, line.line, line.carta_id, line.quantity) for line in lines] == [
        ("ko_1", 1, "morcilla-de-burgos-a-la-brasa", 1),
        ("bar_1", 1, "cana-de-cerveza", 2),
    ]
    assert served_lines([bar("bar_3")], set()) == []
    assert BILL_NOTE == "Solo lo ya servido: platos de cocina y bebidas de la barra."


def test_new_drinks_supersede_a_bill_that_does_not_include_them() -> None:
    drinks = bar("bar_1")
    assert supersedes_bill(drinks) and not supersedes_bill(bar("bar_2", served=False))
    covered = BillRequest(
        bill_id="bill_1",
        lines=[ServedLine(order_id="bar_1", line=1, carta_id="cana-de-cerveza", name="Caña de cerveza", quantity=2)],
    )
    earlier = BillRequest(bill_id="bill_0", lines=served_lines([kitchen("ko_1")], {"ko_1"}))
    assert not bill_misses(covered, drinks)
    assert bill_misses(earlier, drinks) and bill_misses(None, drinks)
    assert bill_misses(covered, kitchen("ko_2"))


def test_bar_messages_carry_their_report_and_the_others_keep_their_shape() -> None:
    message = ChatMessage(
        message_id="m_b", role="bar", text="x" * 3_000, occurred_at=AT, command_event_id="c", bar=bar(),
    )
    assert ChatMessage.model_validate_json(message.model_dump_json()) == message
    waiter = ChatMessage(message_id="m_w", role="assistant", text="Hola", occurred_at=AT, command_event_id="c")
    assert "bar" not in json.loads(waiter.model_dump_json())
    with pytest.raises(ValidationError, match="only they"):
        ChatMessage(message_id="m", role="bar", text="Barra", occurred_at=AT, command_event_id="c")
    with pytest.raises(ValidationError, match="only they"):
        waiter.model_validate({**waiter.model_dump(), "bar": bar().model_dump()})
    with pytest.raises(ValidationError, match="at most"):
        ChatMessage(message_id="m", role="assistant", text="x" * 3_000, occurred_at=AT, command_event_id="c")


def test_the_waiter_turn_carries_the_bar_only_when_it_served() -> None:
    success = WaiterTurnSuccess(
        reply="Aquí tenéis las cañas.", customer=CustomerSnapshot(), order_draft=OrderDraft(), turn_count=2,
    )
    assert "bar" not in json.loads(success.model_dump_json())
    served = success.model_copy(update={"bar": bar()})
    parsed = WAITER_TURN_RESPONSE_ADAPTER.validate_json(served.model_dump_json())
    assert parsed == served and parsed.bar.result.served[0].quantity == 2
