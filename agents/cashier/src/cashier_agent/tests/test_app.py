from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import uvicorn
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)
from a2a.utils.errors import A2AError
from pydantic import BaseModel
from starlette.testclient import TestClient

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.cashier import (
    CASHIER_RESULT_ADAPTER,
    Bill,
    BillRequest,
    BillStage,
    CashierFailure,
    CashierFailureCode,
    PaymentChoice,
    PaymentMethod,
    Receipt,
    ReviewDecision,
)

from cashier_agent.app import create_agent_card, create_app
from cashier_agent.config import Settings
from cashier_agent.evidence import parse
from cashier_agent.ledger import Ledger
from cashier_agent.pricing import price_bill

from carta_fixtures import CARTA, SERVED, TOTAL


def settings(port: int = 8090, **changes: Any) -> Settings:
    return Settings(
        _env_file=None,
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_model_deployment_name="test-model",
        cashier_a2a_public_url=f"http://127.0.0.1:{port}/",
        **changes,
    )


class FakePricing:
    """Prices with the real code from a fixed carta retrieval, without a model."""

    def __init__(self, stage: BillStage = BillStage.AWAITING_PAYMENT, carta: str = CARTA) -> None:
        self._stage = stage
        self._carta = carta

    async def price(self, request: BillRequest) -> Bill | CashierFailure:
        return price_bill(request, parse([self._carta]), stage=self._stage)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class RunningCashier:
    def __init__(self, pricing: FakePricing, ledger: Ledger) -> None:
        self.port = free_port()
        app = create_app(settings(self.port), service=pricing, ledger=ledger)
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> RunningCashier:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the cashier did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *args: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()


@pytest.fixture
def cashier(ledger: Ledger) -> Iterator[RunningCashier]:
    with RunningCashier(FakePricing(), ledger) as running:
        yield running


class Exchange:
    def __init__(self) -> None:
        self.task_id = ""
        self.context_id = ""
        self.states: list[TaskState] = []
        self.results: list[Bill | Receipt | CashierFailure] = []
        self.steps: list[ActivityStep] = []
        self.messages: list[str] = []

    @property
    def state(self) -> TaskState:
        return self.states[-1]


async def send(url: str, value: BaseModel, *, task_id: str = "", context_id: str = "") -> Exchange:
    exchange = Exchange()
    async with httpx.AsyncClient(timeout=10) as http:
        client = await ClientFactory(
            ClientConfig(streaming=True, httpx_client=http, accepted_output_modes=["text"])
        ).create_from_url(url)
        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            task_id=task_id,
            context_id=context_id,
            parts=[Part(text=value.model_dump_json())],
        )
        request = SendMessageRequest(
            message=message, configuration=SendMessageConfiguration(accepted_output_modes=["text"])
        )
        try:
            async for event in client.send_message(request):
                kind = event.WhichOneof("payload")
                if kind == "task":
                    exchange.task_id, exchange.context_id = event.task.id, event.task.context_id
                    exchange.states.append(event.task.status.state)
                elif kind == "status_update":
                    status = event.status_update.status
                    exchange.task_id = event.status_update.task_id
                    exchange.context_id = event.status_update.context_id
                    exchange.states.append(status.state)
                    for part in status.message.parts:
                        if part.text.startswith('{"activity"'):
                            exchange.steps.append(
                                ActivityStep.model_validate(json.loads(part.text)["activity"])
                            )
                        elif part.text:
                            exchange.messages.append(part.text)
                elif kind == "artifact_update":
                    for part in event.artifact_update.artifact.parts:
                        exchange.results.append(CASHIER_RESULT_ADAPTER.validate_json(part.text))
        finally:
            await client.close()
    return exchange


async def get_task(url: str, task_id: str) -> Any:
    async with httpx.AsyncClient(timeout=10) as http:
        client = await ClientFactory(ClientConfig(streaming=True, httpx_client=http)).create_from_url(url)
        try:
            return await client.get_task(GetTaskRequest(id=task_id))
        finally:
            await client.close()


async def cancel(url: str, task_id: str) -> Any:
    async with httpx.AsyncClient(timeout=10) as http:
        client = await ClientFactory(ClientConfig(streaming=True, httpx_client=http)).create_from_url(url)
        try:
            return await client.cancel_task(CancelTaskRequest(id=task_id))
        finally:
            await client.close()


def choice(method: PaymentMethod = PaymentMethod.CARD, key: str = "evt_1") -> PaymentChoice:
    return PaymentChoice(bill_id="bill_1", version=1, method=method, idempotency_key=key)


def test_agent_card_publishes_the_cobrar_skill_and_health() -> None:
    card = create_agent_card(settings())
    assert card.name == "Caja" and card.version == "1.0"
    assert [skill.id for skill in card.skills] == ["cobrar"]
    assert card.supported_interfaces[0].url == "http://127.0.0.1:8090/"
    client = TestClient(create_app(settings(), service=FakePricing(), ledger=Ledger()))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/.well-known/agent-card.json").json()["name"] == "Caja"


