"""BFF seating: it persists and presents what the waiter reports, never the MCP.

The scripted waiter's in-memory seating plays the waiter's side with the same
report as the real agent; the waiter-side HITL itself is tested with the
agent.
"""

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from restaurant_contracts.application import Action, ErrorCode

from bff.main import create_app
from bff.scripted_seating import ScriptedSeating
from bff.service import SEATING_UNAVAILABLE
from conftest import Commands


class CountingSeating(ScriptedSeating):
    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.decisions: list[bool] = []

    def decide(self, visit_id, token, approved):
        self.decisions.append(approved)
        return super().decide(visit_id, token, approved)


@pytest.fixture
def seating(clock) -> CountingSeating:
    return CountingSeating(clock)


@pytest.fixture
def service(make_service, seating):
    return make_service(seating=seating)


async def enter(service, commands, name="Ana"):
    session = service.authenticate(service.open_session(name).token)
    result = await service.submit(session, commands.arrive())
    await service.drain()
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


async def test_the_waiters_proposal_becomes_the_card(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)

    proposal = snapshot.seating.proposal
    assert snapshot.seating.status == "proposed"
    assert (proposal.place.label, proposal.place.capacity, proposal.party_size) == ("Mesa 3", 4, 3)
    assert proposal.proposal_id.startswith("prop_") and proposal.version == 1
    assert Action.DECIDE_TABLE in snapshot.allowed_actions
    assert "Mesa 3" in snapshot.messages[-1].text
    assert seating.awaiting(snapshot.visit_id)


async def test_solo_customer_selects_and_occupies_available_seating_without_a_card(
    service, commands, seating
) -> None:
    session, conversation_id = await enter(service, commands)
    arrived = service.get_snapshot(session, conversation_id)
    assert arrived.customer.party_size == 1
    assert "sola o acompañada" in arrived.messages[-1].text

    await say(service, commands, session, conversation_id, "He venido sola")
    options = service.get_snapshot(session, conversation_id)
    assert options.customer.party_size == 1
    assert "mesa y barra disponibles" in options.messages[-1].text
    assert options.seating.status == "none"

    await say(service, commands, session, conversation_id, "Prefiero mesa")
    seated = service.get_snapshot(session, conversation_id)
    assert seated.seating.status == "seated"
    assert seated.seating.party_size == 1
    assert seated.seating.place.kind == "table"
    assert Action.DECIDE_TABLE not in seated.allowed_actions
    assert seating.decisions == [True]
    assert "te acompaño" in seated.messages[-1].text
    assert "Qué quieres tomar" in seated.messages[-1].text


async def test_default_party_size_does_not_skip_the_initial_question(
    service, commands, seating
) -> None:
    session, conversation_id = await enter(service, commands)

    await say(service, commands, session, conversation_id, "Quiero mesa")
    snapshot = service.get_snapshot(session, conversation_id)

    assert snapshot.customer.party_size == 1
    assert snapshot.seating.status == "none"
    assert not seating.holds(snapshot.visit_id)
    assert "sola o acompañada" in snapshot.messages[-1].text


async def test_accompanied_customer_must_provide_the_total_before_searching(
    service, commands, seating
) -> None:
    session, conversation_id = await enter(service, commands)

    await say(service, commands, session, conversation_id, "No")
    waiting = service.get_snapshot(session, conversation_id)
    assert waiting.customer.party_size == 1
    assert waiting.seating.status == "none"
    assert "Cuántos sois en total" in waiting.messages[-1].text

    await say(service, commands, session, conversation_id, "Somos tres")
    proposed = service.get_snapshot(session, conversation_id)
    assert proposed.customer.party_size == 3
    assert proposed.seating.status == "proposed"


async def test_solo_customer_is_only_offered_currently_available_kinds(
    service, commands, seating
) -> None:
    for number, size in enumerate((2, 2, 4, 4, 6)):
        held = seating.hold(f"other_{number}", size, "table")
        seating.decide(f"other_{number}", held.token, True)
    session, conversation_id = await enter(service, commands)

    await say(service, commands, session, conversation_id, "Estoy solo")
    snapshot = service.get_snapshot(session, conversation_id)

    assert snapshot.customer.party_size == 1
    assert "Ahora mismo puedo ofrecerte la barra. ¿Quieres sentarte allí?" in (
        snapshot.messages[-1].text
    )


