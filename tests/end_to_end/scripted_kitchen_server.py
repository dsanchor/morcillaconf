"""The external kitchen for the cashier end to end, without Foundry (test code only).

dsanchor's A2A kitchen app (``kitchen_agent.app.create_app``) with a scripted
service instead of the chef: it cooks at once the dishes of a tiny carta and
rejects the rest, so the waiter has served dishes to bill. Run with the
waiter's environment, which already includes the kitchen project.
"""

from __future__ import annotations

import os

import uvicorn

from kitchen_agent.app import create_app
from kitchen_agent.config import Settings
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenPlan,
    KitchenSource,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)

# keyword, carta id, name, station
CARTA = (
    ("croqueta", "croquetas-de-morcilla", "Croquetas de morcilla", KitchenStation.FRITOS),
    ("morcilla", "morcilla-de-burgos-a-la-brasa", "Morcilla de Burgos a la brasa", KitchenStation.BRASA),
)


class ScriptedKitchen:
    async def plan(self, order: KitchenOrder) -> KitchenPlan:
        accepted, rejected = [], []
        for line in order.lines:
            dish = next((dish for dish in CARTA if dish[0] in line.name.casefold()), None)
            if dish is None:
                rejected.append(
                    RejectedItem(line=line.line, requested=line.name, quantity=line.quantity, reason="No está en la carta.")
                )
                continue
            accepted.append(
                AcceptedItem(line=line.line, carta_id=dish[1], name=dish[2], quantity=line.quantity, station=dish[3])
            )
        stations = [
            StationPlan(
                station=station,
                tasks=[
                    StationTask(line=item.line, carta_id=item.carta_id, name=item.name, quantity=item.quantity)
                    for item in accepted
                    if item.station is station
                ],
            )
            for station in KitchenStation
            if any(item.station is station for item in accepted)
        ]
        return KitchenPlan(
            order_id=order.order_id,
            accepted=accepted,
            rejected=rejected,
            stations=stations,
            sources=[KitchenSource(document="carta de la casa", version="1")],
        )


def main() -> None:
    port = int(os.environ["PORT"])
    settings = Settings(
        _env_file=None,
        foundry_project_endpoint="https://scripted.invalid/api/projects/scripted",
        azure_ai_model_deployment_name="scripted",
        kitchen_a2a_public_url=f"http://127.0.0.1:{port}/",
    )
    uvicorn.run(create_app(settings, service=ScriptedKitchen()), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
