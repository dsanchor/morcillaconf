import pytest

from bff.seating import (
    McpSeatingGateway,
    NoSeatingAvailable,
    SeatingConflict,
    SeatingExpired,
    SeatingGatewayError,
    SeatingNotFound,
    SeatingUnavailable,
    error_from_text,
)
from seating_stub import free_port


async def test_gateway_holds_confirms_and_reads_the_room(seating_server) -> None:
    gateway = McpSeatingGateway(seating_server.url, timeout_seconds=5)
    held = await gateway.hold(visit_id="visit_1", party_size=3, preference="any", idempotency_key="k1")
    assert (held.resource_id, held.resource_label, held.status, held.version) == ("table-03", "Mesa 3", "held", 1)
    assert held.expires_at is not None and held.expires_at.tzinfo is not None
    confirmed = await gateway.confirm(assignment_id=held.assignment_id, visit_id="visit_1", expected_version=1, idempotency_key="c1")
    assert (confirmed.status, confirmed.version) == ("occupied", 2)
    room = await gateway.room("visit_1")
    assert room.visit is not None and room.visit.assignment_id == held.assignment_id
    assert room.resource("table-03").mine and room.resource("table-03").state == "occupied"
    assert not room.resource("table-01").mine


async def test_gateway_cancels_a_hold(seating_server) -> None:
    gateway = McpSeatingGateway(seating_server.url)
    held = await gateway.hold(visit_id="visit_1", party_size=2, preference="any", idempotency_key="k1")
    cancelled = await gateway.cancel(assignment_id=held.assignment_id, visit_id="visit_1", expected_version=1, idempotency_key="x")
    assert cancelled.status == "cancelled"


async def test_gateway_maps_error_codes(seating_server) -> None:
    gateway = McpSeatingGateway(seating_server.url)
    with pytest.raises(NoSeatingAvailable):
        await gateway.hold(visit_id="visit_1", party_size=9, preference="any", idempotency_key="k1")
    with pytest.raises(SeatingNotFound):
        await gateway.confirm(assignment_id="seat_x", visit_id="visit_1", expected_version=1, idempotency_key="c")
    held = await gateway.hold(visit_id="visit_1", party_size=2, preference="any", idempotency_key="k2")
    seating_server.stub.assignments[held.assignment_id]["status"] = "expired"
    with pytest.raises(SeatingExpired):
        await gateway.confirm(assignment_id=held.assignment_id, visit_id="visit_1", expected_version=1, idempotency_key="c2")


def test_error_texts_map_to_typed_errors() -> None:
    assert isinstance(error_from_text("Error executing tool x: expired: gone"), SeatingExpired)
    assert isinstance(error_from_text("Error executing tool x: conflict: nope"), SeatingConflict)
    assert type(error_from_text("validation failed")) is SeatingGatewayError


async def test_an_unreachable_service_is_reported_quickly() -> None:
    gateway = McpSeatingGateway(f"http://127.0.0.1:{free_port()}/mcp", timeout_seconds=2)
    with pytest.raises(SeatingUnavailable):
        await gateway.room()


async def test_a_slow_service_times_out(seating_server) -> None:
    seating_server.stub.delay_seconds = 3
    gateway = McpSeatingGateway(seating_server.url, timeout_seconds=1)
    with pytest.raises(SeatingUnavailable):
        await gateway.hold(visit_id="visit_1", party_size=2, preference="any", idempotency_key="slow")


async def test_the_gateway_survives_a_service_restart(seating_server) -> None:
    gateway = McpSeatingGateway(seating_server.url)
    await gateway.room()
    seating_server.stop()
    with pytest.raises(SeatingUnavailable):
        await gateway.room()
    seating_server.start()
    assert (await gateway.room()).layout_id == "stub"
