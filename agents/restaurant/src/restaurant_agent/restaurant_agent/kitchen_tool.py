"""The waiter's ``pedir_a_cocina`` tool: it sends a clear order to the kitchen.

The tool builds the ``KitchenOrder`` from the order alone (dishes,
quantities, modifications and the allergies or intolerances declared for it)
and sends it through a ``KitchenPort``; the customer's name, profile, history
and memory never reach the chef. The kitchen's typed answer and its Spanish
text are kept for the turn, so the application can show the chef's bubble
before the waiter's reply, and the model receives the same text to summarize.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from agent_framework import FunctionInvocationContext, FunctionTool
from pydantic import BaseModel, ConfigDict, Field

from restaurant_contracts.kitchen import (
    DishName,
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPort,
    KitchenReport,
    KitchenText,
    Quantity,
)

from restaurant_agent.kitchen.port import kitchen_failure
from restaurant_agent.kitchen.rendering import render_text

logger = logging.getLogger(__name__)

KITCHEN_TOOL = "pedir_a_cocina"
# Session state of the current turn only: ConversationManager takes both out.
KITCHEN_REPORT_KEY = "kitchen_report"
KITCHEN_CALLED_KEY = "kitchen_called"

ALREADY_ANSWERED = (
    "kitchen_already_answered: cocina ya ha respondido a un pedido en este turno. "
    "Comunica ese resultado; si el cliente cambia el pedido, envíalo en su próximo mensaje."
)
PLAN_GUIDANCE = (
    "La aplicación ya muestra este plan al cliente, en su propia burbuja, antes de tu "
    "respuesta. Resume el veredicto de cocina sin alterarlo: lo aceptado con sus "
    "adaptaciones, lo rechazado con su motivo y los avisos, con las mismas cantidades. "
    "No añadas platos, tiempos, existencias ni precios, y no digas que el pedido está "
    "confirmado o en preparación."
)
FAILURE_GUIDANCE = (
    "Díselo al cliente con claridad. No inventes el resultado de cocina ni vuelvas a "
    "llamar a pedir_a_cocina en este turno."
)


class OrderLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DishName = Field(description="El plato o la bebida tal como lo pide el cliente.")
    quantity: Quantity = Field(default=1, description="Raciones, unidades o bebidas.")
    modifications: list[KitchenText] = Field(
        default_factory=list, max_length=10, description="Cambios pedidos, como «sin cebolla»."
    )


class KitchenRequest(BaseModel):
    """What the waiter may tell the kitchen: the order and nothing about the customer."""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderLineRequest] = Field(min_length=1, max_length=20)
    restrictions: list[KitchenText] = Field(
        default_factory=list,
        max_length=14,
        description=(
            "Alergias o intolerancias que el cliente ha declarado en esta visita, "
            "como «celiaquía» o «alergia a los frutos de cáscara»."
        ),
    )


def tool_answer(report: KitchenReport) -> str:
    """What the waiter's model reads: the kitchen's own text and how to relay it."""

    if isinstance(report.result, KitchenFailure):
        return f"kitchen_failed: {report.result.code.value}\n{report.text}\n\n{FAILURE_GUIDANCE}"
    return f"kitchen_plan: {report.result.verdict}\n{report.text}\n\n{PLAN_GUIDANCE}"


def take_kitchen_report(state: dict[str, Any]) -> KitchenReport | None:
    """The kitchen's answer of this turn, removed from the session state."""

    state.pop(KITCHEN_CALLED_KEY, None)
    stored = state.pop(KITCHEN_REPORT_KEY, None)
    return KitchenReport.model_validate(stored) if stored is not None else None


def create_kitchen_tool(kitchen: KitchenPort) -> FunctionTool:
    async def pedir_a_cocina(
        ctx: FunctionInvocationContext,
        items: list[dict[str, Any]],
        restrictions: list[str] | None = None,
    ) -> str:
        state = ctx.session.state if ctx.session is not None else None
        if state is not None:
            # Marked before the chef runs, so a parallel second call is refused too.
            if state.get(KITCHEN_CALLED_KEY):
                return ALREADY_ANSWERED
            state[KITCHEN_CALLED_KEY] = True
        order = KitchenOrder(
            order_id=f"ko_{uuid4().hex}",
            lines=[KitchenOrderLine(line=number, **item) for number, item in enumerate(items, 1)],
            restrictions=list(restrictions or ()),
        )
        try:
            result = await kitchen.plan(order)
        except Exception as exc:
            # The port answers failures itself; this only guards against a bug.
            logger.error("The kitchen port failed on %s: %s", order.order_id, type(exc).__name__)
            result = kitchen_failure(order, KitchenFailureCode.CHEF_UNAVAILABLE)
        report = KitchenReport(order=order, result=result, text=render_text(result))
        if state is not None:
            state[KITCHEN_REPORT_KEY] = report.model_dump(mode="json")
        return tool_answer(report)

    return FunctionTool(
        name=KITCHEN_TOOL,
        description=(
            "Envía a cocina un pedido claro: platos y bebidas con su cantidad, sus "
            "modificaciones y las alergias o intolerancias declaradas. El chef lo "
            "comprueba con la carta y el recetario y devuelve su plan."
        ),
        func=pedir_a_cocina,
        input_model=KitchenRequest,
        approval_mode="never_require",
    )
