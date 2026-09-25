"""Semantic consolidation of overlapping habitual-order memories."""

import json
from typing import Protocol

from agent_framework import AgentResponse
from pydantic import BaseModel, ConfigDict, Field


class HabitualOrderQuestion(BaseModel):
    """Natural question that consolidates overlapping remembered orders."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)


class OrderOptionMerger(Protocol):
    """Agent subset required to consolidate remembered order options."""

    async def run(
        self,
        messages: str,
        *,
        options: dict[str, object],
    ) -> AgentResponse[HabitualOrderQuestion]: ...


async def merge_habitual_order_options(
    merger: OrderOptionMerger,
    order_options: list[str],
) -> str:
    """Build one concise question without repeating overlapping combinations."""

    response = await merger.run(
        (
            "Convierte estos pedidos recordados en una pregunta breve y natural "
            "para el cliente. Fusiona las opciones a nivel de producto: no "
            "enumeres cada combinación completa ni repitas productos presentes "
            "en varias combinaciones. Agrupa productos que cumplen el mismo "
            "papel como alternativas y pregunta aparte por complementos "
            "opcionales. Por ejemplo, para ['agua con gas, pincho de tortilla', "
            "'coca cola', 'agua con gas, pincho de tortilla, coca cola'] responde "
            "«¿Prefieres hoy Coca-Cola o agua con gas? ¿Quieres también pincho "
            "de tortilla?». No menciones memoria, frecuencias, recencia, "
            "contadores ni el proceso interno. No añadas productos que no estén "
            "en la entrada. Devuelve solamente la pregunta estructurada.\n\n"
            f"Pedidos recordados: {json.dumps(order_options, ensure_ascii=False)}"
        ),
        options={"response_format": HabitualOrderQuestion},
    )
    return response.value.question
