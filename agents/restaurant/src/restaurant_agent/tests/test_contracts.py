import json

import pytest
from pydantic import ValidationError

from restaurant_agent.contracts import (
    CustomerSnapshot,
    MemoryIntent,
    PendingField,
    WaiterModelResult,
)


def model_output(**overrides: object) -> str:
    payload = {
        "reply": "¡Claro! ¿Qué desean pedir?",
        "customer": {
            "presented_name": None,
            "party_size": 1,
            "preferences": [],
            "restrictions": [],
        },
        "order_draft": {"items": []},
        "pending_fields": [],
        "memory_candidates": [],
        "memory_intent": "none",
        "remembered_memories": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_pending_fields_are_derived_from_missing_customer_data() -> None:
    result = WaiterModelResult(
        reply="¿Cuántas personas sois?",
        customer=CustomerSnapshot(presented_name="Majo", party_size=None),
        pending_fields=[],
        memory_intent=MemoryIntent.NONE,
    )

    assert result.pending_fields == [PendingField.PARTY_SIZE]


def test_model_output_without_name_is_accepted_with_name_pending() -> None:
    result = WaiterModelResult.model_validate_json(model_output())

    assert result.customer.presented_name is None
    assert result.pending_fields == [PendingField.CUSTOMER_NAME]


def test_known_customer_fields_are_never_pending() -> None:
    result = WaiterModelResult(
        reply="Gracias, Majo.",
        customer=CustomerSnapshot(presented_name="Majo", party_size=2),
        pending_fields=[PendingField.CUSTOMER_NAME, PendingField.PARTY_SIZE],
        memory_intent=MemoryIntent.NONE,
    )

    assert result.pending_fields == []


def test_other_contract_violations_are_still_rejected() -> None:
    with pytest.raises(ValidationError):
        WaiterModelResult.model_validate_json(model_output(unexpected=True))


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
