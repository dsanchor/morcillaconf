import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    Action,
    DecideTableCommand,
    RestaurantSnapshot,
)
from restaurant_contracts.seating import RoomView, SeatingView

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/phase4"
COMMANDS = json.loads((FIXTURES / "commands.json").read_text())
SNAPSHOTS = json.loads((FIXTURES / "snapshots.json").read_text())
ROOMS = json.loads((FIXTURES / "rooms.json").read_text())
PHASE3 = json.loads((FIXTURES.parent / "phase3a" / "snapshots.json").read_text())


@pytest.mark.parametrize(
    ("adapter", "examples"),
    [
        (COMMAND_ADAPTER, COMMANDS),
        (TypeAdapter(RestaurantSnapshot), SNAPSHOTS),
        (TypeAdapter(RoomView), ROOMS),
    ],
)
def test_phase4_examples_round_trip_and_have_json_schema(adapter, examples) -> None:
    assert adapter.json_schema()
    for example in examples:
        model = adapter.validate_python(example)
        assert adapter.validate_json(adapter.dump_json(model)) == model


def test_decision_command_is_typed() -> None:
    command = COMMAND_ADAPTER.validate_python(COMMANDS[0])
    assert isinstance(command, DecideTableCommand)
    assert command.payload.decision == "confirmed"


@pytest.mark.parametrize(
    "payload",
    [
        {"proposal_id": "prop_demo", "version": 1, "decision": "maybe"},
        {"proposal_id": "prop_demo", "version": 0, "decision": "confirmed"},
        {"proposal_id": "prop_demo", "version": "1", "decision": "confirmed"},
        {"proposal_id": " ", "version": 1, "decision": "confirmed"},
        {"proposal_id": "prop_demo", "version": 1, "decision": "confirmed", "assignment_id": "seat_x"},
        {"proposal_id": "prop_demo", "version": 1, "decision": "confirmed", "table_id": "t"},
    ],
)
def test_decision_payload_rejects_invalid_or_internal_fields(payload) -> None:
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({**COMMANDS[0], "payload": payload})


@pytest.mark.parametrize("field", ["actor", "actor_id", "authenticated"])
def test_decision_rejects_client_identity(field) -> None:
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({**COMMANDS[0], field: "untrusted"})


def test_phase3_snapshots_default_to_no_seating() -> None:
    snapshot = RestaurantSnapshot.model_validate(PHASE3[1])
    assert snapshot.seating == SeatingView()


def test_decision_is_allowed_only_with_a_pending_proposal() -> None:
    seated = deepcopy(SNAPSHOTS[2])
    seated["allowed_actions"].append(Action.DECIDE_TABLE.value)
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(seated)
    none = deepcopy(PHASE3[1])
    none["allowed_actions"].append(Action.DECIDE_TABLE.value)
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(none)


@pytest.mark.parametrize(
    "seating",
    [
        {"status": "none", "party_size": 2},
        {"status": "proposed"},
        {"status": "seated", "party_size": 2},
        {"status": "seated", "place": {"place_id": "t", "kind": "table", "label": "Mesa", "capacity": 2, "seats": []}, "party_size": 3, "seated_at": "2026-09-29T20:00:00Z"},
        {"status": "seated", "place": {"place_id": "b", "kind": "bar", "label": "Barra", "capacity": 8, "seats": [1, 3]}, "party_size": 2, "seated_at": "2026-09-29T20:00:00Z"},
        {"status": "seated", "place": {"place_id": "b", "kind": "bar", "label": "Barra", "capacity": 8, "seats": [1, 2]}, "party_size": 3, "seated_at": "2026-09-29T20:00:00Z"},
        {"status": "seated", "place": {"place_id": "t", "kind": "table", "label": "Mesa", "capacity": 4, "seats": [1]}, "party_size": 1, "seated_at": "2026-09-29T20:00:00Z"},
        {"status": "seated", "place": {"place_id": "t", "kind": "table", "label": "Mesa", "capacity": 4, "seats": []}, "party_size": 1, "seated_at": "2026-09-29T20:00:00", "visit_id": "v"},
    ],
)
def test_seating_view_is_consistent(seating) -> None:
    with pytest.raises(ValidationError):
        SeatingView.model_validate(seating)


def _room(**changes: object) -> dict:
    room = deepcopy(ROOMS[1])
    room["places"][changes.pop("index", 0)].update(changes)
    return room


@pytest.mark.parametrize(
    "room",
    [
        _room(state="free"),
        _room(index=3, party_size=2),
        _room(index=3, mine=True),
        _room(party_size=3),
        _room(seats=[{"position": 1, "state": "free", "mine": False}]),
        _room(index=5, party_size=2),
        _room(index=5, mine=True),
        _room(index=5, seats=[{"position": 1, "state": "free", "mine": False}]),
        _room(index=1, place_id="table-01"),
        _room(visit_id="visit_other"),
        _room(name="Luis"),
        {**ROOMS[0], "places": ROOMS[1]["places"]},
    ],
)
def test_room_view_is_anonymous_and_consistent(room) -> None:
    with pytest.raises(ValidationError):
        RoomView.model_validate(room)


def test_room_marks_only_the_callers_places() -> None:
    room = RoomView.model_validate(ROOMS[1])
    assert [place.place_id for place in room.places if place.mine] == ["table-03"]
    assert room.places[-1].seats[2].state == "held"
