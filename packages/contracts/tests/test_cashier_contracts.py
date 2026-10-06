import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    Action,
    ChatMessage,
    DecidePaymentCommand,
    RestaurantSnapshot,
)
from restaurant_contracts.cashier import (
    BILL_NOTE,
    CASHIER_INPUT_ADAPTER,
    CASHIER_RESULT_ADAPTER,
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    BillStage,
    BillView,
    CashierFailureCode,
    CashierReport,
    CashierTask,
    PaymentChoice,
    PaymentMethod,
    PendingBill,
    Receipt,
    ReviewDecision,
    ServedLine,
    cashier_failure,
    euros,
    served_lines,
    supersedes_bill,
)
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)
from restaurant_contracts.waiter import (
    WAITER_PAY_RESPONSE_ADAPTER,
    WAITER_REQUEST_ADAPTER,
    WAITER_TURN_RESPONSE_ADAPTER,
    WaiterPayRequest,
    WaiterPaySuccess,
    WaiterTurnRequest,
    WaiterTurnSuccess,
)

AT = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)
TASK = CashierTask(task_id="task_1", context_id="ctx_1")
MORCILLA = ServedLine(
    order_id="ko_1", line=1, carta_id="morcilla-de-burgos-a-la-brasa",
    name="Morcilla de Burgos a la brasa", quantity=1,
)
CROQUETAS = ServedLine(
    order_id="ko_1", line=2, carta_id="croquetas-de-morcilla", name="Croquetas de morcilla", quantity=1
)
REQUEST = BillRequest(bill_id="bill_1", lines=[MORCILLA, CROQUETAS])


def priced(line: ServedLine, unit: str) -> BillLine:
    price = Decimal(unit)
    return BillLine(**line.model_dump(), unit_price=price, line_total=price * line.quantity)


def bill(**changes) -> Bill:
    values = {
        "bill_id": "bill_1",
        "lines": [priced(MORCILLA, "8.50"), priced(CROQUETAS, "9.00")],
        "total": Decimal("17.50"),
        "sources": [BillSource(document="carta de la casa", version="1")],
    }
    values.update(changes)
    return Bill(**values)


def report(result=None, **changes) -> CashierReport:
    values = {"bill_id": "bill_1", "request": REQUEST, "result": result or bill(), "text": "Cuenta", "task": TASK}
    values.update(changes)
    return CashierReport(**values)


def receipt(**changes) -> Receipt:
    values = {
        "bill_id": "bill_1", "version": 1, "method": PaymentMethod.CARD, "amount": Decimal("17.50"),
        "reference": "pay_1", "paid_at": AT, "idempotency_key": "evt_1",
    }
    values.update(changes)
    return Receipt(**values)


def test_a_bill_adds_up_exactly_in_euros_and_round_trips() -> None:
    value = bill()
    assert value.stage is BillStage.AWAITING_PAYMENT
    assert value.payment_options == [PaymentMethod.CARD, PaymentMethod.CASH]
    assert value.currency == "EUR" and value.note == BILL_NOTE
    data = json.loads(value.model_dump_json())
    assert (data["total"], data["lines"][0]["unit_price"]) == ("17.50", "8.50")
    assert CASHIER_RESULT_ADAPTER.validate_json(value.model_dump_json()) == value
    assert TypeAdapter(CashierReport).json_schema()
    with pytest.raises(ValidationError, match="sum of the line totals"):
        bill(total=Decimal("17.49"))
    with pytest.raises(ValidationError, match="unit price times its quantity"):
        BillLine(**MORCILLA.model_dump(exclude={"quantity"}), quantity=2,
                 unit_price=Decimal("8.50"), line_total=Decimal("8.50"))
    with pytest.raises(ValidationError):
        bill(sources=[])
    with pytest.raises(ValidationError):
        priced(MORCILLA, "8.505")


def test_each_served_line_is_billed_once() -> None:
    with pytest.raises(ValidationError, match="billed once"):
        BillRequest(bill_id="bill_1", lines=[MORCILLA, MORCILLA])
    with pytest.raises(ValidationError, match="billed once"):
        bill(lines=[priced(MORCILLA, "8.50"), priced(MORCILLA, "8.50")], total=Decimal("17.00"))
    with pytest.raises(ValidationError):
        BillRequest(bill_id="bill_1", lines=[])
    with pytest.raises(ValidationError):
        BillRequest.model_validate({**REQUEST.model_dump(), "customer": {"presented_name": "Ana"}})


