import json
from types import SimpleNamespace

import pytest

from restaurant_agent.seating import (
    SeatingToolContextMiddleware,
    VisitContextProvider,
    bind_visit,
    clear_proposal,
    next_hold_key,
    pending_proposal,
    seating_instructions,
    set_seating_context,
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


class Context:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    def extend_instructions(self, source_id: str, instructions: str) -> None:
        self.instructions.append(instructions)


def test_bind_visit_seeds_the_application_visit_and_drops_foreign_holds() -> None:
    state = {
        "visit_context": {"visit_id": "visit_random", "hold_sequence": 3, "hold_requests": {"2:any": "k"}},
        "seating_proposal": dict(HELD),
    }
    bind_visit(state, "visit_bff")
    assert state["visit_context"] == {"visit_id": "visit_bff", "hold_sequence": 0, "hold_requests": {}}
    assert "seating_proposal" not in state
    state["visit_context"]["hold_sequence"] = 2
    bind_visit(state, "visit_bff")
    assert state["visit_context"]["hold_sequence"] == 2
    with pytest.raises(ValueError):
        bind_visit(state, "  ")


def test_hold_keys_rotate_unless_the_pending_request_repeats() -> None:
    state: dict = {}
    bind_visit(state, "visit_bff")
    _, first, fingerprint = next_hold_key(state, 3, "any")
    assert first.startswith("seating:visit_bff:1-")
    assert store_proposal(state, HELD, idempotency_key=first, fingerprint=fingerprint)
    assert next_hold_key(state, 3, "any")[1] == first
    assert next_hold_key(state, 4, "any")[1].startswith("seating:visit_bff:2-")
    clear_proposal(state)
    assert next_hold_key(state, 3, "any")[1].startswith("seating:visit_bff:3-")


def test_a_key_is_never_reused_after_a_lost_turn() -> None:
    state: dict = {}
    bind_visit(state, "visit_bff")
    saved = {"visit_context": dict(state["visit_context"])}
    first = next_hold_key(state, 2, "any")[1]
    # The failed turn's session is not saved: the next turn starts from `saved`.
    again = next_hold_key(saved, 2, "any")[1]
    assert first != again


def test_only_held_results_become_proposals() -> None:
    state: dict = {}
    assert not store_proposal(state, {**HELD, "status": "expired"}, idempotency_key="k", fingerprint="3:any")
    assert not store_proposal(state, {"resource_id": "x"}, idempotency_key="k", fingerprint="3:any")
    assert pending_proposal(state) is None
    assert store_proposal(state, HELD, idempotency_key="k", fingerprint="3:any")
    assert pending_proposal(state)["resource_label"] == "Mesa 3"


def test_seating_instructions_follow_the_application_context() -> None:
    assert seating_instructions({}) is None
    state: dict = {}
    store_proposal(state, HELD, idempotency_key="k", fingerprint="3:any")
    derived = seating_instructions(state)
    assert "Mesa 3" in derived and "Confirmar" in derived
    set_seating_context(state, {"status": "seated", "place": "Mesa 3", "party_size": 3})
    assert "ya está sentado" in seating_instructions(state)
    set_seating_context(state, {"status": "none"})
    assert "seating_hold_seating" in seating_instructions(state)
    set_seating_context(state, None)
    assert "Confirmar" in seating_instructions(state)


async def test_provider_shares_the_seating_state_every_turn() -> None:
    session = SimpleNamespace(state={})
    context = Context()
    await VisitContextProvider().before_run(agent=object(), session=session, context=context, state={})
    assert session.state["visit_context"]["visit_id"].startswith("visit_")
    assert context.instructions == []
    set_seating_context(session.state, {"status": "proposed", "place": "Barra", "seats": [1, 2]})
    await VisitContextProvider().before_run(agent=object(), session=session, context=context, state={})
    assert len(context.instructions) == 1 and "Barra" in context.instructions[0]


async def test_middleware_stores_seats_and_the_key_it_used() -> None:
    session = SimpleNamespace(state={})
    bind_visit(session.state, "visit_bff")
    bar = {**HELD, "resource_id": "bar", "resource_kind": "bar", "seat_ids": ["bar-seat-01", "bar-seat-02"], "party_size": 2}
    context = SimpleNamespace(
        function=SimpleNamespace(name="seating_hold_seating"),
        session=session,
        arguments={"party_size": 2, "preference": "bar", "visit_id": "forged", "idempotency_key": "forged"},
        result=None,
    )

    async def call_next() -> None:
        assert context.arguments["visit_id"] == "visit_bff"
        context.result = [SimpleNamespace(text=json.dumps(bar))]

    await SeatingToolContextMiddleware().process(context, call_next)
    proposal = pending_proposal(session.state)
    assert proposal["seat_ids"] == ["bar-seat-01", "bar-seat-02"]
    assert proposal["idempotency_key"].startswith("seating:visit_bff:1-")
    assert proposal["fingerprint"] == "2:bar"


async def test_middleware_ignores_other_tools() -> None:
    context = SimpleNamespace(function=SimpleNamespace(name="seating_get_seating_availability"), session=None)
    called = []

    async def call_next() -> None:
        called.append(True)

    await SeatingToolContextMiddleware().process(context, call_next)
    assert called == [True]
