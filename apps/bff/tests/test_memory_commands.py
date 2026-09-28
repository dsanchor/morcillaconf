from restaurant_contracts.application import Action, ErrorCode

from bff.service import DUPLICATE_MEMORY


async def setup(make_service, commands, name="Ana"):
    service = make_service()
    session = service.authenticate(service.open_session(name).token)
    arrival = await service.submit(session, commands.arrive())
    await service.submit(
        session,
        commands.say(
            arrival.conversation_id,
            "Prefiero agua con gas y soy alérgica a los frutos secos",
        ),
    )
    await service.drain()
    return service, session, arrival.conversation_id


def memories(service, session, conversation_id):
    snapshot = service.get_snapshot(session, conversation_id)
    return {memory.value: memory for memory in snapshot.memory.memories}


async def test_memories_get_short_typeable_ids(make_service, commands) -> None:
    service, session, conversation_id = await setup(make_service, commands)

    visible = memories(service, session, conversation_id)

    assert set(visible) == {"agua con gas", "frutos secos"}
    assert {memory.memory_id for memory in visible.values()} == {"m1", "m2"}
    assert visible["frutos secos"].kind.value == "restriction"
    assert visible["agua con gas"].source == conversation_id
    snapshot = service.get_snapshot(session, conversation_id)
    assert {Action.CORRECT_MEMORY, Action.DELETE_MEMORY} <= set(snapshot.allowed_actions)


async def test_read_correct_delete_and_clear(make_service, commands) -> None:
    service, session, conversation_id = await setup(make_service, commands)
    water = memories(service, session, conversation_id)["agua con gas"].memory_id
    nuts = memories(service, session, conversation_id)["frutos secos"].memory_id

    read = await service.submit(session, commands.read_memory(conversation_id))
    corrected = await service.submit(
        session, commands.correct_memory(conversation_id, water, "agua sin gas")
    )
    after_correction = memories(service, session, conversation_id)
    deleted = await service.submit(session, commands.delete_memory(conversation_id, nuts))
    after_deletion = memories(service, session, conversation_id)
    cleared = await service.submit(session, commands.clear_memory(conversation_id))

    assert read.status == corrected.status == deleted.status == cleared.status == "completed"
    assert after_correction["agua sin gas"].memory_id == water
    assert set(after_deletion) == {"agua sin gas"}
    assert memories(service, session, conversation_id) == {}
    snapshot = service.get_snapshot(session, conversation_id)
    assert Action.CLEAR_MEMORY in snapshot.allowed_actions
    assert Action.DELETE_MEMORY not in snapshot.allowed_actions

    await service.submit(session, commands.say(conversation_id, "Prefiero la terraza"))
    await service.drain()
    assert memories(service, session, conversation_id)["terraza"].memory_id == "m3"


async def test_unknown_or_duplicate_memories_fail_clearly(make_service, commands) -> None:
    service, session, conversation_id = await setup(make_service, commands)
    await service.submit(session, commands.say(conversation_id, "Prefiero la terraza"))
    await service.drain()
    terrace = memories(service, session, conversation_id)["terraza"].memory_id

    unknown = await service.submit(session, commands.delete_memory(conversation_id, "m7"))
    duplicate = await service.submit(
        session, commands.correct_memory(conversation_id, terrace, "Agua con gas")
    )

    assert unknown.status == "failed"
    assert unknown.error.code == ErrorCode.NOT_FOUND
    assert unknown.error.message == "No recuerdo nada con el identificador m7."
    assert duplicate.status == "failed"
    assert duplicate.error.code == ErrorCode.CONFLICT
    assert duplicate.error.message == DUPLICATE_MEMORY


async def test_memories_belong_to_their_identity(make_service, commands) -> None:
    service, ana, conversation_id = await setup(make_service, commands)
    luis = service.authenticate(service.open_session("Luis").token)
    visit = await service.submit(luis, commands.arrive())

    attempt = await service.submit(luis, commands.delete_memory(visit.conversation_id, "m1"))

    assert attempt.status == "failed" and attempt.error.code == ErrorCode.NOT_FOUND
    assert memories(service, luis, visit.conversation_id) == {}
    assert len(memories(service, ana, conversation_id)) == 2


async def test_memories_are_shared_by_every_visit_of_the_customer(make_service, commands) -> None:
    service, session, conversation_id = await setup(make_service, commands)
    again = service.authenticate(service.open_session("ANA").token)
    visit = await service.submit(again, commands.arrive())

    assert visit.conversation_id != conversation_id
    assert memories(service, again, visit.conversation_id) == memories(
        service, session, conversation_id
    )
