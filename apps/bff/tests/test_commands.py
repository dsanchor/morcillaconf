import asyncio
import json
from datetime import timedelta

import pytest

from restaurant_contracts.application import Action, ErrorCode

from bff.scripted import ScriptedWaiterAgent
from bff.service import (
    BUSY,
    INTERRUPTED,
    TURN_LIMIT,
    WAITER_UNAVAILABLE,
    PublicFailure,
)
from conftest import GatedAgent


class FakeServiceError(Exception):
    pass


FakeServiceError.__module__ = "agent_framework.exceptions"


async def enter(service, commands, name="Ana"):
    session = service.authenticate(service.open_session(name).token)
    result = await service.submit(session, commands.arrive())
    return session, result


async def say(service, commands, session, conversation_id, text):
    pending = await service.submit(session, commands.say(conversation_id, text))
    await service.drain()
    return pending, service.get_result(session, pending.event_id)


def events(service, conversation_id, after=0):
    with service._db.read() as tx:
        return [json.loads(raw) for _, raw in tx.events_after(conversation_id, after)]


async def test_arrival_opens_a_visit_with_an_instant_greeting(make_service, commands) -> None:
    service = make_service()
    session, result = await enter(service, commands, "  Ana  ")

    assert result.status == "completed"
    snapshot = service.get_snapshot(session, result.conversation_id)
    assert snapshot.visit_id == result.visit_id
    assert snapshot.identity.actor_id == "ana"
    assert [(m.role, m.text) for m in snapshot.messages] == [
        ("assistant", "Hombre, Ana, ¿qué tal, maja?")
    ]
    assert snapshot.customer.presented_name == "Ana"
    assert snapshot.customer.party_size == 1
    assert snapshot.pending_fields == []
    assert snapshot.process_status == "idle"
    assert Action.SEND_MESSAGE in snapshot.allowed_actions
    assert snapshot.cursor == result.cursor + 1  # the status event follows
    assert [e["event_type"] for e in events(service, result.conversation_id)] == [
        "snapshot.updated",
        "command.status_changed",
    ]


async def test_a_message_is_pending_then_answered_by_the_waiter(make_service, commands) -> None:
    agent = ScriptedWaiterAgent()
    service = make_service(agent)
    session, arrival = await enter(service, commands)

    pending, result = await say(
        service, commands, session, arrival.conversation_id, "Venimos tres y quiero una tortilla"
    )

    assert pending.status == "pending"
    assert result.status == "completed"
    assert result.correlation_id == pending.correlation_id
    snapshot = service.get_snapshot(session, arrival.conversation_id)
    assert [m.role for m in snapshot.messages] == ["assistant", "user", "assistant"]
    assert snapshot.messages[1].command_event_id == pending.event_id
    assert snapshot.customer.party_size == 3
    assert [item.name for item in snapshot.order_draft.items] == ["tortilla"]
    assert snapshot.process_status == "idle"
    log = events(service, arrival.conversation_id, after=arrival.cursor + 1)
    assert [(e["event_type"], e.get("snapshot", {}).get("process_status")) for e in log] == [
        ("command.status_changed", None),
        ("snapshot.updated", "processing"),
        ("snapshot.updated", "idle"),
        ("command.status_changed", None),
    ]
    assert [e["result"]["status"] for e in log if "result" in e] == ["pending", "completed"]
    assert log[2]["cursor"] == result.cursor
    assert "Hombre" not in agent.prompts[0]


async def test_the_door_name_cannot_be_changed_from_the_chat(make_service, commands) -> None:
    service = make_service()
    session, arrival = await enter(service, commands)
    await say(service, commands, session, arrival.conversation_id, "Soy Pepe")

    snapshot = service.get_snapshot(session, arrival.conversation_id)
    assert snapshot.customer.presented_name == "Ana"
    assert snapshot.identity.actor_id == "ana"


async def test_repeating_a_command_returns_the_stored_result(make_service, commands) -> None:
    service = make_service()
    session = service.authenticate(service.open_session("Ana").token)
    arrival = commands.arrive(event_id="cmd_arrive")

    first = await service.submit(session, arrival)
    second = await service.submit(session, arrival)

    assert first == second
    assert service.open_session("Ana").active_visit_id == first.visit_id
    message = commands.say(first.conversation_id, "Hola", event_id="cmd_hello")
    await service.submit(session, message)
    await service.submit(session, message)
    await service.drain()
    snapshot = service.get_snapshot(session, first.conversation_id)
    assert [m.text for m in snapshot.messages if m.role == "user"] == ["Hola"]


async def test_same_event_id_with_other_content_is_a_conflict(make_service, commands) -> None:
    service = make_service()
    session, arrival = await enter(service, commands)
    await service.submit(session, commands.say(arrival.conversation_id, "Hola", event_id="cmd_1"))

    with pytest.raises(PublicFailure) as error:
        await service.submit(
            session, commands.say(arrival.conversation_id, "Adiós", event_id="cmd_1")
        )
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    await service.drain()


