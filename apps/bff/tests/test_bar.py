"""Barra v1 in the BFF: the bar's round as its own message, served at once and billed.

The waiter plays its part with the scripted agent, plus a fake kitchen on
«Pido…», a fake bar on drinks and a fake cashier on «la cuenta» that bills
the served lines the BFF sent (and, like the real tool, the drinks served
earlier in the same turn).
"""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from restaurant_agent.bar.rendering import render_text as bar_text
from restaurant_agent.bar_tool import BAR_REPORT_KEY
from restaurant_agent.cashier.rendering import GOODBYE, render_text
from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.memory.contracts import MemoryIntent
from restaurant_contracts.application import Action, ActorContext, ChatMessage
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
from restaurant_contracts.cashier import (
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    CashierReport,
    CashierTask,
    Receipt,
    served_lines,
)
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenStation,
    StationPlan,
    StationTask,
)
from restaurant_contracts.memory_store import SQLiteMemoryStore

from bff.local_waiter import LocalWaiter
from bff.scripted import ScriptedWaiterAgent
from bff.scripted_seating import ScriptedSeating
from bff.service import STALE_BILL, RestaurantService
from bff.storage import Database
from bff.waiter import RemoteWaiter, WaiterPayCall, WaiterPayResult, WaiterTurn

AT = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)
DRINKS = {
    "caña": ("cana-de-cerveza", "Caña de cerveza", "2.50"),
    "agua con gas": ("agua-con-gas", "Agua con gas", "2.20"),
}
PRICES = {
    "morcilla-de-burgos-a-la-brasa": Decimal("8.50"),
    **{carta_id: Decimal(price) for carta_id, _, price in DRINKS.values()},
}
_QUANTITY = {"una": 1, "un": 1, "otra": 1, "dos": 2, "tres": 3}
CELIAC = "Contiene cereales con gluten según la carta y has indicado celiaquía."


def morcilla(order_id: str) -> KitchenReport:
    item = AcceptedItem(
        line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa",
        quantity=1, station=KitchenStation.BRASA,
    )
    plan = KitchenPlan(
        order_id=order_id, accepted=[item],
        stations=[StationPlan(station=KitchenStation.BRASA, tasks=[StationTask(
            line=1, carta_id=item.carta_id, name=item.name, quantity=1)])],
    )
    order = KitchenOrder(order_id=order_id, lines=[KitchenOrderLine(line=1, name="morcilla a la brasa")])
    return KitchenReport(order=order, result=plan, text="Platos cocinados")


def drinks_round(round_id: str, text: str) -> BarReport | None:
    """The fake bar: «caña» and «agua con gas» are on the carta, a celiac gets no caña."""

    asked = [
        (_QUANTITY.get(match["count"].casefold(), 1), name)
        for name in DRINKS
        for match in re.finditer(rf"\b(?P<count>\w+)\s+{name}", text, re.IGNORECASE)
    ]
    if not asked:
        return None
    request = BarRequest(
        round_id=round_id,
        items=[BarItem(line=number, name=name, quantity=quantity) for number, (quantity, name) in enumerate(asked, 1)],
        restrictions=["celiaquía"] if "celíac" in text else [],
    )
    if "sin carta" in text:
        result = bar_failure(round_id, BarFailureCode.KNOWLEDGE_UNAVAILABLE)
        return BarReport(request=request, result=result, text=bar_text(result))
    served, rejected = [], []
    for item in request.items:
        carta_id, name, _ = DRINKS[item.name]
        if request.restrictions and carta_id == "cana-de-cerveza":
            rejected.append(RejectedDrink(
                line=item.line, requested=item.name, quantity=item.quantity, reason=CELIAC, carta_id=carta_id,
            ))
        else:
            served.append(ServedDrink(line=item.line, carta_id=carta_id, name=name, quantity=item.quantity))
    result = BarRound(round_id=round_id, served=served, rejected=rejected)
    return BarReport(request=request, result=result, text=bar_text(result))


