import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from restaurant_contracts.application import ChatMessage, RestaurantSnapshot
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    KITCHEN_RESULT_ADAPTER,
    STATION_LABELS,
    STATION_ORDER,
    AcceptedItem,
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenSource,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)
from restaurant_contracts.waiter import WAITER_TURN_RESPONSE_ADAPTER, WaiterTurnSuccess

AT = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
ORDER = KitchenOrder(
    order_id="ko_1",
    lines=[
        KitchenOrderLine(line=1, name="morcilla a la brasa"),
        KitchenOrderLine(line=2, name="croquetas de morcilla", quantity=2, modifications=["sin cebolla"]),
    ],
    restrictions=["celiaquía"],
)
MORCILLA = AcceptedItem(
    line=1,
    carta_id="morcilla-de-burgos-a-la-brasa",
    name="Morcilla de Burgos a la brasa",
    quantity=1,
    station=KitchenStation.BRASA,
)
CROQUETAS = RejectedItem(
    line=2,
    requested="croquetas de morcilla, sin cebolla",
    quantity=2,
    reason="Contienen cereales con gluten y has indicado celiaquía.",
    carta_id="croquetas-de-morcilla",
)
GRILL = StationPlan(
    station=KitchenStation.BRASA,
    tasks=[
        StationTask(
            line=1,
            carta_id="morcilla-de-burgos-a-la-brasa",
            name="Morcilla de Burgos a la brasa",
            quantity=1,
            steps=["Marcar las rodajas en la zona templada de la parrilla."],
            precautions=["Pinzas limpias: el cliente es celíaco."],
        )
    ],
)


def plan(**changes) -> KitchenPlan:
    values = {
        "order_id": "ko_1",
        "accepted": [MORCILLA],
        "rejected": [CROQUETAS],
        "warnings": ["La casa no ofrece platos certificados sin gluten."],
        "stations": [GRILL],
        "sources": [KitchenSource(document="carta de la casa", version="1")],
    }
    values.update(changes)
    return KitchenPlan(**values)


def report(result=None, order: KitchenOrder = ORDER) -> KitchenReport:
    return KitchenReport(order=order, result=result or plan(), text="Plan de cocina")


def test_a_report_round_trips_with_a_json_schema() -> None:
    value = report()
    assert TypeAdapter(KitchenReport).json_schema()
    assert KitchenReport.model_validate_json(value.model_dump_json()) == value
    assert value.result.verdict == "partial"
    failure = KitchenFailure(order_id="ko_1", code=KitchenFailureCode.TIMEOUT, message="Cocina no ha respondido a tiempo.")
    assert KITCHEN_RESULT_ADAPTER.validate_json(failure.model_dump_json()) == failure
    assert report(failure).result.status == "failed"


def test_the_stations_are_a_fixed_set_in_kitchen_order() -> None:
    assert [station.value for station in STATION_ORDER] == ["brasa", "fritos", "pinchos_frios", "barra"]
    assert STATION_LABELS[KitchenStation.PINCHOS_FRIOS] == "Pinchos fríos"
    with pytest.raises(ValidationError):
        StationPlan.model_validate({"station": "postres", "tasks": [GRILL.tasks[0].model_dump()]})


def test_order_lines_are_numbered_in_order_and_carry_no_customer_data() -> None:
    assert set(KitchenOrder.model_fields) == {"schema_version", "order_id", "lines", "restrictions"}
    with pytest.raises(ValidationError):
        KitchenOrder(order_id="ko_1", lines=[KitchenOrderLine(line=2, name="morcilla")])
    with pytest.raises(ValidationError):
        KitchenOrder(order_id="ko_1", lines=[])
    with pytest.raises(ValidationError):
        KitchenOrder.model_validate({**ORDER.model_dump(), "customer": {"presented_name": "Ana"}})


