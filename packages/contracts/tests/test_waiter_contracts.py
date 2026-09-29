import pytest
from pydantic import ValidationError

from restaurant_contracts.application import ActorContext
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.waiter import WaiterTurnRequest, WaiterTurnSuccess


def test_remote_waiter_contract_round_trips() -> None:
    request = WaiterTurnRequest(
        conversation_id="conv_1",
        actor=ActorContext(actor_id="ana", authenticated=True),
        presented_name="Ana",
        message="Hola",
        customer=CustomerSnapshot(presented_name="Ana"),
        order_draft=OrderDraft(),
        turn_count=0,
        correlation_id="corr_1",
    )

    assert WaiterTurnRequest.model_validate_json(
        request.model_dump_json()
    ) == request
    assert WaiterTurnSuccess(
        reply="Hola",
        customer=request.customer,
        order_draft=request.order_draft,
        turn_count=1,
    ).status == "completed"


def test_remote_waiter_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WaiterTurnRequest.model_validate(
            {
                "operation": "take_turn",
                "conversation_id": "conv_1",
                "actor": {"actor_id": "ana", "authenticated": True},
                "presented_name": "Ana",
                "message": "Hola",
                "customer": {},
                "order_draft": {},
                "turn_count": 0,
                "correlation_id": "corr_1",
                "unexpected": True,
            }
        )


from restaurant_contracts.waiter import (  # noqa: E402
    WAITER_REQUEST_ADAPTER,
    WAITER_SEATING_RESPONSE_ADAPTER,
    SeatingReport,
    WaiterSeatingDecisionRequest,
    WaiterSeatingSuccess,
    WaiterSeatingSyncRequest,
    WaiterTurnFailure,
)

REPORT = {
    "status": "proposed",
    "awaiting_decision": True,
    "token": "abc123",
    "place": {"place_id": "bar", "kind": "bar", "label": "Barra", "capacity": 8, "seats": [3, 4]},
    "party_size": 2,
    "version": 1,
    "expires_at": "2026-09-29T20:05:00+00:00",
    "last_outcome": {"decision": "rejected", "place": "Mesa 3"},
    "room": [
        {
            "place_id": "table-03", "kind": "table", "label": "Mesa 3", "capacity": 4,
            "display_order": 30, "state": "held", "party_size": 3,
            "expires_at": "2026-09-29T20:05:00+00:00", "seats": [],
        },
        {
            "place_id": "bar", "kind": "bar", "label": "Barra", "capacity": 8, "display_order": 100,
            "state": "free", "seats": [{"position": 1, "state": "occupied"}],
        },
    ],
}


def test_requests_are_discriminated_by_operation() -> None:
    base = {
        "conversation_id": "conv_1",
        "actor": {"actor_id": "ana", "authenticated": True},
        "presented_name": "Ana",
        "correlation_id": "corr_1",
        "visit_id": "visit_1",
    }
    decision = WAITER_REQUEST_ADAPTER.validate_python(
        {**base, "operation": "decide_seating", "decision": "confirmed", "proposal_token": "abc"}
    )
    assert isinstance(decision, WaiterSeatingDecisionRequest)
    sync = WAITER_REQUEST_ADAPTER.validate_python({**base, "operation": "sync_seating"})
    assert isinstance(sync, WaiterSeatingSyncRequest)
    for invalid in (
        {**base, "operation": "decide_seating", "decision": "maybe", "proposal_token": "abc"},
        {**base, "operation": "decide_seating", "decision": "confirmed"},
        {**base, "operation": "sync_seating", "assignment_id": "seat_1"},
        {**base, "operation": "release_seating"},
    ):
        with pytest.raises(ValidationError):
            WAITER_REQUEST_ADAPTER.validate_python(invalid)


def test_the_retired_reconciliation_fields_are_rejected() -> None:
    for field, value in (
        ("seating_context", {"status": "none"}),
        ("pending_assignment_id", "seat_1"),
        ("history_notes", ["Hola"]),
    ):
        with pytest.raises(ValidationError):
            WaiterTurnRequest.model_validate(
                {
                    "conversation_id": "conv_1",
                    "actor": {"actor_id": "ana", "authenticated": True},
                    "presented_name": "Ana",
                    "message": "Hola",
                    "customer": {},
                    "order_draft": {},
                    "turn_count": 0,
                    "correlation_id": "corr_1",
                    field: value,
                }
            )


def test_seating_results_carry_an_anonymous_report() -> None:
    success = WAITER_SEATING_RESPONSE_ADAPTER.validate_python(
        {"operation": "decide_seating", "reply": "¡Estupendo!", "outcome": "confirmed", "seating": REPORT}
    )
    assert isinstance(success, WaiterSeatingSuccess)
    assert success.seating.place.seats == [3, 4]
    failure = WAITER_SEATING_RESPONSE_ADAPTER.validate_python(
        {"status": "failed", "code": "no_pending_decision", "message": "No hay propuesta"}
    )
    assert isinstance(failure, WaiterTurnFailure)
    for leak in ("assignment_id", "visit_id", "mine"):
        with pytest.raises(ValidationError):
            SeatingReport.model_validate({**REPORT, leak: "x"})
    with pytest.raises(ValidationError):
        SeatingReport.model_validate(
            {**REPORT, "room": [{**REPORT["room"][0], "visit_id": "visit_other"}]}
        )
