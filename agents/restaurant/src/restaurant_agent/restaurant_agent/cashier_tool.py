"""The waiter's ``pedir_la_cuenta`` tool: it asks the cashier for the bill.

The model takes no part in what is billed: the tool has no arguments. The
application sets the turn's billing context (the kitchen dishes and the bar
drinks already served, how many cooked orders still wait at the pass and the
bill already presented) and the tool builds the ``BillRequest`` from it, plus
the drinks served earlier in this same turn: they never wait at the pass. It
refuses while dishes wait at the pass (also those cooked in this turn), when
nothing has been served yet and when a bill is already waiting for the
customer's card or cash, unless new drinks have just made it stale. The
cashier's typed answer and its Spanish text are kept for the turn, so the
application shows the cashier's bubble before the waiter's reply.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from agent_framework import FunctionInvocationContext, FunctionTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from restaurant_contracts.cashier import (
    Bill,
    BillRequest,
    CashierAnswer,
    CashierFailure,
    CashierFailureCode,
    CashierPort,
    CashierReport,
    PendingBill,
    ServedLine,
    cashier_failure,
    euros,
)

from restaurant_agent.bar_tool import served_this_turn
from restaurant_agent.cashier.rendering import render_text
from restaurant_agent.kitchen_tool import cooked_this_turn

logger = logging.getLogger(__name__)

CASHIER_TOOL = "pedir_la_cuenta"
# Session state of the current turn only: ConversationManager takes them out.
BILLING_KEY = "billing_context"
CASHIER_REPORT_KEY = "cashier_report"
CASHIER_CALLED_KEY = "cashier_called"

ALREADY_ANSWERED = (
    "cashier_already_answered: caja ya ha respondido en este turno. Comunica ese resultado."
)
BILL_PENDING = (
    "bill_pending: la cuenta ya está presentada en su burbuja, con los botones «Tarjeta» y "
    "«Efectivo». Dile al cliente que pague con esos botones: un mensaje nunca paga."
)
DISHES_AT_PASS = (
    "dishes_at_pass: hay {count} en el pase todavía sin servir. Dile al cliente que primero "
    "se los llevas a la mesa y que después podrá pedir la cuenta. No inventes importes."
)
NOTHING_SERVED = (
    "nothing_served: todavía no hay nada servido que cobrar. Díselo al cliente y no "
    "inventes importes."
)
BILL_GUIDANCE = (
    "La aplicación ya muestra la cuenta al cliente en su propia burbuja, con los botones "
    "«Tarjeta» y «Efectivo», antes de tu respuesta. Dile que elija cómo pagar con esos "
    "botones. No calcules, sumes ni cambies importes: si mencionas el total, cópialo tal "
    "cual. Un mensaje del cliente nunca paga."
)
FAILURE_GUIDANCE = (
    "Díselo al cliente con claridad. No inventes importes ni vuelvas a llamar a "
    "pedir_la_cuenta en este turno."
)


class NoArguments(BaseModel):
    """The model never chooses what is billed."""

    model_config = ConfigDict(extra="forbid")


class BillingContext(BaseModel):
    """What the application knows about the bill for this turn."""

    model_config = ConfigDict(extra="forbid")

    served: list[ServedLine] = Field(default_factory=list, max_length=50)
    orders_at_pass: int = Field(default=0, ge=0)
    pending_bill: PendingBill | None = None


def set_billing(state: dict[str, Any], billing: BillingContext | None) -> None:
    state[BILLING_KEY] = (billing or BillingContext()).model_dump(mode="json")


def take_cashier_report(state: dict[str, Any]) -> CashierReport | None:
    """The cashier's answer of this turn, removed with the turn's billing context."""

    state.pop(BILLING_KEY, None)
    state.pop(CASHIER_CALLED_KEY, None)
    stored = state.pop(CASHIER_REPORT_KEY, None)
    return CashierReport.model_validate(stored) if stored is not None else None


def tool_answer(report: CashierReport) -> str:
    """What the waiter's model reads: the cashier's own text and how to relay it."""

    if isinstance(report.result, CashierFailure):
        return f"cashier_failed: {report.result.code.value}\n{report.text}\n\n{FAILURE_GUIDANCE}"
    return f"bill_presented: total {euros(report.result.total)}\n{report.text}\n\n{BILL_GUIDANCE}"


def _orders(count: int) -> str:
    return "1 pedido de cocina" if count == 1 else f"{count} pedidos de cocina"


def _report(request: BillRequest, answer: CashierAnswer) -> CashierReport:
    try:
        return CashierReport(
            bill_id=request.bill_id,
            request=request,
            result=answer.result,
            text=render_text(answer.result),
            task=answer.task,
        )
    except ValidationError:
        # The cashier billed something other than the served lines.
        logger.warning("The cashier's bill %s does not match the served lines", request.bill_id)
        failure = cashier_failure(request.bill_id, CashierFailureCode.INVALID_BILL)
        return CashierReport(
            bill_id=request.bill_id, request=request, result=failure, text=render_text(failure)
        )


def create_cashier_tool(cashier: CashierPort) -> FunctionTool:
    async def pedir_la_cuenta(ctx: FunctionInvocationContext) -> str:
        state = ctx.session.state if ctx.session is not None else {}
        # Marked before the cashier runs, so a parallel second call is refused too.
        if state.get(CASHIER_CALLED_KEY):
            return ALREADY_ANSWERED
        state[CASHIER_CALLED_KEY] = True
        billing = BillingContext.model_validate(state.get(BILLING_KEY) or {})
        # Drinks served earlier in this turn: on the table already, and they
        # make a bill presented before them stale.
        drinks = served_this_turn(state)
        if billing.pending_bill is not None and not drinks:
            return BILL_PENDING
        at_pass = billing.orders_at_pass + (1 if cooked_this_turn(state) else 0)
        if at_pass:
            return DISHES_AT_PASS.format(count=_orders(at_pass))
        if not billing.served and not drinks:
            return NOTHING_SERVED
        request = BillRequest(bill_id=f"bill_{uuid4().hex}", lines=[*billing.served, *drinks])
        try:
            answer = await cashier.present(request)
        except Exception as exc:
            # The port answers failures itself; this only guards against a bug.
            logger.error("The cashier port failed on %s: %s", request.bill_id, type(exc).__name__)
            answer = CashierAnswer(
                result=cashier_failure(request.bill_id, CashierFailureCode.CASHIER_UNAVAILABLE)
            )
        report = _report(request, answer)
        if report.task is None and answer.task is not None:
            # A bill that cannot be shown must not stay payable at the cashier.
            version = answer.result.version if isinstance(answer.result, Bill) else 1
            await cashier.cancel(
                PendingBill(bill_id=request.bill_id, version=version, task=answer.task)
            )
        state[CASHIER_REPORT_KEY] = report.model_dump(mode="json")
        return tool_answer(report)

    return FunctionTool(
        name=CASHIER_TOOL,
        description=(
            "Pide a caja la cuenta de los platos y las bebidas ya servidos. No recibe "
            "argumentos: la aplicación sabe qué se ha servido. Úsala solo cuando el "
            "cliente pida la cuenta."
        ),
        func=pedir_la_cuenta,
        input_model=NoArguments,
        approval_mode="never_require",
    )