def test_no_line_is_both_accepted_and_rejected() -> None:
    both = CROQUETAS.model_copy(update={"line": 1, "quantity": 1})
    with pytest.raises(ValidationError, match="both accepted and rejected"):
        plan(rejected=[both])
    with pytest.raises(ValidationError, match="decided once"):
        plan(rejected=[CROQUETAS, CROQUETAS])
    with pytest.raises(ValidationError, match="at least one"):
        plan(accepted=[], rejected=[], stations=[])


def test_every_accepted_line_has_one_matching_task_in_its_station() -> None:
    with pytest.raises(ValidationError, match="exactly one station task"):
        plan(stations=[])
    fried = GRILL.model_copy(update={"station": KitchenStation.FRITOS})
    with pytest.raises(ValidationError, match="matches its accepted line"):
        plan(stations=[fried])
    with pytest.raises(ValidationError, match="kitchen order"):
        plan(stations=[GRILL, GRILL])
    with pytest.raises(ValidationError):
        AcceptedItem.model_validate({**MORCILLA.model_dump(), "carta_id": "Morcilla a la brasa"})


def test_a_report_answers_its_order_and_keeps_every_line_and_quantity() -> None:
    with pytest.raises(ValidationError, match="answers this order"):
        report(plan(order_id="ko_2"))
    with pytest.raises(ValidationError, match="every order line"):
        report(plan(rejected=[]))
    with pytest.raises(ValidationError, match="ordered quantities"):
        report(plan(rejected=[CROQUETAS.model_copy(update={"quantity": 1})]))


def test_kitchen_messages_carry_their_report_and_the_others_keep_their_shape() -> None:
    kitchen = ChatMessage(
        message_id="msg_k", role="kitchen", text="x" * 3_000, occurred_at=AT,
        command_event_id="cmd_1", kitchen=report(),
    )
    assert ChatMessage.model_validate_json(kitchen.model_dump_json()) == kitchen
    waiter = ChatMessage(
        message_id="msg_w", role="assistant", text="Hola", occurred_at=AT, command_event_id="cmd_1"
    )
    assert "kitchen" not in json.loads(waiter.model_dump_json())
    with pytest.raises(ValidationError, match="only they"):
        ChatMessage(message_id="m", role="kitchen", text="Plan", occurred_at=AT, command_event_id="c")
    with pytest.raises(ValidationError, match="only they"):
        waiter.model_validate({**waiter.model_dump(), "kitchen": report().model_dump()})
    with pytest.raises(ValidationError, match="2000"):
        ChatMessage(message_id="m", role="user", text="x" * 2_001, occurred_at=AT, command_event_id="c")


def test_a_snapshot_shows_the_kitchen_between_the_customer_and_the_waiter() -> None:
    messages = [
        ChatMessage(message_id="m1", role="user", text="Una morcilla", occurred_at=AT, command_event_id="c"),
        ChatMessage(message_id="m2", role="kitchen", text="Plan", occurred_at=AT, command_event_id="c", kitchen=report()),
        ChatMessage(message_id="m3", role="assistant", text="Cocina acepta la morcilla.", occurred_at=AT, command_event_id="c"),
    ]
    snapshot = RestaurantSnapshot(
        schema_version=1, visit_id="visit_1", conversation_id="conv_1",
        identity={"actor_id": "ana", "authenticated": True}, cursor=1, messages=messages,
        customer=CustomerSnapshot(presented_name="Ana"), order_draft=OrderDraft(),
        pending_fields=[], memory={"memories": []}, process_status="idle", allowed_actions=[],
    )
    assert RestaurantSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_the_waiter_turn_carries_the_kitchen_report_only_when_there_is_one() -> None:
    plain = WaiterTurnSuccess(
        reply="Hola", customer=CustomerSnapshot(presented_name="Ana"), order_draft=OrderDraft(), turn_count=1
    )
    assert "kitchen" not in json.loads(plain.model_dump_json())
    with_kitchen = plain.model_copy(update={"kitchen": report()})
    parsed = WAITER_TURN_RESPONSE_ADAPTER.validate_json(with_kitchen.model_dump_json())
    assert parsed == with_kitchen and parsed.kitchen.result.verdict == "partial"
