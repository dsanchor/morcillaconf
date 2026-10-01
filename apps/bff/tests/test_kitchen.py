"""Cocina v1 in the BFF: the chef's plan is its own message, before the waiter's reply."""

import json
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from restaurant_contracts.application import ActorContext, ChatMessage
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)
from restaurant_contracts.memory_store import SQLiteMemoryStore

from bff.local_waiter import LocalWaiter
from bff.main import create_app
from bff.scripted import ScriptedWaiterAgent
from bff.service import RestaurantService
from bff.storage import Database
from bff.waiter import RemoteWaiter, WaiterTurn, WaiterTurnResult

ORDER = KitchenOrder(
    order_id="ko_1",
    lines=[
        KitchenOrderLine(line=1, name="morcilla a la brasa"),
        KitchenOrderLine(line=2, name="hamburguesa", modifications=["sin queso"]),
    ],
)
PLAN = KitchenPlan(
    order_id="ko_1",
    accepted=[
        AcceptedItem(
            line=1,
            carta_id="morcilla-de-burgos-a-la-brasa",
            name="Morcilla de Burgos a la brasa",
            quantity=1,
            station=KitchenStation.BRASA,
        )
    ],
    rejected=[RejectedItem(line=2, requested="hamburguesa, sin queso", quantity=1, reason="No está en la carta.")],
    stations=[
        StationPlan(
            station=KitchenStation.BRASA,
            tasks=[
                StationTask(
                    line=1,
                    carta_id="morcilla-de-burgos-a-la-brasa",
                    name="Morcilla de Burgos a la brasa",
                    quantity=1,
                )
            ],
        )
    ],
)
REPORT = KitchenReport(order=ORDER, result=PLAN, text="Plan de cocina\nAceptado:\n- 1 × Morcilla de Burgos a la brasa")
FAILED = KitchenReport(
    order=ORDER,
    result=KitchenFailure(
        order_id="ko_1",
        code=KitchenFailureCode.NOT_CONFIGURED,
        message="Cocina no puede consultar la carta: la base de conocimiento no está configurada.",
    ),
    text="Cocina no ha podido preparar el plan del pedido.",
)


class KitchenWaiter:
    """The scripted waiter, plus the kitchen's answer when the message is an order."""

    def __init__(self, waiter: LocalWaiter, report: KitchenReport) -> None:
        self._waiter = waiter
        self._report = report
        self.mode = waiter.mode

    async def take_turn(self, turn: WaiterTurn) -> WaiterTurnResult:
        result = await self._waiter.take_turn(turn)
        if turn.message.startswith("Pido"):
            result = replace(result, kitchen=self._report)
        return result

    def __getattr__(self, name):
        return getattr(self._waiter, name)


def kitchen_waiter(report: KitchenReport = REPORT):
    def build(config):
        memory_store = SQLiteMemoryStore(config.bff_database_path.with_name("test-waiter-memory.db"))
        local = LocalWaiter(
            ScriptedWaiterAgent(), mode="scripted", max_turns=config.waiter_max_turns, memory_store=memory_store
        )
        return KitchenWaiter(local, report)

    return build


def say(client: TestClient, headers: dict, conversation_id: str, event_id: str, text: str) -> dict:
    response = client.post(
        "/v1/commands",
        json={
            "schema_version": 1,
            "event_id": event_id,
            "occurred_at": "2026-10-01T12:00:00Z",
            "event_type": "conversation.message_sent",
            "conversation_id": conversation_id,
            "payload": {"message": text},
        },
        headers=headers,
    )
    assert response.status_code == 202
    for _ in range(200):
        result = client.get(f"/v1/commands/{event_id}", headers=headers).json()
        if result["status"] != "pending":
            return result
    raise AssertionError("The turn did not finish")


