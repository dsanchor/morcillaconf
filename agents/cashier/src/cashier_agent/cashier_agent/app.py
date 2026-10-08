"""A2A JSON-RPC server for the external cashier agent.

One A2A task per bill. The first message, a ``BillRequest``, prices the bill
and leaves the task ``input-required`` with the ``Bill`` artifact. The
customer's ``PaymentChoice`` resumes that same task, which completes with the
``Receipt`` artifact. With staff review on, a ``ReviewDecision`` first moves
the bill from ``awaiting_review`` to ``awaiting_payment`` (another
``input-required`` step). Failures are explicit ``CashierFailure`` artifacts.
"""

from __future__ import annotations

import json
import logging
import uuid

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    TaskState,
)
from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.cashier import (
    CASHIER_INPUT_ADAPTER,
    Bill,
    BillRequest,
    BillStage,
    CashierFailure,
    CashierFailureCode,
    PaymentChoice,
    Receipt,
    ReviewDecision,
    euros,
)

from cashier_agent import progress
from cashier_agent.config import Settings
from cashier_agent.ledger import Ledger
from cashier_agent.service import CashierService

logger = logging.getLogger(__name__)

AWAITING_PAYMENT = "Cuenta presentada: el cliente elige tarjeta o efectivo."
AWAITING_REVIEW = "Cuenta pendiente de revisión en caja."
UNREADABLE_ANSWER = "Caja no entiende esa respuesta; la cuenta sigue pendiente."
INVALID_REQUEST = "La petición A2A de caja no tiene un formato válido."
UNAVAILABLE = "Caja no puede responder ahora mismo."


def request_text(context: RequestContext) -> str:
    if context.message is None:
        raise ValueError("A2A message is required")
    texts = [
        part.text
        for part in context.message.parts
        if part.WhichOneof("content") == "text"
    ]
    if len(texts) != 1 or not texts[0].strip():
        raise ValueError("Cashier expects one JSON text part")
    return texts[0]