async def test_an_arrival_reads_the_room_through_the_waiter(service, commands, seating) -> None:
    session, conversation_id = await enter(service, commands)
    room = service.room(session, conversation_id)
    assert room.seating_enabled is True and len(room.places) == 6
    assert service.seating_enabled is True


async def test_confirming_goes_to_the_waiter_once(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal = snapshot.seating.proposal
    command = commands.decide(conversation_id, proposal.proposal_id)

    result = await service.submit(session, command)
    assert result.status == "completed"
    assert await service.submit(session, command) == result
    again = await service.submit(session, commands.decide(conversation_id, proposal.proposal_id))
    assert again.status == "completed"
    assert seating.decisions == [True]

    seated = service.get_snapshot(session, conversation_id)
    assert seated.seating.status == "seated"
    assert (seated.seating.place.label, seated.seating.party_size) == ("Mesa 3", 3)
    assert seated.seating.seated_at is not None
    assert Action.DECIDE_TABLE not in seated.allowed_actions
    assert seated.messages[-1].text == (
        "¡Estupendo! Os acompaño a la Mesa 3. ¿Qué queréis tomar?"
    )

    reject = await service.submit(
        session, commands.decide(conversation_id, proposal.proposal_id, decision="rejected")
    )
    assert reject.status == "failed" and reject.error.code == ErrorCode.CONFLICT


async def test_a_concurrent_double_click_confirms_once(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal_id = snapshot.seating.proposal.proposal_id
    first, second = await asyncio.gather(
        service.submit(session, commands.decide(conversation_id, proposal_id)),
        service.submit(session, commands.decide(conversation_id, proposal_id)),
    )
    assert (first.status, second.status) == ("completed", "completed")
    assert seating.decisions == [True]
    messages = service.get_snapshot(session, conversation_id).messages
    assert sum("Os acompaño" in message.text for message in messages) == 1


async def test_rejecting_frees_the_place_at_once(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal_id = snapshot.seating.proposal.proposal_id

    result = await service.submit(
        session, commands.decide(conversation_id, proposal_id, decision="rejected")
    )

    assert result.status == "completed"
    after = service.get_snapshot(session, conversation_id)
    assert after.seating.status == "none"
    assert after.messages[-1].text.startswith("Sin problema, dejo libre la Mesa 3")
    assert seating.decisions == [False]
    luis, luis_conversation, luis_snapshot = await propose(service, commands, "Luis", "Somos tres")
    assert luis_snapshot.seating.proposal.place.label == "Mesa 3"


@pytest.mark.parametrize("stale", ["version", "proposal"])
async def test_a_stale_or_foreign_decision_never_reaches_the_waiter(service, commands, seating, stale) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    proposal = snapshot.seating.proposal
    command = (
        commands.decide(conversation_id, proposal.proposal_id, version=2)
        if stale == "version"
        else commands.decide(conversation_id, "prop_other")
    )
    result = await service.submit(session, command)
    assert result.status == "failed" and result.error.message == "Esa propuesta ya no está vigente."
    assert seating.decisions == []


async def test_another_customer_cannot_decide(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    luis = service.authenticate(service.open_session("Luis").token)
    result = await service.submit(
        luis, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id)
    )
    assert result.status == "failed" and result.error.code == ErrorCode.FORBIDDEN
    assert seating.decisions == []


async def test_a_decision_without_proposal_is_refused(service, commands) -> None:
    session, conversation_id = await enter(service, commands)
    result = await service.submit(session, commands.decide(conversation_id, "prop_x"))
    assert result.status == "failed"
    assert result.error.message == "No tengo ninguna propuesta de sitio pendiente para ti."


async def test_a_late_confirmation_shows_the_waiters_expiry_notice(service, commands, clock) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    clock.now += timedelta(minutes=6)
    room = service.room(session, conversation_id)
    assert next(place for place in room.places if place.label == "Mesa 3").state == "free"
    assert service.get_snapshot(session, conversation_id).seating.status == "proposed"

    result = await service.submit(
        session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id)
    )

    assert result.status == "failed" and result.error.code == ErrorCode.CONFLICT
    assert result.error.message.startswith("La reserva de la Mesa 3 ha caducado")
    assert service.get_snapshot(session, conversation_id).seating.status == "none"


async def test_an_unavailable_waiter_keeps_the_card(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    seating.available = False
    proposal_id = snapshot.seating.proposal.proposal_id
    result = await service.submit(session, commands.decide(conversation_id, proposal_id))
    assert result.status == "failed" and result.error.message == SEATING_UNAVAILABLE
    after = service.get_snapshot(session, conversation_id)
    assert after.seating.status == "proposed" and after.process_status == "idle"
    seating.available = True
    assert (await service.submit(session, commands.decide(conversation_id, proposal_id))).status == "completed"


async def test_a_turn_fails_clearly_when_seating_is_down(service, commands, seating) -> None:
    session, conversation_id = await enter(service, commands)
    seating.available = False
    result = await say(service, commands, session, conversation_id, "Somos dos")
    assert result.status == "failed" and result.error.message == SEATING_UNAVAILABLE


async def test_writing_while_pending_keeps_the_card_and_a_typed_yes_never_confirms(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    first = snapshot.seating.proposal.proposal_id

    await say(service, commands, session, conversation_id, "Sí, confírmala")

    after = service.get_snapshot(session, conversation_id)
    assert seating.decisions == []
    assert after.seating.status == "proposed"
    assert after.seating.proposal.proposal_id == first
    assert after.messages[-1].text.startswith("Para confirmar hay que pulsar «Confirmar»")
    assert (await service.submit(session, commands.decide(conversation_id, first))).status == "completed"


async def test_a_new_party_size_while_pending_replaces_the_card(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands, "Ana", "Somos dos")
    await say(service, commands, session, conversation_id, "Perdona, somos cinco")
    after = service.get_snapshot(session, conversation_id)
    assert after.seating.proposal.place.label == "Mesa 5"
    assert after.seating.proposal.proposal_id != snapshot.seating.proposal.proposal_id
    assert len(seating.holds(snapshot.visit_id)) == 1


async def test_parallel_customers_see_each_other_after_any_waiter_call(service, commands) -> None:
    ana, ana_conversation, ana_snapshot = await propose(service, commands, "Ana", "Somos dos")
    await service.submit(
        ana, commands.decide(ana_conversation, ana_snapshot.seating.proposal.proposal_id)
    )
    luis, luis_conversation, luis_snapshot = await propose(service, commands, "Luis", "Somos dos")

    assert ana_snapshot.seating.proposal.place.label == "Mesa 1"
    assert luis_snapshot.seating.proposal.place.label == "Mesa 2"
    room = service.room(ana, ana_conversation)
    places = {place.label: place for place in room.places}
    assert (places["Mesa 1"].state, places["Mesa 1"].mine, places["Mesa 1"].party_size) == ("occupied", True, 2)
    assert (places["Mesa 2"].state, places["Mesa 2"].mine) == ("held", False)
    luis_room = {place.label: place for place in service.room(luis, luis_conversation).places}
    assert (luis_room["Mesa 1"].mine, luis_room["Mesa 2"].mine) == (False, True)
    dumped = json.dumps(room.model_dump(mode="json"))
    assert "Luis" not in dumped and "visit_" not in dumped and "prop_" not in dumped


async def test_full_tables_fall_back_to_the_bar_in_order(service, commands, seating) -> None:
    for number, size in enumerate((2, 2, 4, 4, 6)):
        held = seating.hold(f"other_{number}", size, "table")
        seating.decide(f"other_{number}", held.token, True)
    ana, conversation_id, snapshot = await propose(service, commands, "Ana", "Somos dos")
    proposal = snapshot.seating.proposal
    assert (proposal.place.kind, proposal.place.seats) == ("bar", [1, 2])
    assert "barra, puestos 1 a 2" in snapshot.messages[-1].text
    await service.submit(ana, commands.decide(conversation_id, proposal.proposal_id))

    luis, luis_conversation, luis_snapshot = await propose(
        service, commands, "Luis", "Nos sentamos en la barra, somos 3"
    )
    assert luis_snapshot.seating.proposal.place.seats == [3, 4, 5]
    bar = next(place for place in service.room(luis, luis_conversation).places if place.kind == "bar")
    assert [seat.state for seat in bar.seats][:5] == ["occupied", "occupied", "held", "held", "held"]
    assert [seat.mine for seat in bar.seats][:5] == [False, False, True, True, True]


async def test_new_visit_rejects_a_pending_proposal_through_the_waiter(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    result = await service.submit(session, commands.arrive())
    await service.drain()
    assert result.status == "completed" and result.conversation_id != conversation_id
    assert seating.decisions == [False]
    assert not seating.awaiting(snapshot.visit_id)
    assert service.get_snapshot(session, conversation_id).seating.status == "none"


async def test_new_visit_is_refused_if_the_waiter_cannot_reject(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    seating.available = False
    refused = await service.submit(session, commands.arrive())
    assert refused.status == "failed"
    assert refused.error.message == "Antes de empezar otra visita, confirma o rechaza la propuesta de la Mesa 3."


async def test_new_visit_is_refused_while_seated_until_the_waiter_sees_a_reset(service, commands, seating) -> None:
    session, conversation_id, snapshot = await propose(service, commands)
    await service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))

    refused = await service.submit(session, commands.arrive())
    assert refused.status == "failed" and refused.error.code == ErrorCode.CONFLICT
    assert refused.error.message.startswith("Ya estáis sentados en la Mesa 3")

    seating.reset()
    assert (await service.submit(session, commands.arrive())).status == "completed"
    assert service.get_snapshot(session, conversation_id).seating.status == "none"


async def test_a_restart_keeps_the_pending_card(make_service, commands, seating) -> None:
    first = make_service(seating=seating)
    session, conversation_id, snapshot = await propose(first, commands)
    second = make_service(seating=seating)
    restored = second.get_snapshot(session, conversation_id)
    assert restored.seating == snapshot.seating
    result = await second.submit(
        session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id)
    )
    assert result.status == "completed"


async def test_without_seating_the_room_is_decorative(make_service, commands) -> None:
    service = make_service()
    session, conversation_id = await enter(service, commands)
    await say(service, commands, session, conversation_id, "Venimos tres")
    snapshot = service.get_snapshot(session, conversation_id)
    assert snapshot.seating.status == "none"
    assert Action.DECIDE_TABLE not in snapshot.allowed_actions
    room = service.room(session, conversation_id)
    assert room.seating_enabled is False and room.places == []


def test_the_room_endpoint_is_owned_and_anonymous(settings, clock) -> None:
    config = settings.model_copy(update={"bff_scripted_seating": True})
    app = create_app(config, clock=clock)
    with TestClient(app) as client:
        ana = client.post("/v1/sessions", json={"name": "Ana"}).json()
        luis = client.post("/v1/sessions", json={"name": "Luis"}).json()
        headers = {"Authorization": f"Bearer {ana['token']}"}
        arrived = client.post(
            "/v1/commands", json=Commands().arrive().model_dump(mode="json"), headers=headers
        ).json()
        path = f"/v1/conversations/{arrived['conversation_id']}/room"
        for _ in range(50):
            body = client.get(path, headers=headers).json()
            if body["seating_enabled"]:
                break
        assert body["seating_enabled"] is True and len(body["places"]) == 6
        assert client.get(path, headers={"Authorization": f"Bearer {luis['token']}"}).status_code == 403
        assert client.get(path).status_code == 401
        assert client.get("/healthz").json()["seating"] == "on"


async def test_new_visit_waits_for_a_decision_in_flight(make_service, commands, clock) -> None:
    seating = CountingSeating(clock)
    service = make_service(seating=seating)
    session, conversation_id, snapshot = await propose(service, commands)

    original = service._waiter.decide_seating
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(call, **kwargs):
        entered.set()
        await release.wait()
        return await original(call, **kwargs)

    service._waiter.decide_seating = slow
    confirm = asyncio.create_task(
        service.submit(session, commands.decide(conversation_id, snapshot.seating.proposal.proposal_id))
    )
    await entered.wait()
    new_visit = asyncio.create_task(service.submit(session, commands.arrive()))
    await asyncio.sleep(0.05)
    release.set()
    confirmed, refused = await asyncio.gather(confirm, new_visit)

    assert confirmed.status == "completed"
    assert refused.status == "failed"
    assert service.get_snapshot(session, conversation_id).seating.status == "seated"
