"""Caja v1 from the waiter: the bill tool, the A2A cashier port and the payment.

The waiter runs with a scripted model and a fake cashier port; the port runs
against a fake transport and against a live A2A stub of the cashier built
with the A2A SDK, which speaks the same protocol as ``agents/cashier``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn
from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part, TaskState
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError
from starlette.applications import Starlette

from restaurant_agent.activity import recording
from restaurant_agent.agent import create_waiter_agent
from restaurant_agent.cashier import A2ACashier
from restaurant_agent.cashier.port import CashierReply
from restaurant_agent.cashier.rendering import BILL_CLOSED, GOODBYE, PAY_AGAIN, render_text
from restaurant_agent.cashier_tool import (
    ALREADY_ANSWERED,
    BILL_PENDING,
    BILLING_KEY,
    CASHIER_REPORT_KEY,
    CASHIER_TOOL,
    NOTHING_SERVED,
    BillingContext,
    create_cashier_tool,
)
from restaurant_agent.config import Settings
from restaurant_agent.conversation import ConversationManager
from restaurant_agent.remote import RemoteWaiterService, create_server
from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.application import ActorContext
from restaurant_contracts.cashier import (
    CASHIER_INPUT_ADAPTER,
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    CashierAnswer,
    CashierFailure,
    CashierFailureCode,
    CashierTask,
    PaymentChoice,
    PaymentMethod,
    PendingBill,
    Receipt,
    ServedLine,
    cashier_failure,
)
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenStation,
    StationPlan,
    StationTask,
)
from restaurant_contracts.waiter import (
    WAITER_PAY_RESPONSE_ADAPTER,
    WAITER_TURN_RESPONSE_ADAPTER,
    WaiterPayRequest,
    WaiterTurnRequest,
)
from scripted_model import ScriptedModel, say
from seating_stub import free_port

AT = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
TASK = CashierTask(task_id="task_1", context_id="ctx_1")
SERVED = [
    ServedLine(order_id="ko_1", line=1, carta_id="morcilla-de-burgos-a-la-brasa",
               name="Morcilla de Burgos a la brasa", quantity=1),
    ServedLine(order_id="ko_1", line=2, carta_id="croquetas-de-morcilla",
               name="Croquetas de morcilla", quantity=1),
]
PRICES = {"morcilla-de-burgos-a-la-brasa": Decimal("8.50"), "croquetas-de-morcilla": Decimal("9.00")}


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "azure_ai_model_deployment_name": "test-model",
        "memory_database_path": Path("/tmp/unused-memory.db"),
        "cashier_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


def priced(request: BillRequest, *, lines: list[ServedLine] | None = None) -> Bill:
    billed = [
        BillLine(**line.model_dump(), unit_price=PRICES[line.carta_id],
                 line_total=PRICES[line.carta_id] * line.quantity)
        for line in (lines or request.lines)
    ]
    return Bill(
        bill_id=request.bill_id,
        lines=billed,
        total=sum((line.line_total for line in billed), Decimal(0)),
        sources=[BillSource(document="carta de la casa", version="1")],
    )


def receipt(bill_id: str = "bill_1", key: str = "evt_1") -> Receipt:
    return Receipt(bill_id=bill_id, version=1, method=PaymentMethod.CARD, amount=Decimal("17.50"),
                   reference="pay_1", paid_at=AT, idempotency_key=key)


class FakeCashier:
    """A cashier port that records what it is asked and prices from fixed carta prices."""

    def __init__(self, *, lines: list[ServedLine] | None = None) -> None:
        self.requests: list[BillRequest] = []
        self.payments: list[tuple[PendingBill, PaymentChoice]] = []
        self.cancelled: list[PendingBill] = []
        self._lines = lines
        self.paid: Receipt | CashierFailure | None = None

    async def present(self, request: BillRequest) -> CashierAnswer:
        self.requests.append(request)
        return CashierAnswer(result=priced(request, lines=self._lines), task=TASK)

    async def pay(self, pending: PendingBill, choice: PaymentChoice) -> CashierAnswer:
        self.payments.append((pending, choice))
        return CashierAnswer(result=self.paid or receipt(pending.bill_id, choice.idempotency_key), task=pending.task)

    async def cancel(self, pending: PendingBill) -> bool:
        self.cancelled.append(pending)
        return True


def ask_bill(call_id: str = "call_bill", **arguments: object) -> tuple[str, str, dict]:
    return (call_id, CASHIER_TOOL, dict(arguments))


def function_results(model: ScriptedModel, call: int) -> list[str]:
    return [
        text
        for _, contents in model.calls[call]["messages"]
        for kind, _, _, text in contents
        if kind == "function_result"
    ]


def waiter(model: ScriptedModel, port: FakeCashier | None = None) -> ConversationManager:
    manager = ConversationManager(create_waiter_agent(settings(), client=model, cashier=port))
    manager.restore_conversation(conversation_id="conv_1", actor_id="ana", presented_name="Ana")
    return manager


async def ask(manager: ConversationManager, billing: BillingContext | None, message: str = "La cuenta, por favor"):
    return await manager.send_message(
        conversation_id="conv_1", actor_id="ana", message=message, billing=billing
    )


# The tool


async def test_the_bill_holds_exactly_the_served_lines_and_the_model_chooses_nothing() -> None:
    port = FakeCashier()
    model = ScriptedModel([{"calls": [ask_bill()]}, say("Aquí tenéis la cuenta: 17,50 €.")])
    manager = waiter(model, port)

    response = await ask(manager, BillingContext(served=SERVED))

    [request] = port.requests
    assert request.lines == SERVED and request.bill_id.startswith("bill_")
    assert "Ana" not in request.model_dump_json()
    report = response.cashier
    assert report is not None and report.request == request and report.task == TASK
    assert isinstance(report.result, Bill) and report.result.total == Decimal("17.50")
    assert report.text.startswith("Cuenta\n- 1 × Morcilla de Burgos a la brasa: 8,50 € = 8,50 €")
    [answer] = function_results(model, 1)
    assert answer.startswith("bill_presented: total 17,50 €\nCuenta")
    assert "No calcules" in answer and "nunca paga" in answer
    assert CASHIER_TOOL in model.calls[0]["tools"]
    schema = create_cashier_tool(port).parameters()
    assert (schema["properties"], schema["additionalProperties"]) == ({}, False)
    state = manager.export_conversation(conversation_id="conv_1", actor_id="ana").agent_session.state
    assert BILLING_KEY not in state and CASHIER_REPORT_KEY not in state


async def test_arguments_from_the_model_never_reach_the_bill() -> None:
    port = FakeCashier()
    model = ScriptedModel([
        {"calls": [ask_bill(lines=[{"carta_id": "chuletillas-de-lechazo-a-la-brasa"}])]},
        say("Hecho."),
    ])

    response = await ask(waiter(model, port), BillingContext(served=SERVED))

    assert port.requests == []
    assert response.cashier is None
    assert "chuletillas" not in json.dumps(function_results(model, 1))


@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        (BillingContext(served=SERVED, orders_at_pass=1), "dishes_at_pass: hay 1 pedido de cocina en el pase"),
        (BillingContext(served=SERVED, orders_at_pass=2), "dishes_at_pass: hay 2 pedidos de cocina en el pase"),
        (BillingContext(), NOTHING_SERVED),
        (None, NOTHING_SERVED),
        (BillingContext(served=SERVED, pending_bill=PendingBill(bill_id="bill_0", version=1, task=TASK)), BILL_PENDING),
    ],
)
async def test_the_tool_refuses_to_bill_dishes_at_the_pass_nothing_or_twice(billing, expected) -> None:
    port = FakeCashier()
    model = ScriptedModel([{"calls": [ask_bill()]}, say("Primero os sirvo.")])

    response = await ask(waiter(model, port), billing)

    assert port.requests == [] and response.cashier is None
    [answer] = function_results(model, 1)
    assert answer.startswith(expected)


async def test_the_cashier_is_asked_once_per_turn_even_in_parallel() -> None:
    port = FakeCashier()
    model = ScriptedModel([
        {"calls": [ask_bill("b1"), ask_bill("b2")]}, {"calls": [ask_bill("b3")]}, say("La cuenta."),
    ])

    response = await ask(waiter(model, port), BillingContext(served=SERVED))

    assert len(port.requests) == 1
    assert sorted(function_results(model, 1)).count(ALREADY_ANSWERED) == 1
    assert function_results(model, 2)[-1] == ALREADY_ANSWERED
    assert response.cashier.request == port.requests[0]


async def test_a_bill_for_other_lines_is_refused_and_its_task_cancelled() -> None:
    port = FakeCashier(lines=SERVED[:1])
    model = ScriptedModel([{"calls": [ask_bill()]}, say("Caja no ha podido.")])

    response = await ask(waiter(model, port), BillingContext(served=SERVED))

    assert response.cashier.result.code is CashierFailureCode.INVALID_BILL
    assert response.cashier.task is None
    assert [pending.task for pending in port.cancelled] == [TASK]
    assert function_results(model, 1)[0].startswith("cashier_failed: invalid_bill\nCaja no ha podido preparar la cuenta")


async def test_without_a_cashier_endpoint_the_waiter_gets_an_explicit_failure() -> None:
    model = ScriptedModel([{"calls": [ask_bill()]}, say("Caja no está configurada.")])
    manager = ConversationManager(create_waiter_agent(settings(), client=model))
    manager.restore_conversation(conversation_id="conv_1", actor_id="ana", presented_name="Ana")

    response = await ask(manager, BillingContext(served=SERVED))

    assert response.cashier.result.code is CashierFailureCode.CASHIER_NOT_CONFIGURED
    assert function_results(model, 1)[0].startswith("cashier_failed: cashier_not_configured")


def test_the_instructions_keep_the_bill_rules() -> None:
    from restaurant_agent.agent import load_instructions

    rules = " ".join(load_instructions().split())
    assert "pedir_la_cuenta" in rules and "Un mensaje nunca paga" in rules
    assert "No calcules, sumes ni cambies importes" in rules
    assert "todavía no hay nada servido que cobrar" in rules
    assert "las bebidas aún no se cobran" not in rules


# The A2A port


class FakeTransport:
    def __init__(self, *replies: CashierReply | Exception, delay: float = 0) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[str, CashierTask | None]] = []
        self.read: list[CashierTask] = []
        self.delay = delay

    def _next(self) -> CashierReply:
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def send(self, payload: str, task: CashierTask | None = None) -> CashierReply:
        self.sent.append((payload, task))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._next()

    async def get(self, task: CashierTask) -> CashierReply:
        self.read.append(task)
        return self._next()

    async def cancel(self, task: CashierTask) -> bool:
        return True


REQUEST = BillRequest(bill_id="bill_1", lines=SERVED)
PENDING = PendingBill(bill_id="bill_1", version=1, task=TASK)
CHOICE = PaymentChoice(bill_id="bill_1", version=1, method=PaymentMethod.CARD, idempotency_key="evt_1")
WAITING = TaskState.TASK_STATE_INPUT_REQUIRED
DONE = TaskState.TASK_STATE_COMPLETED


def reply(state: TaskState, *results, task: CashierTask | None = TASK) -> CashierReply:
    return CashierReply(task=task, state=state, results=tuple(result.model_dump_json() for result in results))


async def test_input_required_is_a_presented_bill_that_keeps_its_task() -> None:
    transport = FakeTransport(reply(WAITING, priced(REQUEST)))

    answer = await A2ACashier(settings(), transport=transport).present(REQUEST)

    assert isinstance(answer.result, Bill) and answer.task == TASK
    [(payload, task)] = transport.sent
    assert task is None and CASHIER_INPUT_ADAPTER.validate_json(payload) == REQUEST


@pytest.mark.parametrize(
    ("transport_reply", "code"),
    [
        (reply(DONE, cashier_failure("bill_1", CashierFailureCode.PRICE_MISSING)), CashierFailureCode.PRICE_MISSING),
        (reply(DONE, priced(REQUEST)), CashierFailureCode.INVALID_BILL),
        (reply(WAITING, priced(BillRequest(bill_id="bill_2", lines=SERVED))), CashierFailureCode.INVALID_BILL),
        (reply(TaskState.TASK_STATE_FAILED), CashierFailureCode.CASHIER_UNAVAILABLE),
        (InvalidParamsError("bad"), CashierFailureCode.CASHIER_UNAVAILABLE),
    ],
)
async def test_every_other_answer_is_an_explicit_failure(transport_reply, code) -> None:
    answer = await A2ACashier(settings(), transport=FakeTransport(transport_reply)).present(REQUEST)

    assert isinstance(answer.result, CashierFailure) and answer.result.code is code
    assert answer.task is None


async def test_the_cashier_call_is_bounded_and_needs_an_endpoint() -> None:
    slow = A2ACashier(settings(cashier_timeout_seconds=1), transport=FakeTransport(reply(WAITING, priced(REQUEST)), delay=3))
    assert (await slow.present(REQUEST)).result.code is CashierFailureCode.TIMEOUT
    missing = A2ACashier(settings())
    assert (await missing.present(REQUEST)).result.code is CashierFailureCode.CASHIER_NOT_CONFIGURED
    assert (await missing.pay(PENDING, CHOICE)).result.code is CashierFailureCode.CASHIER_NOT_CONFIGURED
    assert await missing.cancel(PENDING) is False


async def test_the_payment_resumes_the_same_task_and_returns_its_receipt() -> None:
    transport = FakeTransport(reply(WAITING, priced(REQUEST)), reply(DONE, receipt()))

    answer = await A2ACashier(settings(), transport=transport).pay(PENDING, CHOICE)

    assert answer.result == receipt() and answer.task == TASK
    [(payload, task)] = transport.sent
    assert task == TASK and CASHIER_INPUT_ADAPTER.validate_json(payload) == CHOICE


async def test_a_completed_task_returns_its_stored_receipt_without_paying_again() -> None:
    transport = FakeTransport(reply(DONE, priced(REQUEST), receipt()))

    answer = await A2ACashier(settings(), transport=transport).pay(PENDING, CHOICE)

    assert answer.result == receipt() and transport.sent == []


async def test_a_payment_racing_another_reads_the_receipt_the_other_left() -> None:
    transport = FakeTransport(
        reply(WAITING, priced(REQUEST)), InvalidParamsError("terminal state"), reply(DONE, priced(REQUEST), receipt()),
    )

    answer = await A2ACashier(settings(), transport=transport).pay(PENDING, CHOICE)

    assert answer.result == receipt() and len(transport.read) == 2


@pytest.mark.parametrize(
    "first", [TaskNotFoundError("gone"), reply(TaskState.TASK_STATE_CANCELED, priced(REQUEST))]
)
async def test_a_bill_the_cashier_no_longer_has_cannot_be_paid(first) -> None:
    answer = await A2ACashier(settings(), transport=FakeTransport(first)).pay(PENDING, CHOICE)

    assert answer.result.code is CashierFailureCode.BILL_NOT_FOUND


# A live A2A cashier stub, the same protocol as agents/cashier


class StubCashierExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.charges = 0
        self.receipts: dict[str, Receipt] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        received = CASHIER_INPUT_ADAPTER.validate_json(context.message.parts[0].text)
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, context.context_id)
        step = ActivityStep(step_id=f"c-{uuid.uuid4().hex[:6]}", component="caja", label="Caja: consulta precios en la carta")
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message([Part(text=json.dumps({"activity": step.model_dump(mode="json")}))]),
        )
        if isinstance(received, BillRequest):
            await updater.add_artifact(parts=[Part(text=priced(received).model_dump_json())], artifact_id="bill")
            await updater.requires_input(message=updater.new_agent_message([Part(text="Cuenta presentada.")]))
            return
        self.charges += 1
        paid = self.receipts.setdefault(received.bill_id, receipt(received.bill_id, received.idempotency_key))
        await updater.add_artifact(parts=[Part(text=paid.model_dump_json())], artifact_id="receipt")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class RunningCashier:
    def __init__(self) -> None:
        self.port = free_port()
        self.executor = StubCashierExecutor()
        card = AgentCard(
            name="Caja", description="Stub", version="1.0",
            default_input_modes=["text"], default_output_modes=["text"],
            capabilities=AgentCapabilities(streaming=True),
            supported_interfaces=[AgentInterface(url=f"{self.url}/", protocol_binding="JSONRPC")],
            skills=[AgentSkill(id="cobrar", name="Cobrar", description="Stub", tags=["cashier"])],
        )
        handler = DefaultRequestHandler(agent_executor=self.executor, task_store=InMemoryTaskStore(), agent_card=card)
        app = Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")])
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> RunningCashier:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("stub A2A cashier did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *args: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


async def test_the_waiter_reaches_a_live_a2a_cashier_and_relays_its_steps() -> None:
    steps: list[ActivityStep] = []
    with RunningCashier() as running:
        cashier = A2ACashier(settings(cashier_a2a_url=running.url))
        with recording(steps.append):
            presented = await cashier.present(REQUEST)
        assert isinstance(presented.result, Bill) and presented.task is not None
        pending = PendingBill(bill_id="bill_1", version=1, task=presented.task)

        first = await cashier.pay(pending, CHOICE)
        again = await cashier.pay(pending, CHOICE.model_copy(update={"idempotency_key": "evt_2"}))

        assert first.result == again.result == receipt()
        assert running.executor.charges == 1

        other = await cashier.present(REQUEST.model_copy(update={"bill_id": "bill_2"}))
        cancelled = PendingBill(bill_id="bill_2", version=1, task=other.task)
        assert await cashier.cancel(cancelled) is True
        assert await cashier.cancel(cancelled) is False
        assert (await cashier.pay(cancelled, CHOICE.model_copy(update={"bill_id": "bill_2"}))).result.code is (
            CashierFailureCode.BILL_NOT_FOUND
        )
    assert [step.label for step in steps] == ["Caja: consulta precios en la carta"]


# The remote waiter


def turn(**changes: object) -> WaiterTurnRequest:
    values: dict[str, object] = {
        "conversation_id": "conv_1", "actor": ActorContext(actor_id="ana", authenticated=True),
        "presented_name": "Ana", "message": "La cuenta, por favor",
        "customer": CustomerSnapshot(presented_name="Ana"), "order_draft": OrderDraft(),
        "turn_count": 0, "correlation_id": "corr_1", "visit_id": "visit_1",
    }
    values.update(changes)
    return WaiterTurnRequest(**values)


def service(tmp_path, model: ScriptedModel, port: FakeCashier, kitchen=None) -> RemoteWaiterService:
    return RemoteWaiterService(
        settings(memory_database_path=tmp_path / "memory.db"),
        agent_factory=lambda configured, memory_store: create_waiter_agent(
            configured, memory_store=memory_store, client=model, kitchen=kitchen, cashier=port
        ),
        cashier=port,
    )


async def test_the_remote_turn_carries_the_bill_and_never_its_billing_context(tmp_path) -> None:
    port = FakeCashier()
    model = ScriptedModel([{"calls": [ask_bill()]}, say("Aquí tenéis la cuenta.")])

    result = await service(tmp_path, model, port).take_turn(turn(served=SERVED))

    parsed = WAITER_TURN_RESPONSE_ADAPTER.validate_json(result.model_dump_json())
    assert parsed.cashier is not None and parsed.cashier.pending == PendingBill(
        bill_id=port.requests[0].bill_id, version=1, task=TASK
    )
    assert BILLING_KEY not in parsed.session_json and CASHIER_REPORT_KEY not in parsed.session_json


class CookingKitchen:
    async def plan(self, order: KitchenOrder) -> KitchenPlan:
        item = AcceptedItem(line=1, carta_id="croquetas-de-morcilla", name="Croquetas de morcilla",
                            quantity=1, station=KitchenStation.FRITOS)
        return KitchenPlan(
            order_id=order.order_id, accepted=[item],
            stations=[StationPlan(station=KitchenStation.FRITOS, tasks=[StationTask(
                line=1, carta_id=item.carta_id, name=item.name, quantity=1)])],
        )


async def test_ordering_more_dishes_cancels_the_pending_bill(tmp_path) -> None:
    port = FakeCashier()
    model = ScriptedModel([
        {"calls": [("call_kitchen", "pedir_a_cocina", {"items": [{"name": "croquetas de morcilla"}]})]},
        say("Marchan unas croquetas más."),
    ])
    pending = PendingBill(bill_id="bill_0", version=1, task=TASK)
    steps: list[ActivityStep] = []

    with recording(steps.append):
        result = await service(tmp_path, model, port, kitchen=CookingKitchen()).take_turn(
            turn(message="Unas croquetas más", served=SERVED, pending_bill=pending)
        )

    assert result.kitchen is not None and port.cancelled == [pending]
    labels = [(step.label, step.status) for step in steps if step.component == "caja"]
    assert labels[-1] == ("Caja: anula la cuenta pendiente", "done")


async def test_a_turn_without_new_dishes_keeps_the_pending_bill(tmp_path) -> None:
    port = FakeCashier()
    model = ScriptedModel([{"calls": [ask_bill()]}, say("Pagad con los botones.")])
    pending = PendingBill(bill_id="bill_0", version=1, task=TASK)

    result = await service(tmp_path, model, port).take_turn(turn(served=SERVED, pending_bill=pending))

    assert port.cancelled == [] and port.requests == [] and result.cashier is None
    assert function_results(model, 1) == [BILL_PENDING]


def pay_request(**changes: object) -> WaiterPayRequest:
    values: dict[str, object] = {
        "conversation_id": "conv_1", "actor": ActorContext(actor_id="ana", authenticated=True),
        "correlation_id": "corr_2", "bill": PENDING, "method": PaymentMethod.CARD,
        "idempotency_key": "evt_1",
    }
    values.update(changes)
    return WaiterPayRequest(**values)


async def test_paying_relays_the_button_without_the_model_and_says_goodbye(tmp_path) -> None:
    def no_model(*args, **kwargs):
        raise AssertionError("Paying never calls the model")

    port = FakeCashier()
    service = RemoteWaiterService(
        settings(memory_database_path=tmp_path / "memory.db"), agent_factory=no_model, cashier=port
    )

    paid = await service.pay_bill(pay_request())

    [(pending, choice)] = port.payments
    assert pending == PENDING
    assert (choice.method, choice.idempotency_key, choice.version) == (PaymentMethod.CARD, "evt_1", 1)
    assert paid.reply == GOODBYE
    assert paid.cashier.result == receipt() and paid.cashier.task == TASK
    assert paid.cashier.text.startswith("Pagado con tarjeta: 17,50 €.")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (CashierFailureCode.BILL_NOT_FOUND, BILL_CLOSED),
        (CashierFailureCode.CASHIER_UNAVAILABLE, PAY_AGAIN),
        (CashierFailureCode.TIMEOUT, PAY_AGAIN),
        (CashierFailureCode.NOT_PAYABLE, PAY_AGAIN),
    ],
)
async def test_a_failed_payment_never_says_goodbye(tmp_path, code, expected) -> None:
    port = FakeCashier()
    port.paid = cashier_failure("bill_1", code)
    service = RemoteWaiterService(settings(memory_database_path=tmp_path / "memory.db"), cashier=port)

    paid = await service.pay_bill(pay_request())

    assert paid.reply == expected
    assert paid.cashier.text.startswith("Caja no ha podido cobrar.")


def test_the_responses_endpoint_dispatches_the_payment(tmp_path, monkeypatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    port = FakeCashier()
    server = create_server(
        settings(memory_database_path=tmp_path / "memory.db"),
        service=RemoteWaiterService(settings(memory_database_path=tmp_path / "memory.db"), cashier=port),
    )

    with TestClient(server) as client:
        response = client.post(
            "/responses",
            json={"model": "restaurant", "input": pay_request().model_dump_json(),
                  "conversation": {"id": "conv_1"}, "store": False, "stream": False},
            headers={"x-agent-user-id": "ana"},
        )

    output = response.json()["output"][0]["content"][0]["text"]
    paid = WAITER_PAY_RESPONSE_ADAPTER.validate_json(output)
    assert paid.reply == GOODBYE and isinstance(paid.cashier.result, Receipt)


def test_the_text_lists_lines_total_note_and_sources() -> None:
    text = render_text(priced(REQUEST))

    assert text.splitlines() == [
        "Cuenta",
        "- 1 × Morcilla de Burgos a la brasa: 8,50 € = 8,50 €",
        "- 1 × Croquetas de morcilla: 9,00 € = 9,00 €",
        "Total: 17,50 €",
        "Solo lo ya servido: platos de cocina y bebidas de la barra.",
        "Pago: tarjeta o efectivo.",
        "Fuentes: carta de la casa (versión 1).",
    ]
    assert render_text(receipt()).splitlines() == [
        "Pagado con tarjeta: 17,50 €.",
        "Referencia pay_1 · 06/10/2026 21:30 UTC",
    ]