def test_only_a_bill_awaiting_payment_offers_card_or_cash() -> None:
    review = bill(stage=BillStage.AWAITING_REVIEW, payment_options=[])
    assert not BillView.of(review).payable
    assert BillView.of(bill()).payable
    with pytest.raises(ValidationError, match="payment options"):
        bill(stage=BillStage.AWAITING_REVIEW)
    with pytest.raises(ValidationError, match="payment options"):
        bill(payment_options=[])
    with pytest.raises(ValidationError, match="offered once"):
        bill(payment_options=[PaymentMethod.CARD, PaymentMethod.CARD])


def test_the_cashier_inputs_are_told_apart_by_their_kind() -> None:
    choice = PaymentChoice(bill_id="bill_1", version=1, method=PaymentMethod.CASH, idempotency_key="evt_1")
    review = ReviewDecision(bill_id="bill_1", version=1, decision="approved", reviewer="caja")
    for value in (REQUEST, choice, review):
        assert CASHIER_INPUT_ADAPTER.validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        CASHIER_INPUT_ADAPTER.validate_python({"kind": "refund", "bill_id": "bill_1"})


def test_a_report_prices_exactly_the_served_lines_and_keeps_its_task() -> None:
    value = report()
    assert value.pending == PendingBill(bill_id="bill_1", version=1, task=TASK)
    assert CashierReport.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError, match="exactly the served lines"):
        report(bill(lines=[priced(MORCILLA, "8.50")], total=Decimal("8.50")))
    with pytest.raises(ValidationError, match="answers this bill"):
        report(bill(bill_id="bill_2"))
    with pytest.raises(ValidationError, match="keeps its A2A task"):
        report(task=None)
    paid = report(receipt(), request=None)
    assert paid.pending is None and paid.result.status == "paid"
    failed = report(cashier_failure("bill_1", CashierFailureCode.PRICE_MISSING), task=None)
    assert failed.pending is None
    assert failed.result.message.startswith("Caja no encuentra en la carta")
    with pytest.raises(ValidationError):
        receipt(paid_at=datetime(2026, 10, 6, 21, 0))


def kitchen(order_id: str, *, accepted: bool = True) -> KitchenReport:
    order = KitchenOrder(
        order_id=order_id,
        lines=[KitchenOrderLine(line=1, name="morcilla"), KitchenOrderLine(line=2, name="hamburguesa")],
    )
    item = AcceptedItem(
        line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa",
        quantity=1, station=KitchenStation.BRASA,
    )
    refused = RejectedItem(line=2, requested="hamburguesa", quantity=1, reason="No está en la carta.")
    if not accepted:
        plan = KitchenPlan(order_id=order_id, rejected=[refused.model_copy(update={"line": 1}), refused])
    else:
        plan = KitchenPlan(
            order_id=order_id,
            accepted=[item],
            rejected=[refused],
            stations=[StationPlan(station=KitchenStation.BRASA, tasks=[StationTask(
                line=1, carta_id=item.carta_id, name=item.name, quantity=1)])],
        )
    return KitchenReport(order=order, result=plan, text="Plan")


def test_only_accepted_dishes_of_served_orders_can_be_billed() -> None:
    reports = [kitchen("ko_1"), kitchen("ko_2"), kitchen("ko_3", accepted=False)]
    lines = served_lines(reports, {"ko_1", "ko_3"})
    assert [(line.order_id, line.line, line.carta_id) for line in lines] == [
        ("ko_1", 1, "morcilla-de-burgos-a-la-brasa")
    ]
    failure = KitchenReport(
        order=reports[0].order,
        result=KitchenFailure(order_id="ko_1", code=KitchenFailureCode.TIMEOUT, message="Tarde."),
        text="Fallo",
    )
    assert served_lines([failure], {"ko_1"}) == []
    assert supersedes_bill(reports[0])
    assert not supersedes_bill(reports[2]) and not supersedes_bill(failure)


def test_amounts_read_in_spanish() -> None:
    assert euros(Decimal("17.5")) == "17,50 €"
    assert euros(Decimal("1234.5")) == "1.234,50 €"
    assert euros(Decimal("0")) == "0,00 €"


def test_cashier_messages_carry_their_report_and_the_others_keep_their_shape() -> None:
    cashier = ChatMessage(
        message_id="m_c", role="cashier", text="x" * 3_000, occurred_at=AT,
        command_event_id="c", cashier=report(),
    )
    assert ChatMessage.model_validate_json(cashier.model_dump_json()) == cashier
    waiter = ChatMessage(message_id="m_w", role="assistant", text="Hola", occurred_at=AT, command_event_id="c")
    assert "cashier" not in json.loads(waiter.model_dump_json())
    with pytest.raises(ValidationError, match="only they"):
        ChatMessage(message_id="m", role="cashier", text="Cuenta", occurred_at=AT, command_event_id="c")
    with pytest.raises(ValidationError, match="only they"):
        waiter.model_validate({**waiter.model_dump(), "cashier": report().model_dump()})


