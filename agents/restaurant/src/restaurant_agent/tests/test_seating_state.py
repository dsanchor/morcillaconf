import json

import pytest
from agent_framework import Content

from restaurant_agent.seating import (
    LAST_OUTCOME_KEY,
    ROOM_KEY,
    SEATED_KEY,
    apply_room,
    bind_visit,
    build_report,
    card_reply,
    clear_proposal,
    error_code,
    next_hold_key,
    pending_confirm_request,
    pending_proposal,
    place_text,
    result_object,
    seating_instructions,
    store_proposal,
)

HELD = {
    "assignment_id": "seat-1",
    "visit_id": "visit_bff",
    "resource_id": "table-03",
    "resource_kind": "table",
    "seat_ids": [],
    "party_size": 3,
    "status": "held",
    "expires_at": "2026-09-29T20:05:00+00:00",
    "version": 1,
    "resource_label": "Mesa 3",
}
BAR = HELD | {
    "assignment_id": "seat-2",
    "resource_id": "bar",
    "resource_kind": "bar",
    "seat_ids": ["bar-seat-03", "bar-seat-04"],
    "party_size": 2,
    "resource_label": "Barra",
}


def room(visit=None, **table03):
    return {
        "layout_id": "demo",
        "resources": [
            {
                "resource_id": "table-03", "kind": "table", "label": "Mesa 3", "capacity": 4,
                "display_order": 30, "state": "free", "party_size": None, "mine": False,
                "expires_at": None, "seats": [], **table03,
            },
            {
                "resource_id": "bar", "kind": "bar", "label": "Barra", "capacity": 8,
                "display_order": 100, "state": "free", "party_size": None, "mine": False,
                "expires_at": None,
                "seats": [
                    {"seat_id": f"bar-seat-{n:02d}", "position": n, "state": "free", "mine": False, "expires_at": None}
                    for n in range(1, 9)
                ],
            },
        ],
        "visit": visit,
    }


def proposed(result=HELD):
    state: dict = {}
    bind_visit(state, "visit_bff")
    _, key, fingerprint = next_hold_key(state, result["party_size"], "any")
    assert store_proposal(state, result, idempotency_key=key, fingerprint=fingerprint)
    return state


def test_bind_visit_seeds_the_application_visit_and_drops_foreign_places() -> None:
    state = {
        "visit_context": {"visit_id": "visit_random", "hold_sequence": 3, "hold_requests": {}},
        "seating_proposal": dict(HELD),
        SEATED_KEY: {"assignment_id": "x"},
    }
    bind_visit(state, "visit_bff")
    assert state["visit_context"] == {"visit_id": "visit_bff", "hold_sequence": 0, "hold_requests": {}}
    assert pending_proposal(state) is None and SEATED_KEY not in state
    with pytest.raises(ValueError):
        bind_visit(state, "  ")


def test_hold_keys_rotate_unless_the_pending_request_repeats() -> None:
    state: dict = {}
    bind_visit(state, "visit_bff")
    _, first, fingerprint = next_hold_key(state, 3, "any")
    assert first.startswith("seating:visit_bff:1-")
    store_proposal(state, HELD, idempotency_key=first, fingerprint=fingerprint)
    assert next_hold_key(state, 3, "any")[1] == first
    assert next_hold_key(state, 4, "any")[1].startswith("seating:visit_bff:2-")
    clear_proposal(state)
    third = next_hold_key(state, 3, "any")[1]
    assert third.startswith("seating:visit_bff:3-") and third != first


def test_only_held_results_become_proposals() -> None:
    state: dict = {}
    assert not store_proposal(state, HELD | {"status": "expired"}, idempotency_key="k", fingerprint="3:any")
    assert not store_proposal(state, {"resource_id": "x"}, idempotency_key="k", fingerprint="3:any")
    assert pending_proposal(state) is None