def visit(client: TestClient) -> tuple[dict, str]:
    session = client.post("/v1/sessions", json={"name": "Ana"}).json()
    headers = {"Authorization": f"Bearer {session['token']}"}
    arrival = client.post(
        "/v1/commands",
        json={
            "schema_version": 1,
            "event_id": "cmd_arrive",
            "occurred_at": "2026-10-01T12:00:00Z",
            "event_type": "customer.arrived",
            "payload": {},
        },
        headers=headers,
    ).json()
    return headers, arrival["conversation_id"]


def test_the_chef_bubble_sits_between_the_order_and_the_waiter_reply(settings) -> None:
    with TestClient(create_app(settings, waiter_factory=kitchen_waiter())) as client:
        headers, conversation_id = visit(client)
        assert say(client, headers, conversation_id, "cmd_order", "Pido una morcilla y una hamburguesa sin queso")[
            "status"
        ] == "completed"
        assert say(client, headers, conversation_id, "cmd_thanks", "Gracias")["status"] == "completed"
        snapshot = client.get(f"/v1/conversations/{conversation_id}/snapshot", headers=headers).json()

    messages = snapshot["messages"]
    assert [message["role"] for message in messages] == [
        "assistant", "user", "kitchen", "assistant", "user", "assistant",
    ]
    kitchen = messages[2]
    assert kitchen["text"] == REPORT.text
    assert kitchen["command_event_id"] == messages[1]["command_event_id"] == messages[3]["command_event_id"]
    assert ChatMessage.model_validate(kitchen).kitchen == REPORT
    # Every other message keeps the shape the previous view reads.
    assert all("kitchen" not in message for index, message in enumerate(messages) if index != 2)


async def test_the_kitchen_message_is_persisted_with_its_typed_result(settings, clock, commands) -> None:
    database = Database(settings.bff_database_path)
    memory_store = SQLiteMemoryStore(settings.bff_database_path.with_name("test-waiter-memory.db"))
    waiter = KitchenWaiter(
        LocalWaiter(ScriptedWaiterAgent(), mode="scripted", max_turns=20, memory_store=memory_store), FAILED
    )
    service = RestaurantService(database=database, waiter=waiter, clock=clock)
    session = service.authenticate(service.open_session("Ana").token)
    arrival = await service.submit(session, commands.arrive())
    await service.submit(session, commands.say(arrival.conversation_id, "Pido una morcilla"))
    await service.drain()

    reopened = Database(settings.bff_database_path)
    with reopened.read() as tx:
        messages = tx.list_messages(arrival.conversation_id)
        events = [json.loads(raw) for _, raw in tx.events_after(arrival.conversation_id, 0)]
    assert [message.role for message in messages] == ["assistant", "user", "kitchen", "assistant"]
    assert messages[2].kitchen == FAILED and messages[2].kitchen.result.code is KitchenFailureCode.NOT_CONFIGURED
    last_snapshot = [event for event in events if event["event_type"] == "snapshot.updated"][-1]
    assert [message["role"] for message in last_snapshot["snapshot"]["messages"]][-2:] == ["kitchen", "assistant"]


async def test_the_remote_waiter_passes_the_kitchen_report_through() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = {
            "status": "completed",
            "reply": "Cocina acepta la morcilla; la hamburguesa no está en la carta.",
            "customer": {"presented_name": "Ana"},
            "order_draft": {"items": []},
            "turn_count": 1,
            "kitchen": REPORT.model_dump(mode="json"),
        }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(body)}]}],
            },
        )

    waiter = RemoteWaiter("http://restaurant-agent:8088", timeout_seconds=3)
    await waiter._client.aclose()
    waiter._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    result = await waiter.take_turn(
        WaiterTurn(
            conversation_id="conv_1",
            actor=ActorContext(actor_id="ana", authenticated=True),
            presented_name="Ana",
            message="Pido una morcilla y una hamburguesa sin queso",
            customer=CustomerSnapshot(presented_name="Ana"),
            order_draft=OrderDraft(),
            turn_count=0,
            persisted_order_preferences=(),
            session_json=None,
            correlation_id="corr_1",
        )
    )
    await waiter.aclose()
    assert result.kitchen == REPORT