async def test_one_message_at_a_time_per_conversation(make_service, commands) -> None:
    agent = GatedAgent()
    service = make_service(agent)
    session, arrival = await enter(service, commands)
    first = await service.submit(session, commands.say(arrival.conversation_id, "Hola"))

    busy = await service.submit(session, commands.say(arrival.conversation_id, "¿Hola?"))
    snapshot = service.get_snapshot(session, arrival.conversation_id)
    blocked = await service.submit(session, commands.clear_memory(arrival.conversation_id))
    read = await service.submit(session, commands.read_memory(arrival.conversation_id))

    assert busy.status == "failed" and busy.error.code == ErrorCode.CONFLICT
    assert busy.error.message == BUSY
    assert blocked.status == "failed" and blocked.error.code == ErrorCode.CONFLICT
    assert read.status == "completed"
    assert snapshot.process_status == "processing"
    assert snapshot.allowed_actions == [Action.ARRIVE, Action.READ_MEMORY]
    agent.gate.set()
    await service.drain()
    assert service.get_result(session, first.event_id).status == "completed"


async def test_turn_limit_closes_the_conversation(make_service, commands) -> None:
    service = make_service(max_turns=2)
    session, arrival = await enter(service, commands)
    for text in ("Uno", "Dos"):
        _, result = await say(service, commands, session, arrival.conversation_id, text)
        assert result.status == "completed"

    third = await service.submit(session, commands.say(arrival.conversation_id, "Tres"))

    assert third.status == "failed"
    assert third.error.code == ErrorCode.TURN_LIMIT_EXCEEDED
    assert third.error.message == TURN_LIMIT
    snapshot = service.get_snapshot(session, arrival.conversation_id)
    assert Action.SEND_MESSAGE not in snapshot.allowed_actions
    assert "Tres" not in [m.text for m in snapshot.messages]


async def test_waiter_failures_are_public_and_leak_nothing(make_service, commands) -> None:
    agent = ScriptedWaiterAgent(failure=lambda message: FakeServiceError(f"secreto {message}"))
    service = make_service(agent)
    session, arrival = await enter(service, commands)

    _, result = await say(service, commands, session, arrival.conversation_id, "mi dato íntimo")

    assert result.status == "failed"
    assert result.error.code == ErrorCode.UNAVAILABLE
    assert result.error.message == WAITER_UNAVAILABLE
    assert result.error.recovery == "none"
    log = json.dumps(events(service, arrival.conversation_id))
    assert "secreto" not in log
    snapshot = service.get_snapshot(session, arrival.conversation_id)
    assert snapshot.process_status == "idle"
    assert Action.SEND_MESSAGE in snapshot.allowed_actions


async def test_another_identity_cannot_touch_the_visit(make_service, commands) -> None:
    service = make_service()
    ana, arrival = await enter(service, commands, "Ana")
    pending = await service.submit(ana, commands.say(arrival.conversation_id, "Hola"))
    await service.drain()
    luis = service.authenticate(service.open_session("Luis").token)
    cursor = service.get_snapshot(ana, arrival.conversation_id).cursor

    resumed = await service.submit(luis, commands.arrive(arrival.visit_id))
    sent = await service.submit(luis, commands.say(arrival.conversation_id, "Intruso"))
    cleared = await service.submit(luis, commands.clear_memory(arrival.conversation_id))

    assert resumed.status == sent.status == cleared.status == "failed"
    assert {resumed.error.code, sent.error.code, cleared.error.code} == {ErrorCode.FORBIDDEN}
    for call in (
        lambda: service.get_snapshot(luis, arrival.conversation_id),
        lambda: service.check_stream(luis, arrival.conversation_id, 0),
    ):
        with pytest.raises(PublicFailure) as error:
            call()
        assert error.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(PublicFailure) as error:
        service.get_result(luis, pending.event_id)
    assert error.value.code == ErrorCode.NOT_FOUND
    assert service.get_snapshot(ana, arrival.conversation_id).cursor == cursor
    assert service.open_session("Luis").active_visit_id is None


async def test_the_same_normalized_name_is_the_same_customer(make_service, commands) -> None:
    service = make_service()
    _, arrival = await enter(service, commands, "María José")
    opened = service.open_session("  maria   JOSE ")
    session = service.authenticate(opened.token)

    resumed = await service.submit(session, commands.arrive(opened.active_visit_id))

    assert opened.active_visit_id == arrival.visit_id
    assert resumed.status == "completed"
    assert resumed.conversation_id == arrival.conversation_id
    snapshot = service.get_snapshot(session, arrival.conversation_id)
    assert len(snapshot.messages) == 1
    assert snapshot.customer.presented_name == "María José"


