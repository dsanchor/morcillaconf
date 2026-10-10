from __future__ import annotations

from collections import Counter

from bff.scripted_seating import ScriptedSeating


class RecordingTelemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(**fields: object) -> None:
            self.calls.append((name, fields))

        return record

    def counts(self) -> Counter[str]:
        return Counter(name for name, _ in self.calls)


async def test_business_events_are_not_repeated_by_command_replays(
    make_service, commands, clock
) -> None:
    telemetry = RecordingTelemetry()
    seating = ScriptedSeating(clock)
    service = make_service(seating=seating, telemetry=telemetry)
    session = service.authenticate(service.open_session("Ana").token)

    arrival_command = commands.arrive(event_id="cmd_arrival")
    arrival = await service.submit(session, arrival_command)
    await service.drain()
    assert await service.submit(session, arrival_command) == arrival

    pending = await service.submit(
        session,
        commands.say(arrival.conversation_id, "Venimos tres"),
    )
    await service.drain()
    assert service.get_result(session, pending.event_id).status == "completed"
    proposal = service.get_snapshot(session, arrival.conversation_id).seating.proposal

    decision = commands.decide(
        arrival.conversation_id,
        proposal.proposal_id,
        event_id="cmd_decision",
    )
    decided = await service.submit(session, decision)
    assert await service.submit(session, decision) == decided

    exit_command = commands.exit(arrival.conversation_id, event_id="cmd_exit")
    exited = await service.submit(session, exit_command)
    assert await service.submit(session, exit_command) == exited

    assert telemetry.counts()["visit_started"] == 1
    assert telemetry.counts()["seating_proposed"] == 1
    assert telemetry.counts()["seating_decided"] == 1
    assert telemetry.counts()["seating_released"] == 1
    assert telemetry.counts()["visit_abandoned"] == 1


async def test_business_telemetry_never_receives_customer_identity(
    make_service, commands
) -> None:
    telemetry = RecordingTelemetry()
    service = make_service(telemetry=telemetry)
    session = service.authenticate(service.open_session("Ana").token)

    await service.submit(session, commands.arrive())
    await service.drain()

    fields = [key for _, call in telemetry.calls for key in call]
    assert "actor_id" not in fields
    assert "presented_name" not in fields
    assert "message" not in fields
