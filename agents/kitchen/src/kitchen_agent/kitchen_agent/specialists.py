"""Temporary kitchen specialists used by the chef's group chat.

They are real Agent Framework participants, but their current client is
deterministic: each specialist accepts every task assigned to its station.
Replacing that client later adds real station reasoning without changing the
chef's orchestration or consolidation contracts.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
)
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
    "General": frozenset(
        {KitchenStation.PINCHOS_FRIOS, KitchenStation.BARRA}
    ),
}


class AlwaysAcceptSpecialistClient(
    FunctionInvocationLayer,
    ChatMiddlewareLayer,
    BaseChatClient,
):
    """Temporary deterministic client: every assigned station task is accepted."""

    def __init__(self, name: SpecialistName) -> None:
        super().__init__()
        self._name = name
        self._stations = SPECIALIST_STATIONS[name]

    def _inner_get_response(
        self,
        *,
        messages: list[Message],
        stream: bool,
        options: dict[str, Any],
        **kwargs: Any,
    ):
        async def respond() -> ChatResponse:
            payload = _group_chat_payload(messages)
            decisions = [
                SpecialistDecision(
                    line=int(task["line"]),
                    station=KitchenStation(station["station"]),
                    accepted=True,
                    reason="La partida acepta temporalmente esta tarea.",
                )
                for station in payload.get("stations", [])
                if KitchenStation(station["station"]) in self._stations
                for task in station.get("tasks", [])
            ]
            reply = SpecialistReply(
                specialist=self._name,
                decisions=decisions,
            )
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_text(
                                reply.model_dump_json()
                            )
                        ],
                    )
                ]
            )

        return respond()


def _group_chat_payload(messages: list[Message]) -> dict[str, Any]:
    for message in messages:
        if message.role != "user":
            continue
        for content in message.contents:
            text = getattr(content, "text", None)
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if isinstance(payload, dict) and "stations" in payload:
                return payload
    raise ValueError("The specialist did not receive a kitchen plan")


def create_specialists() -> dict[SpecialistName, Agent]:
    descriptions = {
        "Parrilla": "Revisa las tareas de parrilla y brasa.",
        "Fritos": "Revisa las tareas de fritura.",
        "General": "Revisa pinchos fríos, barra y el resto de tareas generales.",
    }
    return {
        name: Agent(
            id=f"kitchen-{name.casefold()}",
            name=name,
            description=description,
            client=AlwaysAcceptSpecialistClient(name),
            instructions=(
                "Revisa únicamente las tareas de tus partidas. Durante esta fase "
                "temporal acepta siempre las tareas recibidas y responde con el "
                "contrato estructurado, sin hablar con el cliente."
            ),
            default_options={
                "store": False,
                "response_format": SpecialistReply,
            },
        )
        for name, description in descriptions.items()
    }
