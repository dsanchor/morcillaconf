import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from restaurant_mcp import layout as layout_cli
from restaurant_mcp.config import Settings
from restaurant_mcp.main import create_server
from restaurant_mcp.seating import (
    IdempotencyConflict,
    NoSeatingAvailable,
    SeatingConflict,
    SeatingExpired,
    SeatingLayout,
    SeatingNotFound,
    SQLiteSeatingRepository,
)

DEMO_LAYOUT = Path(__file__).resolve().parents[1] / "layouts" / "morcillaconf-demo-v1.json"
MOMENT = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)


@pytest.fixture
def demo() -> SeatingLayout:
    return SeatingLayout.model_validate(json.loads(DEMO_LAYOUT.read_text()))


def repo_for(tmp_path, layout: SeatingLayout, hold_minutes: int = 5) -> SQLiteSeatingRepository:
    return SQLiteSeatingRepository(
        tmp_path / "seating.db", layout_id="demo", layout=layout, expected_hash=layout.fingerprint(), hold_minutes=hold_minutes
    )


def hold(repo, visit, size, preference="any", key=None, now=MOMENT):
    return repo.hold(visit_id=visit, party_size=size, preference=preference, idempotency_key=key or f"{visit}:{size}:{preference}", now=now)


def test_demo_layout_is_valid_and_ordered(demo) -> None:
    kinds = [(item.kind, item.capacity) for item in sorted(demo.resources, key=lambda item: item.display_order)]
    assert kinds == [("table", 2), ("table", 2), ("table", 4), ("table", 4), ("table", 6), ("bar", 8)]


