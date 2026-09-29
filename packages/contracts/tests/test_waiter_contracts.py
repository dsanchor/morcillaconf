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
