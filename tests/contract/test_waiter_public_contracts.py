import json
from pathlib import Path

from restaurant_agent import contracts as waiter
from restaurant_agent.memory import contracts as memory
from restaurant_contracts import customer as public_customer
from restaurant_contracts import memory as public_memory
from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    CompletedCommandResult,
    ResponseTextDelta,
    RestaurantSnapshot,
    SnapshotUpdated,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/phase3a"


def test_waiter_reexports_the_same_public_types() -> None:
    for name in (
        "CustomerSnapshot", "OrderDraft", "OrderItemDraft", "DraftItemStatus",
        "PendingField",
    ):
        assert getattr(waiter, name) is getattr(public_customer, name)
    for name in ("MemoryKind", "MemoryCandidate"):
        assert getattr(memory, name) is getattr(public_memory, name)


def test_waiter_response_shape_and_defaults_are_preserved() -> None:
    response = waiter.WaiterResponse(
        conversation_id="conv_demo", correlation_id="corr_message", turn_number=1,
        reply="Hola", customer=waiter.CustomerSnapshot(presented_name="Majo"),
        order_draft=waiter.OrderDraft(items=[waiter.OrderItemDraft(name="agua")]),
        pending_fields=[],
    )
    assert response.model_dump(mode="json") == {
        "conversation_id": "conv_demo",
        "correlation_id": "corr_message",
        "turn_number": 1,
        "reply": "Hola",
        "customer": {
            "presented_name": "Majo", "party_size": 1, "preferences": [],
            "restrictions": [],
        },
        "order_draft": {
            "items": [{"name": "agua", "quantity": 1, "notes": [], "status": "unverified"}],
        },
        "pending_fields": [],
        "remembered_memories": [],
    }
    assert waiter.CustomerSnapshot(party_size=None).party_size is None
    assert waiter.WaiterModelResult.model_json_schema()


def test_fixture_flow_correlates_commands_results_and_reconnection() -> None:
    commands = [
        COMMAND_ADAPTER.validate_python(value)
        for value in json.loads((FIXTURES / "commands.json").read_text())
    ]
    events = [
        STREAM_EVENT_ADAPTER.validate_python(value)
        for value in json.loads((FIXTURES / "events.json").read_text())
    ]
    results = [
        COMMAND_RESULT_ADAPTER.validate_python(value)
        for value in json.loads((FIXTURES / "results.json").read_text())
    ]
    snapshots = [
        RestaurantSnapshot.model_validate(value)
        for value in json.loads((FIXTURES / "snapshots.json").read_text())
    ]
    command = commands[1]
    assert all(event.command_event_id == command.event_id for event in events)
    assert [event.cursor for event in events] == [1, 2, 3, 4]
    assert len({event.event_id for event in events}) == len(events)
    assert isinstance(events[1], ResponseTextDelta)
    assert isinstance(events[2], SnapshotUpdated)
    assert events[2].snapshot == snapshots[1]
    assert results[0].event_id == results[1].event_id == command.event_id
    assert isinstance(results[1], CompletedCommandResult)
    assert results[1].cursor == snapshots[1].cursor
    assert [event.cursor for event in events if event.cursor > snapshots[1].cursor] == [4]
    assert COMMAND_ADAPTER.validate_json(command.model_dump_json()) == command
