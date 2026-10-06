"""Caja v1 in the BFF: served lines, the bill as its own message, payment and the end of the visit.

The waiter plays its part with the scripted agent and a fake cashier: «Pido…»
returns a cooked kitchen plan and «la cuenta» a bill for the served lines the
BFF sent; the payment returns the receipt (or the failure) it is told to.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from restaurant_agent.cashier.rendering import BILL_CLOSED, GOODBYE, PAY_AGAIN, render_text
from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.application import Action, ActorContext, ChatMessage, ErrorCode
from restaurant_contracts.cashier import (
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    BillStage,
    CashierFailureCode,
    CashierReport,
    CashierTask,
    PaymentMethod,
    PendingBill,
    Receipt,
    ServedLine,
    cashier_failure,
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
from restaurant_contracts.memory_store import SQLiteMemoryStore

from bff.local_waiter import LocalWaiter
from bff.scripted import ScriptedWaiterAgent
from bff.scripted_seating import ScriptedSeating
from bff.service import (
    BILL_NOT_PAYABLE,
    CASHIER_UNAVAILABLE,
    NO_BILL,
    STALE_BILL,
    VISIT_CLOSED,
    RestaurantService,
)
from bff.storage import Database
from bff.waiter import (
    RemoteWaiter,
    WaiterPayCall,
    WaiterPayResult,
    WaiterTurn,
    WaiterUnavailableError,
)

AT = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
DISHES = {
    "morcilla a la brasa": ("morcilla-de-burgos-a-la-brasa", "Morcilla de Burgos a la brasa", KitchenStation.BRASA, "8.50"),
    "croquetas": ("croquetas-de-morcilla", "Croquetas de morcilla", KitchenStation.FRITOS, "9.00"),
}
PRICES = {carta_id: Decimal(price) for carta_id, _, _, price in DISHES.values()}


def kitchen(order_id: str, *dishes: str, refused: str | None = None) -> KitchenReport:
    lines = [KitchenOrderLine(line=number, name=dish) for number, dish in enumerate(dishes, 1)]
    if refused:
        lines.append(KitchenOrderLine(line=len(lines) + 1, name=refused))
    accepted = [
        AcceptedItem(line=number, carta_id=DISHES[dish][0], name=DISHES[dish][1], quantity=1, station=DISHES[dish][2])
        for number, dish in enumerate(dishes, 1)
    ]
    stations = [
        StationPlan(station=station, tasks=[
            StationTask(line=item.line, carta_id=item.carta_id, name=item.name, quantity=1)
            for item in accepted if item.station is station
        ])
        for station in (KitchenStation.BRASA, KitchenStation.FRITOS)
        if any(item.station is station for item in accepted)
    ]
    rejected = (
        [RejectedItem(line=len(lines), requested=refused, quantity=1, reason="No está en la carta.")]
        if refused else []
    )
    plan = KitchenPlan(order_id=order_id, accepted=accepted, rejected=rejected, stations=stations)
    return KitchenReport(order=KitchenOrder(order_id=order_id, lines=lines), result=plan, text="Platos cocinados")


def priced(request: BillRequest, stage: BillStage = BillStage.AWAITING_PAYMENT) -> Bill:
    lines = [
        BillLine(**line.model_dump(), unit_price=PRICES[line.carta_id], line_total=PRICES[line.carta_id] * line.quantity)
        for line in request.lines
    ]
    return Bill(
        bill_id=request.bill_id, stage=stage, lines=lines,
        total=sum((line.line_total for line in lines), Decimal(0)),
        payment_options=[] if stage is BillStage.AWAITING_REVIEW else [PaymentMethod.CARD, PaymentMethod.CASH],
        sources=[BillSource(document="carta de la casa", version="1")],
    )


class CashierWaiter:
    """The scripted waiter, plus a cooking kitchen on «Pido…» and a cashier on «la cuenta»."""

    def __init__(self, waiter: LocalWaiter) -> None:
        self._waiter = waiter
        self.mode = waiter.mode
        self.turns: list[WaiterTurn] = []
        self.payments: list[WaiterPayCall] = []
        self.bills: dict[str, Bill] = {}
        self.paid: CashierFailureCode | Exception | None = None
        self.orders = 0

    async def take_turn(self, turn: WaiterTurn):
        self.turns.append(turn)
        result = await self._waiter.take_turn(turn)
        text = turn.message.casefold()
        if text.startswith("pido"):
            self.orders += 1
            dishes = [dish for dish in DISHES if dish in text]
            refused = "hamburguesa" if "hamburguesa" in text else None
            result = replace(result, kitchen=kitchen(f"ko_{self.orders}", *dishes, refused=refused))
        if "la cuenta" in text and turn.served and not turn.orders_at_pass and turn.pending_bill is None:
            request = BillRequest(bill_id=f"bill_{len(self.bills) + 1}", lines=list(turn.served))
            bill = self.bills[request.bill_id] = priced(request)
            result = replace(
                result,
                cashier=CashierReport(
                    bill_id=request.bill_id, request=request, result=bill, text=render_text(bill),
                    task=CashierTask(task_id=f"task_{request.bill_id}", context_id="ctx_1"),
                ),
            )
        return result

    async def pay_bill(self, call: WaiterPayCall) -> WaiterPayResult:
        self.payments.append(call)
        if isinstance(self.paid, Exception):
            raise self.paid
        if self.paid is None:
            result = Receipt(
                bill_id=call.bill.bill_id, version=call.bill.version, method=call.method,
                amount=self.bills[call.bill.bill_id].total, reference="pay_1", paid_at=AT,
                idempotency_key=call.idempotency_key,
            )
            reply = GOODBYE
        else:
            result = cashier_failure(call.bill.bill_id, self.paid)
            reply = BILL_CLOSED if self.paid is CashierFailureCode.BILL_NOT_FOUND else PAY_AGAIN
        step = ActivityStep(step_id="c-caja-1", component="caja", label=f"Caja: cobra con {call.method.value}")
        return WaiterPayResult(
            reply=reply,
            cashier=CashierReport(
                bill_id=call.bill.bill_id, result=result, text=render_text(result, paying=True), task=call.bill.task
            ),
            activity=(step,),
        )

    def __getattr__(self, name):
        return getattr(self._waiter, name)


@pytest.fixture
def seating(clock) -> ScriptedSeating:
    return ScriptedSeating(clock)


@pytest.fixture
def waiter(settings, seating) -> CashierWaiter:
    memory_store = SQLiteMemoryStore(settings.bff_database_path.with_name("test-waiter-memory.db"))
    return CashierWaiter(
        LocalWaiter(
            ScriptedWaiterAgent(seating=seating), mode="scripted", max_turns=20,
            memory_store=memory_store, seating=seating,
        )
    )


@pytest.fixture
def service(settings, clock, waiter) -> RestaurantService:
    return RestaurantService(database=Database(settings.bff_database_path), waiter=waiter, clock=clock)


async def say(service, commands, session, conversation_id, text):
    pending = await service.submit(session, commands.say(conversation_id, text))
    await service.drain()
    return service.get_result(session, pending.event_id)


async def seated_and_served(service, commands, name: str = "Ana"):
    """A seated group whose morcilla and croquetas came out of the kitchen and were served."""

    session = service.authenticate(service.open_session(name).token)
    arrival = await service.submit(session, commands.arrive())
    await service.drain()
    conversation_id = arrival.conversation_id
    await say(service, commands, session, conversation_id, "Venimos tres")
    proposal = service.get_snapshot(session, conversation_id).seating.proposal
    await service.submit(session, commands.decide(conversation_id, proposal.proposal_id))
    await say(
        service, commands, session, conversation_id,
        "Pido una morcilla a la brasa, unas croquetas de morcilla y una hamburguesa",
    )
    assert service.get_snapshot(session, conversation_id).served_orders == ["ko_1"]
    return session, conversation_id


async def with_bill(service, commands, name: str = "Ana"):
    session, conversation_id = await seated_and_served(service, commands, name)
    assert (await say(service, commands, session, conversation_id, "La cuenta, por favor")).status == "completed"
    return session, conversation_id


async def test_the_turn_carries_only_the_served_kitchen_dishes_and_the_bill_is_its_own_message(
    service, commands, waiter
) -> None:
    session, conversation_id = await with_bill(service, commands)

    turn = waiter.turns[-1]
    assert [(line.order_id, line.line, line.carta_id, line.quantity) for line in turn.served] == [
        ("ko_1", 1, "morcilla-de-burgos-a-la-brasa", 1),
        ("ko_1", 2, "croquetas-de-morcilla", 1),
    ]
    assert turn.orders_at_pass == 0 and turn.pending_bill is None
    snapshot = service.get_snapshot(session, conversation_id)
    *_, order, cashier, reply = snapshot.messages
    assert (order.role, cashier.role, reply.role) == ("user", "cashier", "assistant")
    assert cashier.command_event_id == order.command_event_id == reply.command_event_id
    assert cashier.cashier.result.total == Decimal("17.50") and cashier.text.startswith("Cuenta")
    assert snapshot.pending_bill.bill_id == "bill_1" and snapshot.pending_bill.payable
    assert Action.DECIDE_PAYMENT in snapshot.allowed_actions
    # Every other message keeps the shape the previous view reads.
    raw = json.loads(snapshot.model_dump_json())
    assert all("cashier" not in message for message in raw["messages"] if message["role"] != "cashier")


async def test_the_billing_context_counts_the_orders_still_at_the_pass(service, commands, settings) -> None:
    session = service.authenticate(service.open_session("Ana").token)
    conversation_id = (await service.submit(session, commands.arrive())).conversation_id
    failed = KitchenReport(
        order=KitchenOrder(order_id="ko_3", lines=[KitchenOrderLine(line=1, name="morcilla")]),
        result=KitchenFailure(order_id="ko_3", code=KitchenFailureCode.TIMEOUT, message="Tarde."),
        text="Cocina no ha podido preparar el pedido.",
    )
    reports = [
        kitchen("ko_1", "morcilla a la brasa"),
        kitchen("ko_2", "croquetas"),
        failed,
        kitchen("ko_4", refused="hamburguesa"),
    ]
    database = Database(settings.bff_database_path)
    with database.write() as tx:
        for number, report in enumerate(reports):
            tx.add_message(conversation_id, ChatMessage(
                message_id=f"msg_k{number}", role="kitchen", text=report.text, occurred_at=AT,
                command_event_id=f"cmd_{number}", kitchen=report,
            ))
        tx.mark_served(conversation_id, "ko_1", AT)
        tx.mark_served(conversation_id, "ko_3", AT)
    with database.read() as tx:
        served, at_pass, pending = RestaurantService._billing(tx, conversation_id)

    assert served == (ServedLine(
        order_id="ko_1", line=1, carta_id="morcilla-de-burgos-a-la-brasa",
        name="Morcilla de Burgos a la brasa", quantity=1,
    ),)
    assert at_pass == 1 and pending is None


async def test_paying_records_the_receipt_says_goodbye_and_frees_the_place(
    service, commands, waiter, seating
) -> None:
    session, conversation_id = await with_bill(service, commands)
    command = commands.pay(conversation_id, "bill_1", method="tarjeta")

    result = await service.submit(session, command)

    assert result.status == "completed"
    [call] = waiter.payments
    assert call.bill == PendingBill(
        bill_id="bill_1", version=1, task=CashierTask(task_id="task_bill_1", context_id="ctx_1")
    )
    assert (call.method, call.idempotency_key) == (PaymentMethod.CARD, command.event_id)
    snapshot = service.get_snapshot(session, conversation_id)
    *_, receipt, goodbye = snapshot.messages
    assert receipt.role == "cashier" and isinstance(receipt.cashier.result, Receipt)
    assert receipt.text.startswith("Pagado con tarjeta: 17,50 €.")
    assert [step.label for step in receipt.activity] == ["Caja: cobra con tarjeta"]
    assert (goodbye.role, goodbye.text) == ("assistant", GOODBYE)
    assert receipt.command_event_id == goodbye.command_event_id == command.event_id
    assert snapshot.pending_bill is None and snapshot.visit_closed
    assert snapshot.allowed_actions == [Action.ARRIVE]
    assert snapshot.seating.status == "none" and seating.own(snapshot.visit_id) is None


async def test_a_repeated_click_never_charges_twice(service, commands, waiter) -> None:
    session, conversation_id = await with_bill(service, commands)
    first = commands.pay(conversation_id, "bill_1")
    assert (await service.submit(session, first)).status == "completed"
    messages = len(service.get_snapshot(session, conversation_id).messages)

    again = await service.submit(session, commands.pay(conversation_id, "bill_1", method="efectivo"))
    retried = await service.submit(session, first)

    assert again.status == "completed" and retried.event_id == first.event_id
    assert len(waiter.payments) == 1
    assert len(service.get_snapshot(session, conversation_id).messages) == messages


async def test_a_failed_payment_keeps_the_bill_and_retries_with_the_first_key(service, commands, waiter) -> None:
    session, conversation_id = await with_bill(service, commands)
    waiter.paid = CashierFailureCode.CASHIER_UNAVAILABLE
    first = commands.pay(conversation_id, "bill_1")

    assert (await service.submit(session, first)).status == "completed"
    snapshot = service.get_snapshot(session, conversation_id)
    *_, failure, reply = snapshot.messages
    assert failure.cashier.result.code is CashierFailureCode.CASHIER_UNAVAILABLE
    assert failure.text.startswith("Caja no ha podido cobrar.") and reply.text == PAY_AGAIN
    assert snapshot.pending_bill.bill_id == "bill_1" and not snapshot.visit_closed
    assert Action.DECIDE_PAYMENT in snapshot.allowed_actions

    waiter.paid = None
    assert (await service.submit(session, commands.pay(conversation_id, "bill_1"))).status == "completed"
    assert [call.idempotency_key for call in waiter.payments] == [first.event_id, first.event_id]
    assert service.get_snapshot(session, conversation_id).visit_closed


async def test_a_bill_the_cashier_no_longer_has_closes_without_ending_the_visit(
    service, commands, waiter
) -> None:
    session, conversation_id = await with_bill(service, commands)
    waiter.paid = CashierFailureCode.BILL_NOT_FOUND

    await service.submit(session, commands.pay(conversation_id, "bill_1"))

    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.messages[-1].text == BILL_CLOSED
    assert snapshot.pending_bill is None and Action.DECIDE_PAYMENT not in snapshot.allowed_actions
    assert not snapshot.visit_closed and snapshot.seating.status == "seated"


async def test_an_unavailable_waiter_fails_the_payment_and_keeps_the_bill(service, commands, waiter) -> None:
    session, conversation_id = await with_bill(service, commands)
    waiter.paid = WaiterUnavailableError("down")

    result = await service.submit(session, commands.pay(conversation_id, "bill_1"))

    assert result.status == "failed" and result.error.code is ErrorCode.UNAVAILABLE
    assert result.error.message == CASHIER_UNAVAILABLE
    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.process_status == "idle" and snapshot.pending_bill.bill_id == "bill_1"


async def test_ordering_more_dishes_supersedes_the_pending_bill(service, commands, waiter) -> None:
    session, conversation_id = await with_bill(service, commands)

    await say(service, commands, session, conversation_id, "Pido unas croquetas de morcilla")

    assert waiter.turns[-1].pending_bill.bill_id == "bill_1"
    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.pending_bill is None and Action.DECIDE_PAYMENT not in snapshot.allowed_actions
    stale = await service.submit(session, commands.pay(conversation_id, "bill_1"))
    assert stale.status == "failed" and stale.error.message == STALE_BILL
    assert waiter.payments == []

    await say(service, commands, session, conversation_id, "La cuenta, por favor")
    renewed = service.get_snapshot(session, conversation_id)
    assert renewed.pending_bill.bill_id == "bill_2"
    assert renewed.messages[-2].cashier.result.total == Decimal("26.50")


async def test_a_closed_visit_takes_no_more_messages(service, commands) -> None:
    session, conversation_id = await with_bill(service, commands)
    await service.submit(session, commands.pay(conversation_id, "bill_1"))

    result = await service.submit(session, commands.say(conversation_id, "Una caña más"))

    assert result.status == "failed" and result.error.message == VISIT_CLOSED


async def test_a_payment_must_name_the_pending_bill_of_the_customers_own_visit(
    service, commands, waiter, settings
) -> None:
    session, conversation_id = await with_bill(service, commands)

    stale = await service.submit(session, commands.pay(conversation_id, "bill_1", version=2))
    unknown = await service.submit(session, commands.pay(conversation_id, "bill_9"))
    luis = service.authenticate(service.open_session("Luis").token)
    foreign = await service.submit(luis, commands.pay(conversation_id, "bill_1"))

    assert (stale.error.message, unknown.error.message) == (STALE_BILL, NO_BILL)
    assert foreign.error.code is ErrorCode.FORBIDDEN
    database = Database(settings.bff_database_path)
    with database.write() as tx:
        row = tx.pending_bill(conversation_id)
        row.bill = Bill.model_validate(
            {**row.bill.model_dump(), "stage": BillStage.AWAITING_REVIEW, "payment_options": []}
        )
        tx.put_bill(row)
    reviewing = await service.submit(session, commands.pay(conversation_id, "bill_1"))
    assert reviewing.error.message == BILL_NOT_PAYABLE
    assert Action.DECIDE_PAYMENT not in service.get_snapshot(session, conversation_id).allowed_actions
    assert waiter.payments == []


async def test_the_remote_waiter_relays_billing_and_the_payment() -> None:
    seen: list[dict] = []
    bill_request = BillRequest(bill_id="bill_1", lines=[ServedLine(
        order_id="ko_1", line=1, carta_id="croquetas-de-morcilla", name="Croquetas de morcilla", quantity=1,
    )])
    task = CashierTask(task_id="task_1", context_id="ctx_1")
    bill = priced(bill_request)
    receipt = Receipt(bill_id="bill_1", version=1, method=PaymentMethod.CASH, amount=Decimal("9.00"),
                      reference="pay_1", paid_at=AT, idempotency_key="evt_1")

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["input"])
        seen.append(payload)
        if payload["operation"] == "pay_bill":
            body = {"status": "completed", "operation": "pay_bill", "reply": GOODBYE,
                    "cashier": CashierReport(bill_id="bill_1", result=receipt, text="Pagado", task=task).model_dump(mode="json")}
        else:
            body = {"status": "completed", "reply": "Aquí tenéis la cuenta.", "customer": {"presented_name": "Ana"},
                    "order_draft": {"items": []}, "turn_count": 2,
                    "cashier": CashierReport(bill_id="bill_1", request=bill_request, result=bill, text="Cuenta", task=task).model_dump(mode="json")}
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(body)}]}],
        })

    waiter = RemoteWaiter("http://waiter.invalid")
    waiter._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    actor = ActorContext(actor_id="ana", authenticated=True)
    pending = PendingBill(bill_id="bill_0", version=1, task=task)
    result = await waiter.take_turn(WaiterTurn(
        conversation_id="conv_1", actor=actor, presented_name="Ana", message="La cuenta",
        customer=CustomerSnapshot(presented_name="Ana"), order_draft=OrderDraft(), turn_count=1,
        persisted_order_preferences=(), session_json=None, correlation_id="corr_1",
        served=tuple(bill_request.lines), orders_at_pass=1, pending_bill=pending,
    ))
    paid = await waiter.pay_bill(WaiterPayCall(
        conversation_id="conv_1", actor=actor, correlation_id="corr_2", bill=pending,
        method=PaymentMethod.CASH, idempotency_key="evt_1",
    ))
    await waiter.aclose()

    assert seen[0]["served"][0]["carta_id"] == "croquetas-de-morcilla"
    assert (seen[0]["orders_at_pass"], seen[0]["pending_bill"]["bill_id"]) == (1, "bill_0")
    assert result.cashier.pending.task == task
    assert (seen[1]["operation"], seen[1]["method"], seen[1]["idempotency_key"]) == ("pay_bill", "efectivo", "evt_1")
    assert paid.reply == GOODBYE and paid.cashier.result == receipt


async def test_the_scripted_waiter_has_no_cashier_and_never_charges(settings) -> None:
    memory_store = SQLiteMemoryStore(settings.bff_database_path.with_name("test-waiter-memory.db"))
    local = LocalWaiter(ScriptedWaiterAgent(), mode="scripted", max_turns=20, memory_store=memory_store)

    paid = await local.pay_bill(WaiterPayCall(
        conversation_id="conv_1", actor=ActorContext(actor_id="ana", authenticated=True),
        correlation_id="corr_1",
        bill=PendingBill(bill_id="bill_1", version=1, task=CashierTask(task_id="t", context_id="c")),
        method=PaymentMethod.CARD, idempotency_key="evt_1",
    ))

    assert paid.cashier.result.code is CashierFailureCode.CASHIER_NOT_CONFIGURED
    assert paid.reply == BILL_CLOSED