def test_the_room_confirms_expires_and_resets_the_own_place() -> None:
    state = proposed()
    apply_room(state, room(HELD | {"version": 1}, state="held", party_size=3, mine=True))
    assert pending_proposal(state) is not None

    apply_room(state, room(HELD | {"status": "occupied", "version": 2}, state="occupied", party_size=3, mine=True))
    assert pending_proposal(state) is None
    assert state[SEATED_KEY]["label"] == "Mesa 3"
    assert state[LAST_OUTCOME_KEY] == {"decision": "confirmed", "place": "Mesa 3"}

    apply_room(state, room(None))
    assert SEATED_KEY not in state
    assert state[LAST_OUTCOME_KEY] == {"decision": "cancelled", "place": "Mesa 3"}

    expiring = proposed()
    apply_room(expiring, room(HELD | {"status": "expired", "version": 2}))
    assert pending_proposal(expiring) is None
    assert expiring[LAST_OUTCOME_KEY] == {"decision": "expired", "place": "Mesa 3"}


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("rejected", "el cliente la rechazó. Ya no existe"),
        ("expired", "caducó sin confirmar. Ya no existe"),
        ("cancelled", "se anuló. Ya no existe"),
        ("confirmed", "el cliente la confirmó."),
    ],
)
def test_the_last_outcome_is_explained_to_the_model(decision, expected) -> None:
    state: dict = {ROOM_KEY: room(), LAST_OUTCOME_KEY: {"decision": decision, "place": "Mesa 4"}}
    text = seating_instructions(state)
    assert "La última propuesta (Mesa 4): " in text and expected in text
    assert "nunca una que solo esté en el historial" in text
    assert "llama a seating_hold_seating y describe solo lo que devuelva" in text


def test_instructions_describe_a_pending_bar_proposal() -> None:
    state = proposed(BAR)
    state[ROOM_KEY] = room()
    text = seating_instructions(state)
    status = json.loads(text.split("\n")[1])
    assert status == {
        "status": "proposed", "place": "Barra", "kind": "bar", "party_size": 2,
        "expires_at": BAR["expires_at"], "seats": [3, 4],
    }
    assert "«Confirmar» o «Rechazar»" in text
    assert seating_instructions({}) is None


def test_the_report_is_anonymous_and_carries_the_card() -> None:
    state = proposed(BAR)
    state[ROOM_KEY] = room(BAR)
    report = build_report(state, awaiting_decision=True)
    assert report["status"] == "proposed" and report["awaiting_decision"] is True
    assert report["place"] == {"place_id": "bar", "kind": "bar", "label": "Barra", "capacity": 8, "seats": [3, 4]}
    assert report["token"] and "seat-2" not in json.dumps(report)
    assert all("mine" not in place and "seat_id" not in json.dumps(place) for place in report["room"])
    assert card_reply(state) == "Os propongo la barra, puestos 3 a 4 para 2. Confirmadlo o rechazadlo con los botones."


def test_pending_confirm_request_reads_the_framework_state() -> None:
    call = Content.from_function_call(call_id="c1", name="seating_confirm_seating", arguments={})
    request = Content.from_function_approval_request(id="r1", function_call=call)
    state = {"tool_approval": {"pending_approval_requests": [request.to_dict()]}}
    assert pending_confirm_request(state).id == "r1"
    other = Content.from_function_approval_request(
        id="r2", function_call=Content.from_function_call(call_id="c2", name="other", arguments={})
    )
    assert pending_confirm_request({"tool_approval": {"pending_approval_requests": [other.to_dict()]}}) is None
    assert pending_confirm_request({}) is None


def test_helpers_parse_tool_output_and_errors() -> None:
    assert result_object([Content.from_text(json.dumps(HELD)), Content.from_text("{}")]) == HELD
    assert result_object("not json") is None
    assert error_code(RuntimeError("Error executing tool confirm_seating: expired: gone")) == "expired"
    assert error_code("nothing") is None
    assert place_text("bar", "Barra", [5]) == "la barra, puesto 5"
    assert place_text("table", "Mesa 2", []) == "la Mesa 2"
