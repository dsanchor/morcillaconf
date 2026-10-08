"""The waiter's ``servir_bebidas`` tool: drinks served from the bar, checked by code.

Drinks never go to the kitchen. The model decides when the customer has
confirmed a round and passes the drinks as asked and the allergies or
intolerances declared in this visit; the bar decides from the carta which
drink each one is and whether it can be served. Nothing about the customer
reaches it. The typed round and its Spanish text are kept for the turn, so
the application shows the bar's bubble before the waiter's reply and records
the round as served at once: there is no pass. The model receives the same
text to summarize.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from agent_framework import FunctionInvocationContext, FunctionTool
from pydantic import BaseModel, ConfigDict, Field

from restaurant_contracts.bar import (
    BarFailure,
    BarFailureCode,
    BarItem,
    BarReport,
    BarRequest,
    bar_failure,
)
from restaurant_contracts.cashier import ServedLine, served_lines
from restaurant_contracts.kitchen import DishName, KitchenText, Quantity

from restaurant_agent.bar import BarService
from restaurant_agent.bar.rendering import render_text

logger = logging.getLogger(__name__)

BAR_TOOL = "servir_bebidas"
# Session state of the current turn only: ConversationManager takes both out.
BAR_REPORT_KEY = "bar_report"
BAR_CALLED_KEY = "bar_called"

ALREADY_ANSWERED = (
    "bar_already_answered: la barra ya ha servido una ronda en este turno. Comunica ese "
    "resultado; si el cliente quiere más bebidas, sírvelas en su próximo mensaje."
)
SERVED_GUIDANCE = (
    "La aplicación ya muestra la ronda al cliente, en la burbuja de la barra, antes de tu "
    "respuesta. Resume el resultado sin alterarlo: lo servido con sus cantidades y lo no "
    "servido con su motivo. Si una bebida era ambigua, pregunta cuál quiere de las opciones "
    "de la carta. Nunca digas que has servido una bebida que no figure como servida, y no "
    "añadas bebidas ni precios."
)
FAILURE_GUIDANCE = (
    "Díselo al cliente con claridad: no has servido ninguna bebida. No inventes el resultado "
    "ni vuelvas a llamar a servir_bebidas en este turno."
)


class DrinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DishName = Field(
        description=(
            "La bebida tal como la pide el cliente o su nombre en la carta, como "
            "«Caña de cerveza»; una sola bebida por línea y sin comentarios."
        )
    )
    quantity: Quantity = Field(default=1, description="Cuántas.")


class BarToolRequest(BaseModel):
    """What the waiter may tell the bar: the drinks and nothing about the customer."""

    model_config = ConfigDict(extra="forbid")

    items: list[DrinkRequest] = Field(min_length=1, max_length=20)
    restrictions: list[KitchenText] = Field(
        default_factory=list,
        max_length=14,
        description=(
            "Alergias o intolerancias que el cliente ha declarado en esta visita, "
            "como «celiaquía» o «alergia a los sulfitos»."
        ),
    )


def tool_answer(report: BarReport) -> str:
    """What the waiter's model reads: the bar's own text and how to relay it."""

    if isinstance(report.result, BarFailure):
        return f"bar_failed: {report.result.code.value}\n{report.text}\n\n{FAILURE_GUIDANCE}"
    return f"bar_served: {report.result.verdict}\n{report.text}\n\n{SERVED_GUIDANCE}"


def take_bar_report(state: dict[str, Any]) -> BarReport | None:
    """The bar's round of this turn, removed from the session state."""

    state.pop(BAR_CALLED_KEY, None)
    stored = state.pop(BAR_REPORT_KEY, None)
    return BarReport.model_validate(stored) if stored is not None else None


def served_this_turn(state: dict[str, Any]) -> list[ServedLine]:
    """The drinks served earlier in this same turn: billable at once, there is no pass."""

    stored = state.get(BAR_REPORT_KEY)
    if stored is None:
        return []
    report = BarReport.model_validate(stored)
    return served_lines([report], {report.request.round_id})


def create_bar_tool(bar: BarService) -> FunctionTool:
    async def servir_bebidas(
        ctx: FunctionInvocationContext,
        items: list[dict[str, Any]],
        restrictions: list[str] | None = None,
    ) -> str:
        state = ctx.session.state if ctx.session is not None else None
        if state is not None:
            # Marked before the bar runs, so a parallel second call is refused too.
            if state.get(BAR_CALLED_KEY):
                return ALREADY_ANSWERED
            state[BAR_CALLED_KEY] = True
        request = BarRequest(
            round_id=f"bar_{uuid4().hex}",
            items=[BarItem(line=number, **item) for number, item in enumerate(items, 1)],
            restrictions=list(restrictions or ()),
        )
        try:
            result = await bar.serve(request)
        except Exception as exc:
            # The service answers failures itself; this only guards against a bug.
            logger.error("The bar failed on %s: %s", request.round_id, type(exc).__name__)
            result = bar_failure(request.round_id, BarFailureCode.BAR_UNAVAILABLE)
        report = BarReport(request=request, result=result, text=render_text(result))
        if state is not None:
            state[BAR_REPORT_KEY] = report.model_dump(mode="json")
        return tool_answer(report)

    return FunctionTool(
        name=BAR_TOOL,
        description=(
            "Sirve desde la barra las bebidas confirmadas: cada bebida con su cantidad y "
            "las alergias o intolerancias declaradas. La aplicación las comprueba con la "
            "carta y decide qué se sirve. Nunca incluyas comida: va a pedir_a_cocina."
        ),
        func=servir_bebidas,
        input_model=BarToolRequest,
        approval_mode="never_require",
    )
