import inspect
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter

from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    Action,
    ActorContext,
    ErrorCode,
    RestaurantSnapshot,
)
from restaurant_contracts.client import BffClient, BffClientError
from restaurant_contracts.memory import MemoryKind

from frontend.fake_client import FakeBffClient, FakeRestaurant, extract_memories

AT = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)
SNAPSHOT_ADAPTER = TypeAdapter(RestaurantSnapshot)


def _command(event_type: str, event_id: str, conversation_id: str | None = None, **payload):
    data = {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": AT.isoformat(),
        "payload": payload,
    }
    if conversation_id is not None:
        data["conversation_id"] = conversation_id
    return COMMAND_ADAPTER.validate_python(data)


def _client(name: str = "Ana", restaurant: FakeRestaurant | None = None, **options) -> FakeBffClient:
    restaurant = restaurant or FakeRestaurant(**options)
    return restaurant.client(ActorContext(actor_id=name, authenticated=True))


async def _arrive(client: FakeBffClient, event_id: str = "cmd_arrive"):
    result = await client.submit(_command("customer.arrived", event_id, resume_visit_id=None))
    assert result.status == "completed"
    return result


async def _events(client: FakeBffClient, conversation_id: str, after_cursor: int = 0) -> list:
    return [event async for event in client.events(conversation_id, after_cursor=after_cursor)]


def _roundtrip(snapshot: RestaurantSnapshot) -> RestaurantSnapshot:
    return SNAPSHOT_ADAPTER.validate_json(SNAPSHOT_ADAPTER.dump_json(snapshot))


def test_fake_client_implements_the_bff_protocol() -> None:
    client = _client()
    for method in ("submit", "get_result", "get_snapshot", "get_room"):
        assert inspect.iscoroutinefunction(getattr(client, method))
        assert inspect.signature(getattr(client, method)).parameters.keys() == (
            inspect.signature(getattr(BffClient, method)).parameters.keys() - {"self"}
        )
    assert "after_cursor" in inspect.signature(client.events).parameters
    assert hasattr(client.events("conv_x", after_cursor=0), "__anext__")


async def test_arrival_opens_a_visit_greeted_by_name() -> None:
    client = _client("Ana")
    result = await _arrive(client)
    snapshot = await client.get_snapshot(result.conversation_id)
    assert _roundtrip(snapshot) == snapshot
    assert snapshot.identity == ActorContext(actor_id="Ana", authenticated=True)
    assert snapshot.customer.presented_name == "Ana"
    assert snapshot.pending_fields == []
    assert snapshot.process_status == "idle"
    assert [m.text for m in snapshot.messages] == ["Hombre, Ana, ¿qué tal, maja?"]
    assert snapshot.messages[0].command_event_id == "cmd_arrive"
    assert snapshot.visit_id == result.visit_id
    assert Action.SEND_MESSAGE in snapshot.allowed_actions
    assert Action.CORRECT_MEMORY not in snapshot.allowed_actions
    events = await _events(client, result.conversation_id)
    assert snapshot.cursor == events[-1].cursor
    assert result.cursor == events[-2].cursor


async def test_identity_comes_from_the_adapter_not_from_commands() -> None:
    restaurant = FakeRestaurant()
    ana, luis = _client("Ana", restaurant), _client("Luis", restaurant)
    result = await _arrive(ana)
    with pytest.raises(BffClientError) as denied:
        await luis.get_snapshot(result.conversation_id)
    assert denied.value.error.code == ErrorCode.FORBIDDEN
    with pytest.raises(BffClientError):
        await _events(luis, result.conversation_id)
    with pytest.raises(BffClientError) as unknown:
        await luis.get_result("cmd_arrive")
    assert unknown.value.error.code == ErrorCode.NOT_FOUND
    foreign = await luis.submit(
        _command("conversation.message_sent", "cmd_intruder", result.conversation_id, message="Hola")
    )
    assert foreign.status == "failed" and foreign.error.code == ErrorCode.FORBIDDEN
    assert foreign.error.correlation_id == foreign.correlation_id
    assert len((await ana.get_snapshot(result.conversation_id)).messages) == 1


async def test_message_stream_is_confirmed_before_it_is_final() -> None:
    client = _client()
    arrival = await _arrive(client)
    before = (await client.get_snapshot(arrival.conversation_id)).cursor
    result = await client.submit(
        _command("conversation.message_sent", "cmd_hola", arrival.conversation_id, message="Hola")
    )
    events = await _events(client, arrival.conversation_id, before)
    for event in events:
        assert STREAM_EVENT_ADAPTER.validate_json(STREAM_EVENT_ADAPTER.dump_json(event)) == event
        assert event.command_event_id == "cmd_hola"
        assert event.correlation_id == result.correlation_id
    assert [event.cursor for event in events] == list(range(before + 1, before + 1 + len(events)))
    types = [event.event_type for event in events]
    assert types[0] == "command.status_changed" and events[0].result.status == "pending"
    assert types[1] == "snapshot.updated" and events[1].snapshot.process_status == "processing"
    assert [m.role for m in events[1].snapshot.messages] == ["assistant", "user"]
    deltas = [event for event in events if event.event_type == "conversation.text_delta"]
    final = events[-2].snapshot
    assert final.process_status == "idle"
    assert "".join(delta.delta for delta in deltas) == final.messages[-1].text
    assert {delta.message_id for delta in deltas} == {final.messages[-1].message_id}
    assert events[-1].result == result
    assert result.status == "completed" and result.cursor == events[-2].cursor
    assert COMMAND_RESULT_ADAPTER.validate_python(result.model_dump()) == result


