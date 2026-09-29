import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from restaurant_contracts.application import Action, ErrorCode

from bff.main import create_app
from bff.scripted import ScriptedWaiterAgent
from bff.service import SEATING_UNAVAILABLE
from conftest import Commands
from seating_fake import FakeSeatingGateway


@pytest.fixture
def seating(clock) -> FakeSeatingGateway:
    return FakeSeatingGateway(clock)


@pytest.fixture
def service(make_service, seating):
    return make_service(seating=seating)


async def enter(service, commands, name="Ana"):
    session = service.authenticate(service.open_session(name).token)
    result = await service.submit(session, commands.arrive())
    return session, result.conversation_id


async def say(service, commands, session, conversation_id, text):
    pending = await service.submit(session, commands.say(conversation_id, text))
    await service.drain()
    return service.get_result(session, pending.event_id)


async def propose(service, commands, name="Ana", text="Venimos tres"):
    session, conversation_id = await enter(service, commands, name)
    result = await say(service, commands, session, conversation_id, text)
    assert result.status == "completed"
    return session, conversation_id, service.get_snapshot(session, conversation_id)


async def test_a_stated_party_gets_a_held_proposal(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)

    proposal = snapshot.seating.proposal
    assert snapshot.seating.status == "proposed"
    assert (proposal.place.label, proposal.place.capacity, proposal.party_size) == ("Mesa 3", 4, 3)
    assert proposal.proposal_id.startswith("prop_") and proposal.version == 1
    assert Action.DECIDE_TABLE in snapshot.allowed_actions
    assert "Mesa 3" in snapshot.messages[-1].text
    held = seating.active(snapshot.visit_id)
    assert held is not None and held.status == "held"
    assert "seat_" not in json.dumps(snapshot.model_dump(mode="json"))