class CashierAgentExecutor(AgentExecutor):
    def __init__(self, service: CashierService, ledger: Ledger) -> None:
        self._service = service
        self._ledger = ledger

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.context_id is None:
            raise ValueError("A2A context id is required")
        if context.task_id:
            self._ledger.close_task(context.task_id)
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id)
        await updater.cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None or context.context_id is None:
            raise ValueError("A2A message and context id are required")
        task = context.current_task
        opening = task is None
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, context.context_id)

        async def publish(step: ActivityStep) -> None:
            await updater.update_status(
                state=TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(
                    [Part(text=json.dumps({"activity": step.model_dump(mode="json")}))]
                ),
            )

        progress.install(publish)
        try:
            received = CASHIER_INPUT_ADAPTER.validate_json(request_text(context))
        except (ValidationError, ValueError) as exc:
            logger.warning("Invalid A2A cashier message: %s", type(exc).__name__)
            if opening:
                await updater.submit()
                await self._fail(updater, INVALID_REQUEST)
            else:
                await self._wait(updater, UNREADABLE_ANSWER)
            return
        try:
            if opening:
                await self._open(updater, task.id, received)
            elif isinstance(received, PaymentChoice):
                await self._pay(updater, received)
            elif isinstance(received, ReviewDecision):
                await self._review(updater, received)
            else:
                await self._wait(updater, UNREADABLE_ANSWER)
        except Exception as exc:
            logger.exception("Cashier A2A execution failed: %s", type(exc).__name__)
            await self._fail(updater, UNAVAILABLE)

    async def _open(self, updater: TaskUpdater, task_id: str, received: BaseModel) -> None:
        await updater.submit()
        if not isinstance(received, BillRequest):
            await self._fail(updater, INVALID_REQUEST)
            return
        await updater.start_work()
        result = await self._service.price(received)
        if isinstance(result, CashierFailure):
            await self._artifact(updater, result, f"failure-{result.bill_id}")
            await updater.complete()
            return
        self._ledger.open(result, task_id)
        await self._present(updater, result)

    async def _present(self, updater: TaskUpdater, bill: Bill) -> None:
        await self._artifact(updater, bill, f"bill-{bill.bill_id}-{bill.stage.value}", "Bill")
        reviewing = bill.stage is BillStage.AWAITING_REVIEW
        await self._wait(updater, AWAITING_REVIEW if reviewing else AWAITING_PAYMENT)

    async def _pay(self, updater: TaskUpdater, choice: PaymentChoice) -> None:
        await updater.start_work()
        step = await progress.Step("caja", f"Caja: cobra con {choice.method.value}").start()
        result = self._ledger.pay(choice)
        if isinstance(result, Receipt):
            await step.finish(f"{euros(result.amount)} · referencia {result.reference}")
            await self._artifact(updater, result, f"receipt-{result.bill_id}", "Receipt")
            await updater.complete()
            return
        await step.finish(result.message, failed=True)
        await self._settle_failure(updater, result)

    async def _review(self, updater: TaskUpdater, decision: ReviewDecision) -> None:
        result = self._ledger.review(decision)
        if isinstance(result, Bill):
            await self._present(updater, result)
            return
        await self._settle_failure(updater, result)

    async def _settle_failure(self, updater: TaskUpdater, failure: CashierFailure) -> None:
        """A closed bill completes its task; an open one keeps waiting."""

        await self._artifact(updater, failure, f"failure-{failure.bill_id}-{uuid.uuid4().hex[:8]}")
        account = self._ledger.account(failure.bill_id)
        if (
            failure.code is CashierFailureCode.NOT_PAYABLE
            and account is not None
            and not account.closed
        ):
            reviewing = account.bill.stage is BillStage.AWAITING_REVIEW
            await self._wait(updater, AWAITING_REVIEW if reviewing else AWAITING_PAYMENT)
        else:
            await updater.complete()

    @staticmethod
    async def _artifact(
        updater: TaskUpdater, value: BaseModel, artifact_id: str, name: str | None = None
    ) -> None:
        await updater.add_artifact(
            parts=[Part(text=value.model_dump_json())], artifact_id=artifact_id, name=name
        )

    @staticmethod
    async def _wait(updater: TaskUpdater, text: str) -> None:
        await updater.requires_input(message=updater.new_agent_message([Part(text=text)]))

    @staticmethod
    async def _fail(updater: TaskUpdater, text: str) -> None:
        await updater.update_status(
            state=TaskState.TASK_STATE_FAILED,
            message=updater.new_agent_message([Part(text=text)]),
        )


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_agent_card(settings: Settings) -> AgentCard:
    public_url = str(settings.cashier_a2a_public_url).rstrip("/") + "/"
    return AgentCard(
        name="Caja",
        description=(
            "Presenta la cuenta de los platos y bebidas servidos con los precios "
            "de la carta y cobra con tarjeta o efectivo."
        ),
        version="1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
        ),
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding="JSONRPC")
        ],
        skills=[
            AgentSkill(
                id="cobrar",
                name="Cobrar",
                description=(
                    "Recibe las líneas servidas, las cobra con el precio de la carta "
                    "con aritmética exacta, presenta la cuenta y espera "
                    "(input-required) a que el cliente elija tarjeta o efectivo; "
                    "después registra el pago simulado y devuelve el recibo. "
                    "Repetir el pago devuelve el mismo recibo."
                ),
                tags=["cashier", "restaurant", "agent-framework"],
                examples=[
                    '{"kind":"bill_request","schema_version":1,"bill_id":"bill_example",'
                    '"lines":[{"order_id":"ko_example","line":1,'
                    '"carta_id":"morcilla-de-burgos-a-la-brasa",'
                    '"name":"Morcilla de Burgos a la brasa","quantity":1}]}'
                ],
            )
        ],
    )


def create_app(
    settings: Settings,
    *,
    service: CashierService | None = None,
    ledger: Ledger | None = None,
) -> Starlette:
    agent_card = create_agent_card(settings)
    request_handler = DefaultRequestHandler(
        agent_executor=CashierAgentExecutor(
            service or CashierService(settings), ledger or Ledger()
        ),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            *create_agent_card_routes(agent_card),
            *create_jsonrpc_routes(request_handler, "/"),
        ]
    )
