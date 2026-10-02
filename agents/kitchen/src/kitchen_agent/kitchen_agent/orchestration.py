"""Chef-led concurrent specialist review and deterministic consolidation."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from agent_framework import AgentResponse, Message
from agent_framework.orchestrations import ConcurrentBuilder
from pydantic import ValidationError

from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenPlan,
    KitchenStation,
    RejectedItem,
    StationPlan,
)

from kitchen_agent.specialists import (
    SPECIALIST_STATIONS,
    SpecialistName,
    SpecialistReply,
    create_specialists,
)

SPECIALIST_REJECTION = "La partida {specialist} no ha aceptado esta preparación: {reason}"
MISSING_DECISION = "La partida {specialist} no ha confirmado esta preparación."


class InvalidSpecialistAnswer(Exception):
    """A group chat response cannot safely confirm the chef's plan."""


def required_specialists(plan: KitchenPlan) -> list[SpecialistName]:
    stations = {station.station for station in plan.stations}
    return [
        name
        for name, owned in SPECIALIST_STATIONS.items()
        if stations & owned
    ]


def coordination_payload(plan: KitchenPlan) -> str:
    assignments = {
        specialist: [
            station.model_dump(mode="json")
            for station in plan.stations
            if station.station in owned
        ]
        for specialist, owned in SPECIALIST_STATIONS.items()
        if any(station.station in owned for station in plan.stations)
    }
    return json.dumps(
        {
            "order_id": plan.order_id,
            "assignments": assignments,
        },
        ensure_ascii=False,
    )


class ChefCoordination:
    """Run the chef's selected station reviewers concurrently and collect their OKs."""

    def __init__(
        self,
        client_factory: Callable[[SpecialistName], Any],
    ) -> None:
        self._client_factory = client_factory

    async def review(self, order: KitchenOrder, plan: KitchenPlan) -> KitchenPlan:
        required = required_specialists(plan)
        if not required:
            return plan
        specialists = create_specialists(self._client_factory)
        workflow = ConcurrentBuilder(
            name=f"kitchen-{order.order_id}",
            participants=[specialists[name] for name in required],
        ).build()
        result = await workflow.run(coordination_payload(plan))
        outputs = result.get_outputs()
        responses = [
            output for output in outputs if isinstance(output, AgentResponse)
        ]
        if not responses:
            raise InvalidSpecialistAnswer("group chat produced no conversation")
        replies = _parse_replies(
            [
                message
                for response in responses
                for message in response.messages
            ],
            required,
        )
        return consolidate(order, plan, replies)


def _parse_replies(
    conversation: Sequence[object],
    required: Sequence[SpecialistName],
) -> list[SpecialistReply]:
    replies: dict[str, SpecialistReply] = {}
    for item in conversation:
        if not isinstance(item, Message) or item.author_name not in required:
            continue
        try:
            reply = SpecialistReply.model_validate_json(item.text)
        except (ValidationError, ValueError) as exc:
            raise InvalidSpecialistAnswer(
                f"invalid {item.author_name} response"
            ) from exc
        if reply.specialist != item.author_name:
            raise InvalidSpecialistAnswer("specialist identity mismatch")
        replies[item.author_name] = reply
    missing = [name for name in required if name not in replies]
    if missing:
        raise InvalidSpecialistAnswer(
            f"missing specialist responses: {', '.join(missing)}"
        )
    return [replies[name] for name in required]


def consolidate(
    order: KitchenOrder,
    plan: KitchenPlan,
    replies: Sequence[SpecialistReply],
) -> KitchenPlan:
    decisions = {
        decision.line: (reply.specialist, decision)
        for reply in replies
        for decision in reply.decisions
    }
    kept: list[AcceptedItem] = []
    rejected = list(plan.rejected)
    rejected_lines = {item.line for item in rejected}
    for item in plan.accepted:
        specialist = _specialist_for(item.station)
        response = decisions.get(item.line)
        if response is not None and response[0] == specialist and response[1].accepted:
            kept.append(item)
            continue
        reason = (
            MISSING_DECISION.format(specialist=specialist)
            if response is None
            else SPECIALIST_REJECTION.format(
                specialist=specialist,
                reason=response[1].reason,
            )
        )
        source = order.get_line(item.line)
        rejected.append(
            RejectedItem(
                line=item.line,
                carta_id=item.carta_id,
                requested=", ".join([source.name, *source.modifications]),
                quantity=item.quantity,
                reason=reason,
            )
        )
        rejected_lines.add(item.line)
    stations = [
        StationPlan(
            station=station.station,
            tasks=[
                task for task in station.tasks if task.line not in rejected_lines
            ],
        )
        for station in plan.stations
        if any(task.line not in rejected_lines for task in station.tasks)
    ]
    return KitchenPlan(
        order_id=plan.order_id,
        version=plan.version,
        accepted=kept,
        rejected=sorted(rejected, key=lambda item: item.line),
        warnings=plan.warnings,
        stations=stations,
        sources=plan.sources,
    )


def _specialist_for(station: KitchenStation) -> SpecialistName:
    for name, stations in SPECIALIST_STATIONS.items():
        if station in stations:
            return name
    raise InvalidSpecialistAnswer(f"no specialist for station {station.value}")