def test_best_fit_by_size(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    assert hold(repo, "v1", 3).resource_id == "table-03"
    assert hold(repo, "v2", 2).resource_id == "table-01"
    assert hold(repo, "v3", 5).resource_id == "table-05"


@pytest.mark.parametrize("status", ["held", "occupied"])
def test_a_table_holds_one_group_even_with_free_chairs(tmp_path, demo, status) -> None:
    repo = repo_for(tmp_path, demo)
    first = hold(repo, "v1", 1, "table")
    assert first.resource_id == "table-01"
    if status == "occupied":
        repo.confirm(assignment_id=first.assignment_id, visit_id="v1", expected_version=1, idempotency_key="c1", now=MOMENT)
    assert hold(repo, "v2", 1, "table").resource_id == "table-02"


def test_any_falls_back_to_the_bar_and_table_does_not(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    for number, size in enumerate((2, 2, 4, 4, 6)):
        hold(repo, f"t{number}", size, "table")
    with pytest.raises(NoSeatingAvailable, match="^no_seating: "):
        hold(repo, "late", 2, "table")
    seated = hold(repo, "late", 2, "any", key="late-any")
    assert (seated.resource_kind, seated.seat_ids) == ("bar", ["bar-seat-01", "bar-seat-02"])


def test_too_large_group_gets_no_seating(tmp_path, demo) -> None:
    with pytest.raises(NoSeatingAvailable):
        hold(repo_for(tmp_path, demo), "v1", 9)


def test_fresh_bar_fills_in_order_without_holes(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    assert hold(repo, "v1", 2, "bar").seat_ids == ["bar-seat-01", "bar-seat-02"]
    assert hold(repo, "v2", 3, "bar").seat_ids == ["bar-seat-03", "bar-seat-04", "bar-seat-05"]
    assert hold(repo, "v3", 1, "bar").seat_ids == ["bar-seat-06"]


def test_bar_prefers_the_smallest_gap_then_the_lowest_position(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    first = hold(repo, "v1", 2, "bar")  # 1-2
    middle = hold(repo, "v2", 2, "bar")  # 3-4
    hold(repo, "v3", 1, "bar")  # 5
    repo.cancel(assignment_id=middle.assignment_id, visit_id="v2", expected_version=1, idempotency_key="x", now=MOMENT)
    # Free runs: 3-4 (exact for two) and 6-8.
    assert hold(repo, "v4", 2, "bar").seat_ids == ["bar-seat-03", "bar-seat-04"]
    repo.cancel(assignment_id=first.assignment_id, visit_id="v1", expected_version=1, idempotency_key="y", now=MOMENT)
    # Free runs: 1-2 and 6-8; one person fits 1-2 with a smaller gap.
    assert hold(repo, "v5", 1, "bar").seat_ids == ["bar-seat-01"]


def test_only_one_of_two_parallel_holds_wins_the_last_table(tmp_path) -> None:
    layout = SeatingLayout.model_validate({"resources": [{"resource_id": "t", "kind": "table", "label": "Mesa", "capacity": 4, "display_order": 1}]})
    repo_for(tmp_path, layout)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt(visit: str) -> None:
        own = repo_for(tmp_path, layout)
        barrier.wait()
        try:
            own.hold(visit_id=visit, party_size=2, preference="table", idempotency_key=visit)
            outcomes.append("held")
        except NoSeatingAvailable:
            outcomes.append("none")

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["held", "none"]


def test_cancel_frees_the_place_at_once_and_is_idempotent(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    held = hold(repo, "v1", 2, "table")
    cancelled = repo.cancel(assignment_id=held.assignment_id, visit_id="v1", expected_version=1, idempotency_key="k", now=MOMENT)
    assert (cancelled.status, cancelled.version) == ("cancelled", 2)
    assert repo.cancel(assignment_id=held.assignment_id, visit_id="v1", expected_version=1, idempotency_key="k", now=MOMENT) == cancelled
    with pytest.raises(IdempotencyConflict):
        repo.cancel(assignment_id=held.assignment_id, visit_id="v1", expected_version=2, idempotency_key="k", now=MOMENT)
    assert hold(repo, "v2", 2, "table").resource_id == "table-01"


def test_cancel_checks_owner_version_and_status(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    held = hold(repo, "v1", 2, "table")
    with pytest.raises(SeatingNotFound, match="^not_found: "):
        repo.cancel(assignment_id=held.assignment_id, visit_id="intruder", expected_version=1, idempotency_key="a", now=MOMENT)
    with pytest.raises(SeatingConflict, match="^conflict: "):
        repo.cancel(assignment_id=held.assignment_id, visit_id="v1", expected_version=7, idempotency_key="b", now=MOMENT)
    repo.confirm(assignment_id=held.assignment_id, visit_id="v1", expected_version=1, idempotency_key="c", now=MOMENT)
    with pytest.raises(SeatingConflict):
        repo.cancel(assignment_id=held.assignment_id, visit_id="v1", expected_version=2, idempotency_key="d", now=MOMENT)


def test_expired_holds_report_expiry_and_free_the_place(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo, hold_minutes=1)
    held = hold(repo, "v1", 2, "table")
    later = MOMENT + timedelta(minutes=2)
    with pytest.raises(SeatingExpired, match="^expired: "):
        repo.confirm(assignment_id=held.assignment_id, visit_id="v1", expected_version=1, idempotency_key="c", now=later)
    with pytest.raises(SeatingExpired):
        repo.cancel(assignment_id=held.assignment_id, visit_id="v1", expected_version=1, idempotency_key="d", now=later)
    assert hold(repo, "v2", 2, "table", now=later).resource_id == "table-01"


def test_a_new_request_replaces_the_pending_hold_atomically(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    first = hold(repo, "v1", 2, key="k1")
    second = hold(repo, "v1", 4, key="k2")
    assert second.resource_id == "table-03"
    assert repo.seating_map("v1", now=MOMENT)["visit"]["assignment_id"] == second.assignment_id
    assert hold(repo, "v2", 2).resource_id == first.resource_id
    # Nothing fits: the transaction rolls back and the pending hold survives.
    with pytest.raises(NoSeatingAvailable):
        hold(repo, "v1", 12, key="k3")
    assert repo.seating_map("v1", now=MOMENT)["visit"]["status"] == "held"


def test_an_occupied_visit_cannot_hold_again(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    held = hold(repo, "v1", 2)
    repo.confirm(assignment_id=held.assignment_id, visit_id="v1", expected_version=1, idempotency_key="c", now=MOMENT)
    with pytest.raises(SeatingConflict):
        hold(repo, "v1", 3, key="again")


def test_map_is_anonymous_and_flags_the_callers_places(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    mine = hold(repo, "v1", 3)
    other = hold(repo, "v2", 2, "bar")
    repo.confirm(assignment_id=other.assignment_id, visit_id="v2", expected_version=1, idempotency_key="c", now=MOMENT)
    room = repo.seating_map("v1", now=MOMENT)
    assert "v2" not in json.dumps(room) and other.assignment_id not in json.dumps(room)
    places = {item["resource_id"]: item for item in room["resources"]}
    assert places["table-03"] | {"seats": []} == {
        "resource_id": "table-03", "kind": "table", "label": "Mesa 3", "capacity": 4, "display_order": 30,
        "state": "held", "party_size": 3, "mine": True, "seats": [],
    }
    assert places["table-01"]["state"] == "free" and places["table-01"]["party_size"] is None
    bar = places["bar"]
    assert [seat["state"] for seat in bar["seats"]] == ["occupied", "occupied", *["free"] * 6]
    assert not bar["mine"] and bar["state"] == "free"
    assert room["visit"]["assignment_id"] == mine.assignment_id
    assert repo.seating_map(now=MOMENT)["visit"] is None


def test_map_reports_the_latest_assignment_in_any_status(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo, hold_minutes=1)
    hold(repo, "v1", 2)
    later = repo.seating_map("v1", now=MOMENT + timedelta(minutes=5))
    assert later["visit"]["status"] == "expired"
    assert all(item["state"] == "free" for item in later["resources"])


def test_availability_is_coherent_with_exclusive_tables(tmp_path, demo) -> None:
    repo = repo_for(tmp_path, demo)
    hold(repo, "v1", 1, "table", now=datetime.now(UTC))
    hold(repo, "v2", 3, "bar", now=datetime.now(UTC))
    rows = {row["resource_id"]: row for row in repo.availability()}
    assert rows["table-01"]["available_seats"] == 0 and rows["table-01"]["largest_group"] == 0
    assert rows["table-05"]["largest_group"] == 6
    assert rows["bar"]["available_seats"] == 5 and rows["bar"]["largest_group"] == 5


def test_layout_cli_prints_the_service_fingerprint(demo, capsys) -> None:
    layout_cli.main([str(DEMO_LAYOUT), "--sha256"])
    assert capsys.readouterr().out.strip() == demo.fingerprint()
    layout_cli.main([str(DEMO_LAYOUT), "--json"])
    assert capsys.readouterr().out.strip() == demo.canonical_json()


def test_tools_report_stable_error_codes(tmp_path, demo, monkeypatch) -> None:
    monkeypatch.setenv("SEATING_DATABASE_PATH", str(tmp_path / "seating.db"))
    monkeypatch.setenv("SEATING_LAYOUT_ID", "demo")
    monkeypatch.setenv("SEATING_LAYOUT_JSON", demo.canonical_json())
    monkeypatch.setenv("SEATING_LAYOUT_SHA256", demo.fingerprint())
    server = create_server(Settings(_env_file=None))

    async def scenario() -> tuple[object, object, object]:
        held = await server.call_tool("hold_seating", {"party_size": 2, "preference": "table", "visit_id": "v1", "idempotency_key": "k"})
        room = await server.call_tool("get_seating_map", {"visit_id": "v1"})
        try:
            await server.call_tool("hold_seating", {"party_size": 30, "preference": "any", "visit_id": "v2", "idempotency_key": "z"})
        except Exception as error:  # FastMCP wraps tool errors
            return held, room, error
        return held, room, None

    held, room, error = asyncio.run(scenario())
    assert "table-01" in json.dumps(held, default=str)
    assert '"mine": true' in json.dumps(room, default=str).replace('\\"', '"')
    assert error is not None and "no_seating: " in str(error)
