import pytest
from pydantic import ValidationError

from restaurant_agent.contracts import (
    CustomerSnapshot,
    MemoryIntent,
    PendingField,
    WaiterModelResult,
)


def test_pending_fields_must_match_missing_customer_data() -> None:
    with pytest.raises(ValidationError):
        WaiterModelResult(
            reply="¿Cuántas personas sois?",
            customer=CustomerSnapshot(presented_name="Majo", party_size=None),
            pending_fields=[],
            memory_intent=MemoryIntent.NONE,
        )


def test_complete_customer_has_no_pending_fields() -> None:
    result = WaiterModelResult(
        reply="Gracias, Majo.",
        customer=CustomerSnapshot(presented_name="Majo", party_size=2),
        pending_fields=[],
        memory_intent=MemoryIntent.NONE,
    )

    assert result.pending_fields == []


def test_missing_customer_fields_are_explicit() -> None:
    result = WaiterModelResult(
        reply="¿A nombre de quién?",
        customer=CustomerSnapshot(party_size=None),
        pending_fields=[
            PendingField.CUSTOMER_NAME,
            PendingField.PARTY_SIZE,
        ],
        memory_intent=MemoryIntent.NONE,
    )

    assert set(result.pending_fields) == {
        PendingField.CUSTOMER_NAME,
        PendingField.PARTY_SIZE,
    }


def test_party_size_defaults_to_one() -> None:
    result = WaiterModelResult(
        reply="¿En qué puedo ayudarte?",
        customer=CustomerSnapshot(presented_name="Majo"),
        pending_fields=[],
        memory_intent=MemoryIntent.NONE,
    )

    assert result.customer.party_size == 1
