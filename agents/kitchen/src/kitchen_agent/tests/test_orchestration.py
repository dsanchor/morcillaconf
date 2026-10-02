from __future__ import annotations

from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenStation,
    StationPlan,
    StationTask,
)

from kitchen_agent.orchestration import ChefGroupChat, consolidate
from kitchen_agent.specialists import SpecialistDecision, SpecialistReply


def order() -> KitchenOrder:
    return KitchenOrder(
        order_id="ko_group",
        lines=[
            KitchenOrderLine(line=1, name="Morcilla a la brasa"),
            KitchenOrderLine(line=2, name="Croquetas", quantity=2),
            KitchenOrderLine(line=3, name="Ensaladilla"),
        ],
    )


def plan() -> KitchenPlan:
    stations = [
        (KitchenStation.BRASA, 1, "morcilla", 1),
        (KitchenStation.FRITOS, 2, "croquetas", 2),
        (KitchenStation.PINCHOS_FRIOS, 3, "ensaladilla", 1),
    ]
    return KitchenPlan(
        order_id="ko_group",
        accepted=[
            AcceptedItem(
                line=line,
                carta_id=carta_id,
                name=carta_id,
                quantity=quantity,
                station=station,
            )
            for station, line, carta_id, quantity in stations
        ],
        stations=[
            StationPlan(
                station=station,
                tasks=[
                    StationTask(
                        line=line,
                        carta_id=carta_id,
                        name=carta_id,
                        quantity=quantity,
                    )
                ],
            )
            for station, line, carta_id, quantity in stations
        ],
    )


async def test_group_chat_routes_to_all_required_specialists_and_they_accept() -> None:
    result = await ChefGroupChat().review(order(), plan())

    assert result.verdict == "accepted"
    assert [item.line for item in result.accepted] == [1, 2, 3]
    assert [station.station for station in result.stations] == [
        KitchenStation.BRASA,
        KitchenStation.FRITOS,
        KitchenStation.PINCHOS_FRIOS,
    ]


def test_the_chef_rejects_a_line_when_its_specialist_rejects_it() -> None:
    replies = [
        SpecialistReply(
            specialist="Parrilla",
            decisions=[
                SpecialistDecision(
                    line=1,
                    station=KitchenStation.BRASA,
                    accepted=False,
                    reason="La parrilla está fuera de servicio.",
                )
            ],
        ),
        SpecialistReply(
            specialist="Fritos",
            decisions=[
                SpecialistDecision(
                    line=2,
                    station=KitchenStation.FRITOS,
                    accepted=True,
                    reason="Aceptado.",
                )
            ],
        ),
        SpecialistReply(
            specialist="General",
            decisions=[
                SpecialistDecision(
                    line=3,
                    station=KitchenStation.PINCHOS_FRIOS,
                    accepted=True,
                    reason="Aceptado.",
                )
            ],
        ),
    ]

    result = consolidate(order(), plan(), replies)

    assert result.verdict == "partial"
    assert [item.line for item in result.accepted] == [2, 3]
    assert [item.line for item in result.rejected] == [1]
    assert "parrilla está fuera de servicio" in result.rejected[0].reason
    assert [station.station for station in result.stations] == [
        KitchenStation.FRITOS,
        KitchenStation.PINCHOS_FRIOS,
    ]
