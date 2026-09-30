import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from restaurant_mcp.config import Settings
from restaurant_mcp.main import create_server
from restaurant_mcp.seating import (
    IdempotencyConflict,
    NoSeatingAvailable,
    SeatingConflict,
    SeatingLayout,
    SQLiteSeatingRepository,
)


@pytest.fixture
def layout() -> SeatingLayout:
    return SeatingLayout.model_validate(
        {
            "resources": [
                {"resource_id": "table-2", "kind": "table", "label": "Mesa 2", "capacity": 2, "display_order": 20},
                {"resource_id": "table-4", "kind": "table", "label": "Mesa 4", "capacity": 4, "display_order": 10},
                {"resource_id": "bar", "kind": "bar", "label": "Barra", "capacity": 5, "display_order": 100, "seat_prefix": "bar-seat"},
            ]
        }
    )


def repository(tmp_path, layout: SeatingLayout) -> SQLiteSeatingRepository:
    return SQLiteSeatingRepository(tmp_path / "seating.db", layout_id="v1", layout=layout)


def test_table_selects_smallest_sufficient_capacity(tmp_path, layout):
    assignment = repository(tmp_path, layout).hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1")
    assert assignment.resource_id == "table-2"
    assert assignment.status == "held"


def test_bar_assigns_contiguous_seats_and_best_fit(tmp_path, layout):
    repo = repository(tmp_path, layout)
    first = repo.hold(visit_id="visit-1", party_size=2, preference="bar", idempotency_key="evt-1")
    assert first.seat_ids == ["bar-seat-01", "bar-seat-02"]
    repo.confirm(assignment_id=first.assignment_id, visit_id="visit-1", expected_version=1, idempotency_key="confirm-1")
    second = repo.hold(visit_id="visit-2", party_size=2, preference="bar", idempotency_key="evt-2")
    assert second.seat_ids == ["bar-seat-03", "bar-seat-04"]


def test_hold_is_idempotent_but_rejects_payload_change(tmp_path, layout):
    repo = repository(tmp_path, layout)
    first = repo.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1")
    assert repo.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1") == first
    with pytest.raises(IdempotencyConflict):
        repo.hold(visit_id="visit-1", party_size=3, preference="table", idempotency_key="evt-1")


def test_expired_hold_cannot_be_confirmed_and_releases_capacity(tmp_path, layout):
    repo = repository(tmp_path, layout)
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    held = repo.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1", now=moment)
    with pytest.raises(SeatingConflict):
        repo.confirm(assignment_id=held.assignment_id, visit_id="visit-1", expected_version=1, idempotency_key="confirm-1", now=moment + timedelta(minutes=6))
    replacement = repo.hold(visit_id="visit-2", party_size=2, preference="table", idempotency_key="evt-2", now=moment + timedelta(minutes=6))
    assert replacement.resource_id == "table-2"


def test_layout_id_change_deletes_only_seating_state(tmp_path, layout):
    path = tmp_path / "seating.db"
    first = SQLiteSeatingRepository(path, layout_id="v1", layout=layout)
    first.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1")
    changed = SeatingLayout.model_validate({"resources": [{"resource_id": "bar", "kind": "bar", "label": "Barra", "capacity": 3, "display_order": 1, "seat_prefix": "bar-seat"}]})
    second = SQLiteSeatingRepository(path, layout_id="v2", layout=changed)
    assert second.availability() == [{"resource_id": "bar", "kind": "bar", "label": "Barra", "capacity": 3, "available_seats": 3, "largest_group": 3}]
    with pytest.raises(NoSeatingAvailable):
        second.hold(visit_id="visit-2", party_size=2, preference="table", idempotency_key="evt-2")


def test_layout_json_change_with_same_id_preserves_existing_state(tmp_path, layout):
    path = tmp_path / "seating.db"
    first = SQLiteSeatingRepository(path, layout_id="v1", layout=layout)
    held = first.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1")
    changed = SeatingLayout.model_validate(
        {"resources": [{"resource_id": "replacement", "kind": "table", "label": "Nueva", "capacity": 8, "display_order": 1}]}
    )

    second = SQLiteSeatingRepository(path, layout_id="v1", layout=changed)

    assert second.seating_map("visit-1")["visit"]["assignment_id"] == held.assignment_id
    assert {item["resource_id"] for item in second.availability()} == {"table-2", "table-4", "bar"}


def test_legacy_layout_hash_metadata_is_removed_without_reset(tmp_path, layout):
    path = tmp_path / "seating.db"
    first = SQLiteSeatingRepository(path, layout_id="v1", layout=layout)
    held = first.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="evt-1")
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE layout_metadata ADD COLUMN layout_hash TEXT NOT NULL DEFAULT 'legacy'")

    second = SQLiteSeatingRepository(path, layout_id="v1", layout=layout)

    assert second.seating_map("visit-1")["visit"]["assignment_id"] == held.assignment_id
    with sqlite3.connect(path) as db:
        assert "layout_hash" not in {row[1] for row in db.execute("PRAGMA table_info(layout_metadata)")}


def test_layout_rejects_duplicate_generated_bar_seat_ids():
    with pytest.raises(ValueError, match="globally unique"):
        SeatingLayout.model_validate(
            {"resources": [
                {"resource_id": "bar-a", "kind": "bar", "label": "Barra A", "capacity": 2, "display_order": 1, "seat_prefix": "bar-seat"},
                {"resource_id": "bar-b", "kind": "bar", "label": "Barra B", "capacity": 2, "display_order": 2, "seat_prefix": "bar-seat"},
            ]}
        )


def test_confirmation_and_release_are_idempotent(tmp_path, layout):
    repo = repository(tmp_path, layout)
    held = repo.hold(visit_id="visit-1", party_size=2, preference="table", idempotency_key="hold-1")
    occupied = repo.confirm(assignment_id=held.assignment_id, visit_id="visit-1", expected_version=1, idempotency_key="confirm-1")
    assert repo.confirm(assignment_id=held.assignment_id, visit_id="visit-1", expected_version=1, idempotency_key="confirm-1") == occupied
    released = repo.release(assignment_id=held.assignment_id, visit_id="visit-1", expected_version=2, idempotency_key="release-1")
    assert repo.release(assignment_id=held.assignment_id, visit_id="visit-1", expected_version=2, idempotency_key="release-1") == released


def test_server_registers_seating_tools(tmp_path, layout, monkeypatch):
    monkeypatch.setenv("SEATING_DATABASE_PATH", str(tmp_path / "seating.db"))
    monkeypatch.setenv("SEATING_LAYOUT_ID", "v1")
    monkeypatch.setenv("SEATING_LAYOUT_JSON", layout.model_dump_json())
    server = create_server(Settings())
    assert {"get_seating_availability", "hold_seating", "confirm_seating", "confirm_solo_seating", "cancel_seating_hold", "get_seating_map", "release_seating"} <= {
        tool.name for tool in server._tool_manager.list_tools()
    }
