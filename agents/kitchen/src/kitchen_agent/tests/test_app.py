from __future__ import annotations

from starlette.testclient import TestClient

from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenPlan,
    KitchenStation,
    StationPlan,
    StationTask,
)

from kitchen_agent.app import create_agent_card, create_app
from kitchen_agent.config import Settings


def settings() -> Settings:
    return Settings(
        _env_file=None,
        foundry_project_endpoint=(
            "https://example.services.ai.azure.com/api/projects/demo"
        ),
        azure_ai_model_deployment_name="test-model",
        kitchen_a2a_public_url="http://localhost:8089/",
    )


class FakeKitchen:
    async def plan(self, order: KitchenOrder) -> KitchenPlan:
        item = order.lines[0]
        return KitchenPlan(
            order_id=order.order_id,
            accepted=[
                AcceptedItem(
                    line=item.line,
                    carta_id="morcilla-a-la-brasa",
                    name="Morcilla a la brasa",
                    quantity=item.quantity,
                    station=KitchenStation.BRASA,
                )
            ],
            stations=[
                StationPlan(
                    station=KitchenStation.BRASA,
                    tasks=[
                        StationTask(
                            line=item.line,
                            carta_id="morcilla-a-la-brasa",
                            name="Morcilla a la brasa",
                            quantity=item.quantity,
                        )
                    ],
                )
            ],
        )


def test_agent_card_describes_the_confirmed_kitchen_skill() -> None:
    card = create_agent_card(settings())

    assert card.name == "Cocina"
    assert card.version == "1.0"
    assert [skill.id for skill in card.skills] == [
        "coordinate-kitchen-order"
    ]
    assert card.supported_interfaces[0].url == "http://localhost:8089/"


def test_health_and_agent_card_are_published() -> None:
    client = TestClient(create_app(settings(), service=FakeKitchen()))

    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    assert response.json()["name"] == "Cocina"