async def test_preferences_and_allergies_become_visible_memories() -> None:
    client = _client()
    arrival = await _arrive(client)
    await client.submit(
        _command(
            "conversation.message_sent", "cmd_pref", arrival.conversation_id,
            message="Prefiero el agua con gas y soy alérgica a los frutos secos",
        )
    )
    snapshot = await client.get_snapshot(arrival.conversation_id)
    assert snapshot.messages[-1].text == (
        "Apuntado: agua con gas. Y tu alergia a frutos secos, te la preguntaré en cada visita."
    )
    memories = snapshot.memory.memories
    assert [(m.memory_id, m.kind, m.value) for m in memories] == [
        ("m1", MemoryKind.PREFERENCE, "agua con gas"),
        ("m2", MemoryKind.RESTRICTION, "frutos secos"),
    ]
    assert all(m.source == arrival.conversation_id and m.requires_reconfirmation for m in memories)
    assert snapshot.customer.preferences == ["agua con gas"]
    assert snapshot.customer.restrictions == ["frutos secos"]
    assert {Action.CORRECT_MEMORY, Action.DELETE_MEMORY} <= set(snapshot.allowed_actions)


@pytest.mark.parametrize(
    ("text", "preferences", "restrictions"),
    [
        ("Soy alérgico al marisco.", [], ["marisco"]),
        ("prefiero pan y aceite", ["pan y aceite"], []),
        ("Tengo alergia a la lactosa, prefiero el vino tinto", ["vino tinto"], ["lactosa"]),
        ("Hola, ¿qué hay hoy?", [], []),
    ],
)
def test_scripted_waiter_only_understands_simple_phrases(text, preferences, restrictions) -> None:
    assert extract_memories(text) == (preferences, restrictions)


async def test_memory_commands_follow_the_contract() -> None:
    client = _client()
    arrival = await _arrive(client)
    conversation = arrival.conversation_id
    await client.submit(
        _command("conversation.message_sent", "cmd_pref", conversation, message="Prefiero el agua con gas")
    )
    read = await client.submit(_command("memory.read_requested", "cmd_read", conversation))
    assert read.status == "completed"
    corrected = await client.submit(
        _command("memory.correction_requested", "cmd_fix", conversation, memory_id="m1", value="Agua sin gas")
    )
    assert corrected.status == "completed"
    snapshot = await client.get_snapshot(conversation)
    assert [m.value for m in snapshot.memory.memories] == ["Agua sin gas"]
    missing = await client.submit(
        _command("memory.deletion_requested", "cmd_missing", conversation, memory_id="m9")
    )
    assert missing.status == "failed" and missing.error.code == ErrorCode.NOT_FOUND
    assert missing.error.recovery == "none"
    deleted = await client.submit(
        _command("memory.deletion_requested", "cmd_delete", conversation, memory_id="m1")
    )
    assert deleted.status == "completed"
    assert (await client.get_snapshot(conversation)).memory.memories == []


async def test_clearing_memories_keeps_the_visit_and_future_learning() -> None:
    client = _client()
    arrival = await _arrive(client)
    conversation = arrival.conversation_id
    await client.submit(_command("conversation.message_sent", "cmd_1", conversation, message="Prefiero vino"))
    await client.submit(_command("memory.clear_requested", "cmd_clear", conversation))
    cleared = await client.get_snapshot(conversation)
    assert cleared.memory.memories == []
    assert cleared.visit_id == arrival.visit_id
    assert len(cleared.messages) == 3
    await client.submit(
        _command("conversation.message_sent", "cmd_2", conversation, message="Soy alérgica al gluten")
    )
    again = await client.get_snapshot(conversation)
    assert [(m.memory_id, m.value) for m in again.memory.memories] == [("m2", "gluten")]


async def test_memories_belong_to_the_identity_across_visits() -> None:
    restaurant = FakeRestaurant()
    ana = _client("Ana", restaurant)
    first = await _arrive(ana, "cmd_first")
    await ana.submit(
        _command("conversation.message_sent", "cmd_pref", first.conversation_id, message="Prefiero la tortilla")
    )
    second = await _arrive(_client("Ana", restaurant), "cmd_second")
    assert second.conversation_id != first.conversation_id
    remembered = await ana.get_snapshot(second.conversation_id)
    assert [m.value for m in remembered.memory.memories] == ["tortilla"]
    assert remembered.customer.preferences == []
    luis = _client("Luis", restaurant)
    other = await _arrive(luis)
    assert (await luis.get_snapshot(other.conversation_id)).memory.memories == []


