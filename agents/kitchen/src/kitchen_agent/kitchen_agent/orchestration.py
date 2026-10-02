"""Chef-coordinated group chat and deterministic specialist consolidation."""

from __future__ import annotations

from collections.abc import Sequence

from agent_framework import AgentResponse, Message
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState
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


def group_chat_payload(plan: KitchenPlan) -> str:
    return plan.model_dump_json(
        include={"order_id", "stations"},
    )


class ChefGroupChat:
    """The chef selects each relevant specialist and consolidates its verdict."""

    async def review(self, order: KitchenOrder, plan: KitchenPlan) -> KitchenPlan:
        required = required_specialists(plan)
        if not required:
            return plan
        specialists = create_specialists()

        def select(state: GroupChatState) -> str:
            return required[min(state.current_round, len(required) - 1)]

        def complete(conversation: list[Message]) -> bool:
            answered = {
                message.author_name
                for message in conversation
                if message.role == "assistant" and message.author_name in required
            }
            return len(answered) == len(required)

        workflow = GroupChatBuilder(
            name=f"kitchen-{order.order_id}",
            participants=[specialists[name] for name in required],
            selection_func=select,
            orchestrator_name="Chef",
            termination_condition=complete,
            max_rounds=len(required),
            output_from="all",
        ).build()
        result = await workflow.run(group_chat_payload(plan))
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