def priced(request: BillRequest) -> Bill:
    lines = [
        BillLine(**line.model_dump(), unit_price=PRICES[line.carta_id], line_total=PRICES[line.carta_id] * line.quantity)
        for line in request.lines
    ]
    return Bill(
        bill_id=request.bill_id, lines=lines, total=sum((line.line_total for line in lines), Decimal(0)),
        sources=[BillSource(document="carta de la casa", version="1")],
    )


class BarWaiter:
    """The scripted waiter, plus a kitchen on «Pido…», a bar on drinks and a cashier on «la cuenta»."""

    def __init__(self, waiter: LocalWaiter) -> None:
        self._waiter = waiter
        self.mode = waiter.mode
        self.turns: list[WaiterTurn] = []
        self.bills: dict[str, Bill] = {}
        self.rounds = 0
        self.orders = 0
        # Whether «… y la cuenta» asks for the bill before serving the drinks.
        self.bill_first = False

    async def take_turn(self, turn: WaiterTurn):
        self.turns.append(turn)
        result = await self._waiter.take_turn(turn)
        text = turn.message.casefold()
        if text.startswith("pido") and "morcilla" in text:
            self.orders += 1
            result = replace(result, kitchen=morcilla(f"ko_{self.orders}"))
        round_id = f"bar_{self.rounds + 1}"
        bar = drinks_round(round_id, turn.message)
        if bar is not None:
            self.rounds += 1
            result = replace(result, bar=bar)
        if "la cuenta" in text and (turn.pending_bill is None or bar is not None) and not turn.orders_at_pass:
            fresh = [] if bar is None or self.bill_first else served_lines([bar], {round_id})
            lines = [*turn.served, *fresh]
            if lines:
                request = BillRequest(bill_id=f"bill_{len(self.bills) + 1}", lines=lines)
                bill = self.bills[request.bill_id] = priced(request)
                result = replace(result, cashier=CashierReport(
                    bill_id=request.bill_id, request=request, result=bill, text=render_text(bill),
                    task=CashierTask(task_id=f"task_{request.bill_id}", context_id="ctx_1"),
                ))
        return result

    async def pay_bill(self, call: WaiterPayCall) -> WaiterPayResult:
        bill = self.bills[call.bill.bill_id]
        receipt = Receipt(
            bill_id=bill.bill_id, version=1, method=call.method, amount=bill.total, reference="pay_1",
            paid_at=AT, idempotency_key=call.idempotency_key,
        )
        return WaiterPayResult(
            reply=GOODBYE,
            cashier=CashierReport(bill_id=bill.bill_id, result=receipt, text=render_text(receipt, paying=True),
                                  task=call.bill.task),
        )

    def __getattr__(self, name):
        return getattr(self._waiter, name)


@pytest.fixture
def seating(clock) -> ScriptedSeating:
    return ScriptedSeating(clock)