def snapshot(**changes) -> RestaurantSnapshot:
    values = {
        "schema_version": 1, "visit_id": "visit_1", "conversation_id": "conv_1",
        "identity": {"actor_id": "ana", "authenticated": True}, "cursor": 1, "messages": [],
        "customer": CustomerSnapshot(presented_name="Ana", party_size=2), "order_draft": OrderDraft(),
        "pending_fields": [], "memory": {"memories": []}, "process_status": "idle",
        "allowed_actions": [Action.SEND_MESSAGE],
    }
    values.update(changes)
    return RestaurantSnapshot(**values)


def test_the_snapshot_offers_payment_only_for_a_bill_awaiting_it() -> None:
    plain = json.loads(snapshot().model_dump_json())
    assert "pending_bill" not in plain and "visit_closed" not in plain
    payable = snapshot(
        pending_bill=BillView.of(bill()), allowed_actions=[Action.SEND_MESSAGE, Action.DECIDE_PAYMENT]
    )
    assert RestaurantSnapshot.model_validate_json(payable.model_dump_json()) == payable
    with pytest.raises(ValidationError, match="bill awaiting payment"):
        snapshot(allowed_actions=[Action.DECIDE_PAYMENT])
    with pytest.raises(ValidationError, match="bill awaiting payment"):
        snapshot(
            pending_bill=BillView.of(bill(stage=BillStage.AWAITING_REVIEW, payment_options=[])),
            allowed_actions=[Action.DECIDE_PAYMENT],
        )
    closed = snapshot(visit_closed=True, allowed_actions=[Action.ARRIVE])
    assert json.loads(closed.model_dump_json())["visit_closed"] is True


def test_the_payment_command_names_the_bill_and_the_method() -> None:
    command = COMMAND_ADAPTER.validate_python({
        "schema_version": 1, "event_id": "evt_1", "occurred_at": AT, "conversation_id": "conv_1",
        "event_type": "payment.confirmation_decided",
        "payload": {"bill_id": "bill_1", "version": 1, "method": "efectivo"},
    })
    assert isinstance(command, DecidePaymentCommand)
    assert command.payload.method is PaymentMethod.CASH
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({
            **command.model_dump(), "payload": {"bill_id": "bill_1", "version": 1, "method": "bizum"},
        })


def turn_request(**changes) -> WaiterTurnRequest:
    values = {
        "conversation_id": "conv_1", "actor": {"actor_id": "ana", "authenticated": True},
        "presented_name": "Ana", "message": "La cuenta, por favor", "customer": CustomerSnapshot(),
        "order_draft": OrderDraft(), "turn_count": 3, "correlation_id": "corr_1",
    }
    values.update(changes)
    return WaiterTurnRequest(**values)


def test_the_waiter_turn_carries_billing_context_only_when_there_is_one() -> None:
    plain = json.loads(turn_request().model_dump_json())
    assert not {"served", "orders_at_pass", "pending_bill"} & set(plain)
    full = turn_request(
        served=[MORCILLA], orders_at_pass=1,
        pending_bill=PendingBill(bill_id="bill_1", version=1, task=TASK),
    )
    assert WAITER_REQUEST_ADAPTER.validate_json(full.model_dump_json()) == full
    success = WaiterTurnSuccess(
        reply="Aquí tenéis la cuenta.", customer=CustomerSnapshot(), order_draft=OrderDraft(), turn_count=4
    )
    assert "cashier" not in json.loads(success.model_dump_json())
    with_bill = success.model_copy(update={"cashier": report()})
    assert WAITER_TURN_RESPONSE_ADAPTER.validate_json(with_bill.model_dump_json()) == with_bill


def test_the_waiter_relays_a_payment_without_the_model() -> None:
    request = WaiterPayRequest(
        conversation_id="conv_1", actor={"actor_id": "ana", "authenticated": True},
        correlation_id="corr_1", bill=PendingBill(bill_id="bill_1", version=1, task=TASK),
        method=PaymentMethod.CARD, idempotency_key="evt_1",
    )
    assert WAITER_REQUEST_ADAPTER.validate_json(request.model_dump_json()) == request
    paid = WaiterPaySuccess(reply="¡Hasta pronto!", cashier=report(receipt(), request=None))
    parsed = WAITER_PAY_RESPONSE_ADAPTER.validate_json(paid.model_dump_json())
    assert parsed == paid and "activity" not in json.loads(paid.model_dump_json())