async def test_repeated_command_is_idempotent_and_conflicts_are_explicit() -> None:
    client = _client()
    arrival = await _arrive(client)
    command = _command("conversation.message_sent", "cmd_once", arrival.conversation_id, message="Hola")
    first = await client.submit(command)
    assert await client.submit(command) == first
    assert await client.get_result("cmd_once") == first
    assert len((await client.get_snapshot(arrival.conversation_id)).messages) == 3
    changed = _command("conversation.message_sent", "cmd_once", arrival.conversation_id, message="Adiós")
    with pytest.raises(BffClientError) as conflict:
        await client.submit(changed)
    assert conflict.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_cursor_rules_future_expired_and_caught_up() -> None:
    client = _client(retained_events=3)
    arrival = await _arrive(client)
    conversation = arrival.conversation_id
    snapshot = await client.get_snapshot(conversation)
    assert await _events(client, conversation, snapshot.cursor) == []
    with pytest.raises(BffClientError) as future:
        await _events(client, conversation, snapshot.cursor + 1)
    assert future.value.error.code == ErrorCode.CONFLICT
    with pytest.raises(BffClientError) as expired:
        await _events(client, conversation, 0)
    assert expired.value.error.code == ErrorCode.CURSOR_EXPIRED
    assert expired.value.error.recovery == "fetch_snapshot"
    assert len(await _events(client, conversation, snapshot.cursor - 3)) == 3


async def test_arrival_can_resume_only_an_own_visit() -> None:
    restaurant = FakeRestaurant()
    ana = _client("Ana", restaurant)
    arrival = await _arrive(ana)
    resumed = await ana.submit(_command("customer.arrived", "cmd_resume", resume_visit_id=arrival.visit_id))
    assert resumed.status == "completed" and resumed.conversation_id == arrival.conversation_id
    stolen = await _client("Luis", restaurant).submit(
        _command("customer.arrived", "cmd_steal", resume_visit_id=arrival.visit_id)
    )
    assert stolen.status == "failed" and stolen.error.code == ErrorCode.FORBIDDEN
    unknown = await ana.submit(_command("customer.arrived", "cmd_ghost", resume_visit_id="visit_404"))
    assert unknown.status == "failed" and unknown.error.code == ErrorCode.NOT_FOUND


async def test_the_latest_visit_is_the_active_one_per_identity() -> None:
    restaurant = FakeRestaurant()
    ana = _client("Ana", restaurant)
    assert ana.active_visit_id is None
    first = await _arrive(ana)
    assert ana.active_visit_id == first.visit_id
    second = await ana.submit(_command("customer.arrived", "cmd_new"))
    assert ana.active_visit_id == second.visit_id
    assert _client("Luis", restaurant).active_visit_id is None
    assert ana.simulated is True


async def test_turn_limit_is_reported_and_removes_the_action() -> None:
    client = _client(max_turns=1)
    arrival = await _arrive(client)
    conversation = arrival.conversation_id
    await client.submit(_command("conversation.message_sent", "cmd_1", conversation, message="Hola"))
    snapshot = await client.get_snapshot(conversation)
    assert Action.SEND_MESSAGE not in snapshot.allowed_actions
    refused = await client.submit(_command("conversation.message_sent", "cmd_2", conversation, message="Otra"))
    assert refused.status == "failed" and refused.error.code == ErrorCode.TURN_LIMIT_EXCEEDED


async def test_guest_has_no_durable_memory() -> None:
    guest = FakeRestaurant().client(ActorContext(actor_id="session_guest", authenticated=False))
    arrival = await _arrive(guest)
    await guest.submit(
        _command("conversation.message_sent", "cmd_pref", arrival.conversation_id, message="Prefiero agua")
    )
    snapshot = await guest.get_snapshot(arrival.conversation_id)
    assert _roundtrip(snapshot) == snapshot
    assert snapshot.memory.memories == []
    assert set(snapshot.allowed_actions) <= {Action.ARRIVE, Action.SEND_MESSAGE}
    refused = await guest.submit(_command("memory.read_requested", "cmd_read", arrival.conversation_id))
    assert refused.status == "failed" and refused.error.code == ErrorCode.FORBIDDEN


async def test_clock_is_used_for_server_dates() -> None:
    moments = iter(AT + timedelta(seconds=step) for step in range(100))
    client = _client(clock=lambda: next(moments))
    arrival = await _arrive(client)
    snapshot = await client.get_snapshot(arrival.conversation_id)
    assert snapshot.messages[0].occurred_at.tzinfo is not None


def test_view_modules_do_not_import_agents_frameworks_or_databases() -> None:
    subprocess.run(
        [
            sys.executable, "-c",
            "import sys\n"
            "import frontend.config, frontend.fake_client, frontend.http_client\n"
            "import frontend.markup\n"
            "import frontend.slash_commands, frontend.stylesheets, frontend.visit\n"
            "forbidden = ('restaurant_agent', 'agent_framework', 'azure', 'mcp', 'a2a',\n"
            "             'fastapi', 'sqlite3', 'streamlit', 'openai')\n"
            "for name in sys.modules:\n"
            "    assert not name.startswith(forbidden), name\n",
        ],
        check=True,
    )