@pytest.fixture
def waiter(settings, seating) -> BarWaiter:
    memory_store = SQLiteMemoryStore(settings.bff_database_path.with_name("test-waiter-memory.db"))
    return BarWaiter(
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


async def seated(service, commands, name: str = "Ana"):
    session = service.authenticate(service.open_session(name).token)
    arrival = await service.submit(session, commands.arrive())
    await service.drain()
    conversation_id = arrival.conversation_id
    await say(service, commands, session, conversation_id, "Venimos tres")
    proposal = service.get_snapshot(session, conversation_id).seating.proposal
    await service.submit(session, commands.decide(conversation_id, proposal.proposal_id))
    return session, conversation_id


async def test_a_round_is_its_own_message_and_served_at_once(service, commands, waiter) -> None:
    session, conversation_id = await seated(service, commands)

    await say(service, commands, session, conversation_id, "Sí, dos caña")

    snapshot = service.get_snapshot(session, conversation_id)
    *_, order, bar, reply = snapshot.messages
    assert (order.role, bar.role, reply.role) == ("user", "bar", "assistant")
    assert bar.command_event_id == order.command_event_id == reply.command_event_id
    assert [(item.carta_id, item.quantity) for item in bar.bar.result.served] == [("cana-de-cerveza", 2)]
    assert bar.text.startswith("Barra\nServido:\n- 2 × Caña de cerveza")
    assert snapshot.served_orders == ["bar_1"]
    assert waiter.turns[-1].orders_at_pass == 0
    # Every other message keeps the shape the previous view reads.
    raw = json.loads(snapshot.model_dump_json())
    assert all("bar" not in message for message in raw["messages"] if message["role"] != "bar")


async def test_a_mixed_order_shows_the_chef_then_the_bar_then_the_waiter(service, commands) -> None:
    session, conversation_id = await seated(service, commands)

    result = await say(service, commands, session, conversation_id, "Pido una morcilla a la brasa y un agua con gas")

    snapshot = service.get_snapshot(session, conversation_id)
    turn = [message.role for message in snapshot.messages if message.command_event_id == result.event_id]
    assert turn == ["user", "kitchen", "bar", "assistant"]
    # The round at once; the dishes once the waiter takes them from the pass.
    assert snapshot.served_orders == ["bar_1", "ko_1"]


async def test_served_drinks_are_billed_and_a_drinks_only_customer_pays(service, commands, waiter) -> None:
    session, conversation_id = await seated(service, commands)
    await say(service, commands, session, conversation_id, "Sí, dos caña")

    await say(service, commands, session, conversation_id, "La cuenta, por favor")

    turn = waiter.turns[-1]
    assert [(line.order_id, line.line, line.carta_id, line.quantity) for line in turn.served] == [
        ("bar_1", 1, "cana-de-cerveza", 2),
    ]
    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.messages[-2].cashier.result.total == Decimal("5.00")
    assert snapshot.pending_bill.bill_id == "bill_1" and Action.DECIDE_PAYMENT in snapshot.allowed_actions

    assert (await service.submit(session, commands.pay(conversation_id, "bill_1"))).status == "completed"
    closed = service.get_snapshot(session, conversation_id)
    assert closed.visit_closed and closed.messages[-1].text == GOODBYE


async def test_rejected_or_failed_rounds_are_shown_but_never_billed(service, commands, waiter) -> None:
    session, conversation_id = await seated(service, commands)

    await say(service, commands, session, conversation_id, "Soy celíaco: una caña")
    await say(service, commands, session, conversation_id, "Un agua con gas, sin carta")

    snapshot = service.get_snapshot(session, conversation_id)
    rejected, failed = [message.bar for message in snapshot.messages if message.role == "bar"]
    assert rejected.result.rejected[0].reason == CELIAC
    assert failed.text == "La barra no ha podido servir las bebidas. La barra no puede consultar la carta ahora mismo."
    assert snapshot.served_orders == []
    await say(service, commands, session, conversation_id, "La cuenta, por favor")
    assert waiter.turns[-1].served == () and service.get_snapshot(session, conversation_id).pending_bill is None


async def test_new_drinks_supersede_the_pending_bill(service, commands, waiter) -> None:
    session, conversation_id = await seated(service, commands)
    await say(service, commands, session, conversation_id, "Sí, dos caña")
    await say(service, commands, session, conversation_id, "La cuenta, por favor")

    await say(service, commands, session, conversation_id, "Sí, otra caña")

    assert waiter.turns[-1].pending_bill.bill_id == "bill_1"
    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.pending_bill is None and Action.DECIDE_PAYMENT not in snapshot.allowed_actions
    stale = await service.submit(session, commands.pay(conversation_id, "bill_1"))
    assert stale.status == "failed" and stale.error.message == STALE_BILL

    await say(service, commands, session, conversation_id, "La cuenta, por favor")
    renewed = service.get_snapshot(session, conversation_id)
    assert renewed.pending_bill.bill_id == "bill_2"
    assert renewed.messages[-2].cashier.result.total == Decimal("7.50")


async def test_a_bill_of_the_same_turn_stays_only_if_it_includes_the_new_drinks(service, commands, waiter) -> None:
    session, conversation_id = await seated(service, commands)
    await say(service, commands, session, conversation_id, "Sí, dos caña")
    await say(service, commands, session, conversation_id, "La cuenta, por favor")

    await say(service, commands, session, conversation_id, "Sí, otra caña y la cuenta")

    snapshot = service.get_snapshot(session, conversation_id)
    *_, order, bar, cashier, reply = snapshot.messages
    assert [message.role for message in (order, bar, cashier, reply)] == ["user", "bar", "cashier", "assistant"]
    assert snapshot.pending_bill.bill_id == "bill_2" and snapshot.pending_bill.total == Decimal("7.50")

    waiter.bill_first = True
    await say(service, commands, session, conversation_id, "Sí, otra caña y la cuenta")
    assert service.get_snapshot(session, conversation_id).pending_bill is None


async def test_only_kitchen_orders_wait_at_the_pass(service, commands, settings) -> None:
    session = service.authenticate(service.open_session("Ana").token)
    conversation_id = (await service.submit(session, commands.arrive())).conversation_id
    database = Database(settings.bff_database_path)
    bar = drinks_round("bar_1", "dos caña")
    kitchen = morcilla("ko_1")
    with database.write() as tx:
        for number, (role, field, report) in enumerate((("kitchen", "kitchen", kitchen), ("bar", "bar", bar))):
            tx.add_message(conversation_id, ChatMessage(
                message_id=f"msg_{number}", role=role, text=report.text, occurred_at=AT,
                command_event_id=f"cmd_{number}", **{field: report},
            ))
        tx.mark_served(conversation_id, "bar_1", AT)
    with database.read() as tx:
        served, at_pass, pending = RestaurantService._billing(tx, conversation_id)

    assert [line.order_id for line in served] == ["bar_1"] and at_pass == 1 and pending is None


async def test_the_remote_waiter_reads_the_round() -> None:
    bar = drinks_round("bar_1", "dos caña")

    def handle(request: httpx.Request) -> httpx.Response:
        body = {
            "status": "completed", "reply": "Aquí tenéis las cañas.", "customer": {"presented_name": "Ana"},
            "order_draft": {"items": []}, "turn_count": 2, "bar": bar.model_dump(mode="json"),
        }
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(body)}]}],
        })

    waiter = RemoteWaiter("http://waiter.invalid")
    waiter._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    result = await waiter.take_turn(WaiterTurn(
        conversation_id="conv_1", actor=ActorContext(actor_id="ana", authenticated=True), presented_name="Ana",
        message="Sí", customer=CustomerSnapshot(presented_name="Ana"), order_draft=OrderDraft(), turn_count=1,
        persisted_order_preferences=(), session_json=None, correlation_id="corr_1",
    ))
    await waiter.aclose()

    assert result.bar == bar and result.kitchen is None and result.cashier is None