async def test_new_arrival_opens_another_visit_that_becomes_active(make_service, commands) -> None:
    service = make_service()
    session, first = await enter(service, commands)
    second = await service.submit(session, commands.arrive())

    assert second.visit_id != first.visit_id
    assert service.open_session("Ana").active_visit_id == second.visit_id
    old = await service.submit(session, commands.arrive(first.visit_id))
    assert old.conversation_id == first.conversation_id
    unknown = await service.submit(session, commands.arrive("visit_missing"))
    assert unknown.status == "failed" and unknown.error.code == ErrorCode.NOT_FOUND


async def test_a_restarted_bff_keeps_the_visit_and_the_waiter_history(
    make_service, commands
) -> None:
    service = make_service()
    session, arrival = await enter(service, commands)
    token = service.open_session("Ana").token
    await say(service, commands, session, arrival.conversation_id, "Quiero una caña")

    restarted = make_service()
    session = restarted.authenticate(token)
    await say(restarted, commands, session, arrival.conversation_id, "Gracias")

    snapshot = restarted.get_snapshot(session, arrival.conversation_id)
    assert [m.text for m in snapshot.messages if m.role == "user"] == ["Quiero una caña", "Gracias"]
    with restarted._db.read() as tx:
        stored = json.loads(tx.get_conversation(arrival.conversation_id).agent_session_json)
    assert stored["state"]["scripted_history"] == ["Quiero una caña", "Gracias"]


async def test_an_interrupted_turn_is_failed_on_startup(make_service, commands) -> None:
    service = make_service(GatedAgent())
    session, arrival = await enter(service, commands)
    pending = await service.submit(session, commands.say(arrival.conversation_id, "Hola"))
    await service.shutdown()

    restarted = make_service()
    assert restarted.recover_interrupted_turns() == 1

    result = restarted.get_result(session, pending.event_id)
    assert result.status == "failed"
    assert result.error.message == INTERRUPTED
    snapshot = restarted.get_snapshot(session, arrival.conversation_id)
    assert snapshot.process_status == "idle"
    assert restarted.recover_interrupted_turns() == 0


async def test_sessions_are_checked_and_expire(make_service, clock) -> None:
    service = make_service()
    token = service.open_session("Ana").token
    assert service.authenticate(token).actor.actor_id == "ana"
    for bad in (None, "", "otro"):
        with pytest.raises(PublicFailure) as error:
            service.authenticate(bad)
        assert error.value.code == ErrorCode.UNAUTHENTICATED
    clock.now += timedelta(hours=13)
    with pytest.raises(PublicFailure):
        service.authenticate(token)
    with pytest.raises(PublicFailure) as error:
        service.open_session("   ")
    assert error.value.code == ErrorCode.INVALID_COMMAND


async def test_stream_replays_then_follows_new_events(make_service, commands) -> None:
    service = make_service()
    session, arrival = await enter(service, commands)
    service.check_stream(session, arrival.conversation_id, 0)
    stream = service.stream(arrival.conversation_id, 0)

    replayed = [await anext(stream), await anext(stream)]
    assert [event.cursor for event in replayed] == [1, 2]
    follower = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    await service.submit(session, commands.read_memory(arrival.conversation_id))
    event = await asyncio.wait_for(follower, timeout=1)
    assert event.cursor == 3
    await stream.aclose()


async def test_stream_cursor_validation(make_service, commands) -> None:
    service = make_service(retention=2)
    session, arrival = await enter(service, commands)
    for _ in range(2):
        await service.submit(session, commands.read_memory(arrival.conversation_id))

    cases = {-1: ErrorCode.INVALID_COMMAND, 99: ErrorCode.CONFLICT, 0: ErrorCode.CURSOR_EXPIRED}
    for cursor, code in cases.items():
        with pytest.raises(PublicFailure) as error:
            service.check_stream(session, arrival.conversation_id, cursor)
        assert error.value.code == code
    with pytest.raises(PublicFailure) as error:
        service.check_stream(session, arrival.conversation_id, 0)
    assert error.value.to_error().recovery == "fetch_snapshot"
    service.check_stream(session, arrival.conversation_id, 5)
    service.check_stream(session, arrival.conversation_id, 6)


async def test_a_failure_after_the_waiter_still_ends_the_turn(make_service, commands) -> None:
    service = make_service()
    session, arrival = await enter(service, commands)
    original = service._finish_turn
    calls = []

    def flaky(job, outcome, *rest):
        calls.append(outcome)
        if len(calls) == 1:
            raise RuntimeError("disco lleno con datos de Ana")
        original(job, outcome, *rest)

    service._finish_turn = flaky
    _, result = await say(service, commands, session, arrival.conversation_id, "Hola")

    assert result.status == "failed"
    assert result.error.code == ErrorCode.INTERNAL_ERROR
    snapshot = service.get_snapshot(session, arrival.conversation_id)
    assert snapshot.process_status == "idle"
    assert Action.SEND_MESSAGE in snapshot.allowed_actions