async def test_confirming_seats_the_group_once(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal = snapshot.seating.proposal
    command = commands.decide(conversation_id, proposal.proposal_id)

    result = await service.submit(session, command)
    assert result.status == "completed"
    assert await service.submit(session, command) == result
    again = await service.submit(session, commands.decide(conversation_id, proposal.proposal_id))
    assert again.status == "completed"
    assert seating.calls.count("confirm") == 1

    seated = service.get_snapshot(session, conversation_id)
    assert seated.seating.status == "seated"
    assert (seated.seating.place.label, seated.seating.party_size) == ("Mesa 3", 3)
    assert seated.seating.seated_at is not None
    assert Action.DECIDE_TABLE not in seated.allowed_actions
    assert seated.messages[-1].text == "¡Estupendo! Os acompaño a la Mesa 3."
    assert seating.active(snapshot.visit_id).status == "occupied"

    reject = await service.submit(session, commands.decide(conversation_id, proposal.proposal_id, decision="rejected"))
    assert reject.status == "failed" and reject.error.code == ErrorCode.CONFLICT


async def test_a_concurrent_double_click_confirms_once(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal_id = snapshot.seating.proposal.proposal_id
    first, second = await asyncio.gather(
        service.submit(session, commands.decide(conversation_id, proposal_id)),
        service.submit(session, commands.decide(conversation_id, proposal_id)),
    )
    assert (first.status, second.status) == ("completed", "completed")
    assert seating.calls.count("confirm") == 1
    messages = service.get_snapshot(session, conversation_id).messages
    assert sum("Os acompaño" in message.text for message in messages) == 1


async def test_rejecting_frees_the_place_at_once(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal_id = snapshot.seating.proposal.proposal_id

    result = await service.submit(session, commands.decide(conversation_id, proposal_id, decision="rejected"))

    assert result.status == "completed"
    after = service.get_snapshot(session, conversation_id)
    assert after.seating.status == "none"
    assert after.messages[-1].text.startswith("Sin problema, dejo libre la Mesa 3")
    assert seating.active(snapshot.visit_id) is None
    luis, luis_conversation, luis_snapshot = await propose(service, commands, "Luis", "Somos tres")
    assert luis_snapshot.seating.proposal.place.label == "Mesa 3"


async def test_a_new_request_after_rejecting_holds_again(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    await service.submit(
        session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id, decision="rejected")
    )
    await say(service, commands, session, conversation_id, "Venimos tres")
    again = service.get_snapshot(session, conversation_id)
    assert again.seating.status == "proposed"
    assert again.seating.proposal.proposal_id != snapshot.seating.proposal.proposal_id
    assert seating.active(snapshot.visit_id).status == "held"


@pytest.mark.parametrize("stale", ["version", "proposal"])
async def test_a_stale_or_foreign_decision_has_no_effect(service, commands, seating, stale) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal = snapshot.seating.proposal
    command = (
        commands.decide(conversation_id, proposal.proposal_id, version=2)
        if stale == "version"
        else commands.decide(conversation_id, "prop_other")
    )
    result = await service.submit(session, command)
    assert result.status == "failed" and result.error.message == "Esa propuesta ya no está vigente."
    assert seating.active(snapshot.visit_id).status == "held"


async def test_another_customer_cannot_decide(service, commands) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    luis = service.authenticate(service.open_session("Luis").token)
    result = await service.submit(luis, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    assert result.status == "failed" and result.error.code == ErrorCode.FORBIDDEN


async def test_a_decision_without_proposal_is_refused(service, commands) -> None:
    session, conversation_id = await enter(service, commands)
    result = await service.submit(session, commands.decide(conversation_id, "prop_x"))
    assert result.status == "failed"
    assert result.error.message == "No tengo ninguna propuesta de sitio pendiente para ti."


async def test_a_late_confirmation_reports_the_expiry(service, commands, seating, clock) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    clock.now += timedelta(minutes=6)
    # The poll keeps the card so a late click gets the message.
    room = await service.room(session, conversation_id)
    assert next(place for place in room.places if place.label == "Mesa 3").state == "free"
    assert service.get_snapshot(session, conversation_id).seating.status == "proposed"

    result = await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))

    assert result.status == "failed" and result.error.code == ErrorCode.CONFLICT
    assert result.error.message.startswith("La reserva de la Mesa 3 ha caducado")
    assert service.get_snapshot(session, conversation_id).seating.status == "none"


async def test_an_expiry_seen_only_by_the_mcp_is_reported(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    seating.expire_all()
    result = await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    assert result.status == "failed" and "ha caducado" in result.error.message


async def test_a_new_turn_drops_an_expired_proposal(service, commands, clock) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    clock.now += timedelta(minutes=6)
    await say(service, commands, session, conversation_id, "¿Tenéis vino?")
    assert service.get_snapshot(session, conversation_id).seating.status == "none"


async def test_an_unavailable_service_keeps_the_proposal(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    seating.available = False
    proposal_id = snapshot.seating.proposal.proposal_id
    result = await service.submit(session, commands.decide(conversation_id, proposal_id))
    assert result.status == "failed" and result.error.message == SEATING_UNAVAILABLE
    assert service.get_snapshot(session, conversation_id).seating.status == "proposed"
    seating.available = True
    assert (await service.submit(session, commands.decide(conversation_id, proposal_id))).status == "completed"


async def test_a_turn_fails_clearly_when_seating_is_down(service, commands, seating) -> None:
    session, conversation_id = await enter(service, commands)
    seating.available = False
    result = await say(service, commands, session, conversation_id, "Somos dos")
    assert result.status == "failed" and result.error.message == SEATING_UNAVAILABLE


async def test_parallel_customers_get_different_tables_and_see_each_other(service, commands) -> None:
    ana, ana_conversation, ana_snapshot = await propose(service, commands, "Ana", "Somos dos")
    await service.submit(ana, commands.decide(ana_conversation, ana_snapshot.seating.proposal.proposal_id))
    luis, luis_conversation, luis_snapshot = await propose(service, commands, "Luis", "Somos dos")

    assert ana_snapshot.seating.proposal.place.label == "Mesa 1"
    assert luis_snapshot.seating.proposal.place.label == "Mesa 2"
    room = await service.room(ana, ana_conversation)
    places = {place.label: place for place in room.places}
    assert (places["Mesa 1"].state, places["Mesa 1"].mine, places["Mesa 1"].party_size) == ("occupied", True, 2)
    assert (places["Mesa 2"].state, places["Mesa 2"].mine) == ("held", False)
    dumped = json.dumps(room.model_dump(mode="json"))
    assert "Luis" not in dumped and "luis" not in dumped and "visit_" not in dumped and "seat_" not in dumped


async def test_full_tables_fall_back_to_the_bar_in_order(service, commands, seating) -> None:
    for number, size in enumerate((2, 2, 4, 4, 6)):
        await seating.hold(visit_id=f"other_{number}", party_size=size, preference="table", idempotency_key=f"o{number}")
    ana, conversation_id, snapshot = await propose(service, commands, "Ana", "Somos dos")
    proposal = snapshot.seating.proposal
    assert (proposal.place.kind, proposal.place.seats) == ("bar", [1, 2])
    assert "barra, puestos 1 a 2" in snapshot.messages[-1].text
    await service.submit(ana, commands.decide(conversation_id, proposal.proposal_id))
    seated = service.get_snapshot(ana, conversation_id)
    assert (seated.seating.place.kind, seated.seating.place.seats) == ("bar", [1, 2])

    luis, luis_conversation, luis_snapshot = await propose(
        service, commands, "Luis", "Nos sentamos en la barra, somos 3"
    )
    assert luis_snapshot.seating.proposal.place.seats == [3, 4, 5]
    room = await service.room(luis, luis_conversation)
    bar = next(place for place in room.places if place.kind == "bar")
    assert [seat.state for seat in bar.seats][:5] == ["occupied", "occupied", "held", "held", "held"]
    assert [seat.mine for seat in bar.seats][:5] == [False, False, True, True, True]


async def test_a_group_too_large_gets_an_honest_answer(service, commands) -> None:
    session, conversation_id, snapshot = await propose(service, commands, "Ana", "Somos nueve")
    assert snapshot.seating.status == "none"
    assert "no hay sitio para 9" in snapshot.messages[-1].text


async def test_a_new_party_size_replaces_the_pending_proposal(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands, "Ana", "Somos dos")
    await say(service, commands, session, conversation_id, "Perdona, somos cinco")
    after = service.get_snapshot(session, conversation_id)
    assert after.seating.proposal.place.label == "Mesa 5"
    held = [a for a in seating.assignments.values() if a.visit_id == snapshot.visit_id and a.status == "held"]
    assert len(held) == 1


async def test_saying_yes_does_not_confirm(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    await say(service, commands, session, conversation_id, "Sí, vale")
    after = service.get_snapshot(session, conversation_id)
    assert after.seating.status == "proposed"
    assert "Confirmar" in after.messages[-1].text
    assert "confirm" not in seating.calls


async def test_new_visit_cancels_a_pending_proposal(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    result = await service.submit(session, commands.arrive())
    assert result.status == "completed" and result.conversation_id != conversation_id
    assert seating.active(snapshot.visit_id) is None
    assert service.get_snapshot(session, conversation_id).seating.status == "none"


async def test_new_visit_is_refused_while_seated_until_the_room_is_reset(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))

    refused = await service.submit(session, commands.arrive())
    assert refused.status == "failed" and refused.error.code == ErrorCode.CONFLICT
    assert refused.error.message.startswith("Ya estáis sentados en la Mesa 3")

    seating.reset()
    room = await service.room(session, conversation_id)
    assert not any(place.mine for place in room.places)
    assert service.get_snapshot(session, conversation_id).seating.status == "none"
    assert (await service.submit(session, commands.arrive())).status == "completed"


async def test_the_room_reconciles_a_confirmation_lost_by_a_crash(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    held = seating.active(snapshot.visit_id)
    await seating.confirm(assignment_id=held.assignment_id, visit_id=held.visit_id, expected_version=1, idempotency_key="x")
    await service.room(session, conversation_id)
    assert service.get_snapshot(session, conversation_id).seating.status == "seated"


async def test_a_restart_keeps_the_pending_card(make_service, commands, seating) -> None:
    first = make_service(seating=seating)
    session, conversation_id, snapshot = await propose(first, commands)
    second = make_service(seating=seating)
    restored = second.get_snapshot(session, conversation_id)
    assert restored.seating == snapshot.seating
    result = await second.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    assert result.status == "completed"


async def test_a_phase3_session_is_rebound_to_the_bff_visit(service, commands, seating) -> None:
    from agent_framework import AgentSession

    session, conversation_id = await enter(service, commands)
    legacy = AgentSession(session_id=conversation_id)
    legacy.state["visit_context"] = {"visit_id": "visit_random", "hold_sequence": 4, "hold_requests": {"2:any": "k"}}
    with service._db.write() as tx:
        row = tx.get_conversation(conversation_id)
        row.agent_session_json = json.dumps(legacy.to_dict())
        tx.update_conversation(row)
    await say(service, commands, session, conversation_id, "Somos dos")
    snapshot = service.get_snapshot(session, conversation_id)
    assert seating.active(snapshot.visit_id) is not None
    assert seating.active("visit_random") is None


async def test_without_seating_nothing_changes(make_service, commands) -> None:
    service = make_service()
    session, conversation_id = await enter(service, commands)
    await say(service, commands, session, conversation_id, "Venimos tres")
    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.seating.status == "none"
    assert Action.DECIDE_TABLE not in snapshot.allowed_actions
    room = await service.room(session, conversation_id)
    assert room.seating_enabled is False and room.places == []


def test_the_room_endpoint_is_owned_and_anonymous(settings, clock) -> None:
    fake = FakeSeatingGateway(clock)
    config = settings.model_copy(update={"bff_room_cache_seconds": 0})
    app = create_app(config, seating_factory=lambda _: fake, clock=clock)
    with TestClient(app) as client:
        ana = client.post("/v1/sessions", json={"name": "Ana"}).json()
        luis = client.post("/v1/sessions", json={"name": "Luis"}).json()
        headers = {"Authorization": f"Bearer {ana['token']}"}
        arrived = client.post("/v1/commands", json=Commands().arrive().model_dump(mode="json"), headers=headers).json()
        path = f"/v1/conversations/{arrived['conversation_id']}/room"
        room = client.get(path, headers=headers)
        assert room.status_code == 200
        body = room.json()
        assert body["seating_enabled"] is True and len(body["places"]) == 6
        assert client.get(path, headers={"Authorization": f"Bearer {luis['token']}"}).status_code == 403
        assert client.get(path).status_code == 401
        assert client.get("/healthz").json()["seating"] == "on"
        fake.available = False
        down = client.get(path, headers=headers)
        assert down.status_code == 503 and down.json()["message"] == SEATING_UNAVAILABLE


async def test_an_unexpected_decision_error_leaves_the_conversation_usable(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)

    async def broken(**_):
        raise RuntimeError("bug")

    seating.confirm = broken
    result = await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    assert result.status == "failed" and result.error.message == SEATING_UNAVAILABLE
    after = service.get_snapshot(session, conversation_id)
    assert after.process_status == "idle" and after.seating.status == "proposed"


async def test_a_bar_proposal_survives_a_failed_room_lookup(service, commands, seating) -> None:
    from restaurant_agent.seating_gateway import SeatingUnavailable

    await seating.hold(visit_id="other", party_size=2, preference="bar", idempotency_key="o")

    async def down(visit_id: str = ""):
        raise SeatingUnavailable("down")

    seating.room = down
    session, conversation_id, snapshot = await propose(service, commands, "Ana", "En la barra, somos dos")
    place = snapshot.seating.proposal.place
    assert (place.kind, place.seats) == ("bar", [3, 4]) and place.capacity >= 4


def _capture_turns(service):
    turns = []
    original = service._waiter.take_turn

    async def take_turn(turn):
        turns.append(turn)
        return await original(turn)

    service._waiter.take_turn = take_turn
    return turns


def _history(service, conversation_id):
    with service._db.read() as tx:
        raw = tx.get_conversation(conversation_id).agent_session_json
    state = json.loads(raw)["state"]["in_memory"]
    return [
        "".join(content.get("text", "") for content in message.get("contents", []))
        for message in state.get("messages", [])
    ]


@pytest.mark.parametrize(
    ("decision", "outcome", "note", "status"),
    [
        ("rejected", "rejected", "Sin problema, dejo libre la Mesa 3. ¿Preferís otro sitio?", "none"),
        ("confirmed", "confirmed", "¡Estupendo! Os acompaño a la Mesa 3.", "seated"),
    ],
)
async def test_a_decision_reaches_the_next_turn(service, commands, decision, outcome, note, status) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    turns = _capture_turns(service)
    await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id, decision=decision))

    await say(service, commands, session, conversation_id, "¿Qué tal?")
    context = turns[-1].seating_context
    assert context["status"] == status
    assert context["last_outcome"] == {"decision": outcome, "place": "Mesa 3"}
    assert turns[-1].history_notes == (note,)
    history = _history(service, conversation_id)
    assert note in history

    await say(service, commands, session, conversation_id, "Gracias")
    assert turns[-1].history_notes == ()
    assert turns[-1].seating_context["last_outcome"]["decision"] == outcome
    assert _history(service, conversation_id).count(note) == 1


async def test_a_late_confirmation_reaches_the_next_turn_as_expired(service, commands, clock) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    turns = _capture_turns(service)
    clock.now += timedelta(minutes=6)
    result = await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    await say(service, commands, session, conversation_id, "Vaya")
    assert turns[-1].seating_context == {
        "status": "none",
        "last_outcome": {"decision": "expired", "place": "Mesa 3"},
    }
    assert turns[-1].history_notes == (result.error.message,)


async def test_a_reset_reaches_the_next_turn_as_cancelled(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    turns = _capture_turns(service)
    seating.reset()
    await say(service, commands, session, conversation_id, "¿Seguimos?")
    assert turns[-1].seating_context["status"] == "none"
    assert turns[-1].seating_context["last_outcome"] == {"decision": "cancelled", "place": "Mesa 3"}


async def test_a_new_proposal_replaces_the_last_outcome(service, commands) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id, decision="rejected"))
    turns = _capture_turns(service)
    await say(service, commands, session, conversation_id, "Somos dos")
    await say(service, commands, session, conversation_id, "Vale")
    context = turns[-1].seating_context
    assert context["status"] == "proposed" and context["place"] == "Mesa 1"
    assert "last_outcome" not in context


async def test_notes_wait_for_a_turn_that_saves_the_history(make_service, commands, seating) -> None:
    class FakeServiceError(Exception):
        pass

    FakeServiceError.__module__ = "agent_framework.exceptions"
    failing = {"on": False}

    def failure(message):
        return FakeServiceError("down") if failing["on"] else None

    service = make_service(ScriptedWaiterAgent(seating=seating, failure=failure), seating=seating)
    session, conversation_id, snapshot = await propose(service, commands)
    await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id, decision="rejected"))
    turns = _capture_turns(service)
    failing["on"] = True
    assert (await say(service, commands, session, conversation_id, "Hola")).status == "failed"
    failing["on"] = False
    await say(service, commands, session, conversation_id, "Hola")
    assert turns[-1].history_notes == turns[0].history_notes != ()