class ServingAgent(ScriptedWaiterAgent):
    """A scripted model whose servir_bebidas call left a round in the session, like the real tool."""

    async def run(self, messages, *, session, options):
        session.state[BAR_REPORT_KEY] = drinks_round("bar_1", "dos caña").model_dump(mode="json")
        return SimpleNamespace(value=WaiterModelResult(
            reply="Aquí tenéis las dos cañas.", customer=CustomerSnapshot(presented_name="Ana"),
            memory_intent=MemoryIntent.NONE,
        ))


async def test_the_local_waiter_passes_the_round_and_drops_it_from_the_session(settings) -> None:
    memory_store = SQLiteMemoryStore(settings.bff_database_path.with_name("test-waiter-memory.db"))
    local = LocalWaiter(ServingAgent(), mode="scripted", max_turns=20, memory_store=memory_store)

    result = await local.take_turn(WaiterTurn(
        conversation_id="conv_1", actor=ActorContext(actor_id="ana", authenticated=True), presented_name="Ana",
        message="Sí", customer=CustomerSnapshot(presented_name="Ana"), order_draft=OrderDraft(), turn_count=0,
        persisted_order_preferences=(), session_json=None, correlation_id="corr_1",
    ))

    assert isinstance(result.bar.result, BarRound) and result.bar.result.served[0].quantity == 2
    assert BAR_REPORT_KEY not in (result.session_json or "")