async def test_the_bill_waits_for_input_and_the_payment_resumes_and_completes_it(
    cashier: RunningCashier, ledger: Ledger
) -> None:
    presented = await send(cashier.url, SERVED)

    assert presented.state == TaskState.TASK_STATE_INPUT_REQUIRED
    (bill,) = presented.results
    assert isinstance(bill, Bill) and bill.total == TOTAL
    assert bill.payment_options == [PaymentMethod.CARD, PaymentMethod.CASH]
    assert presented.messages[-1].startswith("Cuenta presentada")

    paid = await send(
        cashier.url, choice(), task_id=presented.task_id, context_id=presented.context_id
    )

    assert paid.task_id == presented.task_id
    assert paid.state == TaskState.TASK_STATE_COMPLETED
    (receipt,) = paid.results
    assert isinstance(receipt, Receipt)
    assert (receipt.method, receipt.amount, receipt.idempotency_key) == (
        PaymentMethod.CARD, Decimal("17.50"), "evt_1",
    )
    assert [(step.label, step.status) for step in paid.steps] == [
        ("Caja: cobra con tarjeta", "running"),
        ("Caja: cobra con tarjeta", "done"),
    ]
    assert ledger.charges == 1


async def test_a_completed_task_keeps_its_receipt_and_never_charges_twice(
    cashier: RunningCashier, ledger: Ledger
) -> None:
    presented = await send(cashier.url, SERVED)
    paid = await send(
        cashier.url, choice(), task_id=presented.task_id, context_id=presented.context_id
    )

    with pytest.raises(A2AError, match="terminal state"):
        await send(
            cashier.url,
            choice(PaymentMethod.CASH, "evt_2"),
            task_id=presented.task_id,
            context_id=presented.context_id,
        )
    task = await get_task(cashier.url, presented.task_id)
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    stored = [
        CASHIER_RESULT_ADAPTER.validate_json(part.text)
        for artifact in task.artifacts
        for part in artifact.parts
    ]
    assert stored[-1] == paid.results[-1]
    assert ledger.charges == 1


async def test_a_payment_the_bill_does_not_allow_keeps_the_bill_waiting(
    cashier: RunningCashier, ledger: Ledger
) -> None:
    presented = await send(cashier.url, SERVED)
    stale = PaymentChoice(bill_id="bill_1", version=2, method=PaymentMethod.CARD, idempotency_key="evt_1")

    refused = await send(cashier.url, stale, task_id=presented.task_id, context_id=presented.context_id)

    assert refused.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert refused.results[-1].code is CashierFailureCode.NOT_PAYABLE
    assert ledger.charges == 0
    paid = await send(cashier.url, choice(), task_id=presented.task_id, context_id=presented.context_id)
    assert paid.state == TaskState.TASK_STATE_COMPLETED


async def test_a_cancelled_bill_can_no_longer_be_paid(cashier: RunningCashier, ledger: Ledger) -> None:
    presented = await send(cashier.url, SERVED)

    cancelled = await cancel(cashier.url, presented.task_id)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    with pytest.raises(A2AError):
        await send(cashier.url, choice(), task_id=presented.task_id, context_id=presented.context_id)
    assert ledger.pay(choice()).code is CashierFailureCode.BILL_NOT_FOUND
    assert ledger.charges == 0


async def test_a_bill_the_carta_cannot_price_completes_with_an_explicit_failure(ledger: Ledger) -> None:
    with RunningCashier(FakePricing(carta="no_results: nada"), ledger) as running:
        answered = await send(running.url, SERVED)

    assert answered.state == TaskState.TASK_STATE_COMPLETED
    (failure,) = answered.results
    assert isinstance(failure, CashierFailure)
    assert failure.code is CashierFailureCode.PRICE_MISSING
    assert failure.message.endswith(": Morcilla de Burgos a la brasa.")


async def test_with_review_on_card_or_cash_come_only_after_the_approval(ledger: Ledger) -> None:
    with RunningCashier(FakePricing(BillStage.AWAITING_REVIEW), ledger) as running:
        presented = await send(running.url, SERVED)
        assert presented.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert presented.results[-1].stage is BillStage.AWAITING_REVIEW
        assert presented.messages[-1] == "Cuenta pendiente de revisión en caja."
        ids = {"task_id": presented.task_id, "context_id": presented.context_id}

        early = await send(running.url, choice(), **ids)
        assert early.results[-1].code is CashierFailureCode.NOT_PAYABLE
        approved = await send(
            running.url,
            ReviewDecision(bill_id="bill_1", version=1, decision="approved", reviewer="caja"),
            **ids,
        )
        assert approved.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert approved.results[-1].stage is BillStage.AWAITING_PAYMENT
        paid = await send(running.url, choice(PaymentMethod.CASH), **ids)

    assert paid.state == TaskState.TASK_STATE_COMPLETED
    assert paid.results[-1].method is PaymentMethod.CASH
    assert ledger.charges == 1


async def test_an_unreadable_first_message_fails_the_task(cashier: RunningCashier) -> None:
    class Garbage(BaseModel):
        hello: str = "mundo"

    answered = await send(cashier.url, Garbage())

    assert answered.state == TaskState.TASK_STATE_FAILED
    assert answered.results == []
