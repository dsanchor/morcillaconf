"""Prompted kitchen specialists that review the chef's assignments."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from agent_framework import Agent
from pydantic import BaseModel, ConfigDict, Field

from restaurant_contracts.kitchen import KitchenStation

SpecialistName = Literal["Parrilla", "Fritos", "General"]


class SpecialistDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1, le=20)
    station: KitchenStation
    accepted: bool
    reason: str


class SpecialistReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialist: SpecialistName
    decisions: list[SpecialistDecision] = Field(default_factory=list, max_length=20)


SPECIALIST_STATIONS: dict[SpecialistName, frozenset[KitchenStation]] = {
    "Parrilla": frozenset({KitchenStation.BRASA}),
    "Fritos": frozenset({KitchenStation.FRITOS}),
    "General": frozenset({KitchenStation.PINCHOS_FRIOS}),
}


def create_specialists(
    client_factory: Callable[[SpecialistName], Any],
) -> dict[SpecialistName, Agent]:
    descriptions = {
        "Parrilla": "Revisa las tareas de parrilla y brasa.",
        "Fritos": "Revisa las tareas de fritura.",
        "General": "Revisa pinchos fríos y el resto de elaboraciones generales.",
    }
    return {
        name: Agent(
            id=f"kitchen-{name.casefold()}",
            name=name,
            description=description,
            client=client_factory(name),
            instructions=(
                f"Eres la partida {name} de la cocina. Revisa exclusivamente las "
                "tareas que el chef te asigne bajo tu nombre y solo para estas "
                f"partidas: {', '.join(sorted(station.value for station in SPECIALIST_STATIONS[name]))}. "
                "Devuelve una decisión por cada línea asignada, conservando su "
                "número y partida. Acepta cuando la preparación, las omisiones y "
                "las precauciones sean ejecutables y coherentes; rechaza si falta "
                "información imprescindible, la tarea pertenece a otra partida o "
                "las instrucciones son contradictorias o inseguras, explicando el "
                "motivo. No revises líneas de otras partidas, no inventes stock ni "
                "tiempos y no hables con el cliente. Responde solo con el contrato "
                "estructurado solicitado."
            ),
            default_options={
                "store": False,
                "response_format": SpecialistReply,
            },
        )
        for name, description in descriptions.items()
    }
