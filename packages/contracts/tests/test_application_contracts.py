import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    ActorContext,
    PublicError,
    RestaurantSnapshot,
)
from restaurant_contracts.client import BffClientError

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/phase3a"
COMMANDS = json.loads((FIXTURES / "commands.json").read_text())
RESULTS = json.loads((FIXTURES / "results.json").read_text())
SNAPSHOTS = json.loads((FIXTURES / "snapshots.json").read_text())
EVENTS = json.loads((FIXTURES / "events.json").read_text())
EXPIRED = json.loads((FIXTURES / "cursor-expired.json").read_text())


@pytest.mark.parametrize(
    ("adapter", "examples"),
    [
        (COMMAND_ADAPTER, COMMANDS),
        (COMMAND_RESULT_ADAPTER, RESULTS),
        (TypeAdapter(RestaurantSnapshot), SNAPSHOTS),
        (STREAM_EVENT_ADAPTER, EVENTS),
        (TypeAdapter(PublicError), [EXPIRED]),
    ],
)
def test_examples_round_trip_and_have_json_schema(adapter, examples) -> None:
    assert adapter.json_schema()
    for example in examples:
        model = adapter.validate_python(example)
        assert adapter.validate_json(adapter.dump_json(model)) == model


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("field", ["actor", "actor_id", "authenticated"])
def test_commands_reject_client_identity(command, field) -> None:
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({**command, field: "untrusted"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("event_id", "   "),
        ("occurred_at", "2026-09-27T08:00:00"),
        ("event_type", "order.confirmation_decided"),
        ("payload", {"message": "   "}),
        ("payload", {"message": "x" * 2_001}),
        ("payload", {"message": "Hola", "authenticated": True}),
        ("conversation_id", ""),
    ],
)
def test_invalid_message_command_is_rejected(field, value) -> None:
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({**COMMANDS[1], field: value})


def test_message_command_requires_conversation() -> None:
    command = deepcopy(COMMANDS[1])
    del command["conversation_id"]
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python(command)


def test_arrival_can_resume_visit_without_claiming_actor() -> None:
    command = deepcopy(COMMANDS[0])
    command["payload"]["resume_visit_id"] = "visit_demo"
    assert COMMAND_ADAPTER.validate_python(command).payload.resume_visit_id == "visit_demo"


@pytest.mark.parametrize("event_type", ["memory.consent_granted", "memory.consent_revoked"])
def test_retired_consent_commands_are_rejected(event_type) -> None:
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({**COMMANDS[2], "event_type": event_type})


@pytest.mark.parametrize(
    ("index", "payload"),
    [(2, {"unexpected": 1}), (3, {"memory_id": "mem_demo", "value": ""}),
     (4, {"memory_id": " "}), (5, {"actor_id": "someone"})],
)
def test_memory_payloads_are_typed(index, payload) -> None:
    with pytest.raises(ValidationError):
        COMMAND_ADAPTER.validate_python({**COMMANDS[index], "payload": payload})


def test_pending_result_has_no_success_or_error_payload() -> None:
    for extra in ({"error": RESULTS[2]["error"]}, {"visit_id": "visit_demo"}):
        with pytest.raises(ValidationError):
            COMMAND_RESULT_ADAPTER.validate_python({**RESULTS[0], **extra})


def test_completed_requires_resource_and_cursor() -> None:
    for field in ("visit_id", "conversation_id", "cursor"):
        result = deepcopy(RESULTS[1])
        del result[field]
        with pytest.raises(ValidationError):
            COMMAND_RESULT_ADAPTER.validate_python(result)


def test_failed_requires_correlated_public_error() -> None:
    result = deepcopy(RESULTS[2])
    del result["error"]
    with pytest.raises(ValidationError):
        COMMAND_RESULT_ADAPTER.validate_python(result)
    result["error"] = {**RESULTS[2]["error"], "correlation_id": "wrong"}
    with pytest.raises(ValidationError):
        COMMAND_RESULT_ADAPTER.validate_python(result)


@pytest.mark.parametrize("cursor", [-1, "3", 1.5, True])
def test_invalid_snapshot_cursor_is_rejected(cursor) -> None:
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate({**SNAPSHOTS[0], "cursor": cursor})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pending_fields", []),
        ("pending_fields", ["customer_name", "customer_name"]),
        ("allowed_actions", ["conversation.message_sent"] * 2),
        ("allowed_actions", ["payment.confirmation_decided"]),
        ("process_status", "paid"),
        ("table_id", "table_1"),
    ],
)
def test_snapshot_rejects_inconsistent_or_future_fields(field, value) -> None:
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate({**SNAPSHOTS[0], field: value})


def test_guest_snapshot_has_no_durable_memory_or_memory_actions() -> None:
    snapshot = deepcopy(SNAPSHOTS[0])
    snapshot["identity"]["authenticated"] = False
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(snapshot)
    snapshot["allowed_actions"] = ["conversation.message_sent"]
    RestaurantSnapshot.model_validate(snapshot)
    snapshot["memory"] = SNAPSHOTS[2]["memory"]
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(snapshot)


def test_remembered_restrictions_are_not_current_customer_restrictions() -> None:
    snapshot = RestaurantSnapshot.model_validate(SNAPSHOTS[2])
    assert snapshot.memory.memories[0].requires_reconfirmation is True
    assert snapshot.customer.restrictions == []
    assert snapshot.allowed_actions == []
    assert snapshot.customer.party_size is None


@pytest.mark.parametrize("consent", [None, {"status": "granted"}, {"status": "revoked"}])
def test_retired_consent_field_is_rejected(consent) -> None:
    snapshot = deepcopy(SNAPSHOTS[2])
    snapshot["memory"]["consent"] = consent
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(snapshot)


def test_memories_are_visible_without_consent_fields() -> None:
    snapshot = RestaurantSnapshot.model_validate(SNAPSHOTS[2])
    assert len(snapshot.memory.memories) == 1
    assert set(snapshot.memory.model_dump()) == {"memories"}


def test_visible_memories_cannot_be_binding() -> None:
    snapshot = deepcopy(SNAPSHOTS[2])
    snapshot["memory"]["memories"][0]["requires_reconfirmation"] = False
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(snapshot)


def test_projection_ids_must_be_unique() -> None:
    snapshot = deepcopy(SNAPSHOTS[1])
    snapshot["messages"].append(snapshot["messages"][0])
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(snapshot)
    snapshot = deepcopy(SNAPSHOTS[2])
    snapshot["memory"]["memories"] *= 2
    with pytest.raises(ValidationError):
        RestaurantSnapshot.model_validate(snapshot)


@pytest.mark.parametrize("field", ["conversation_id", "cursor"])
def test_event_cannot_embed_another_snapshot(field) -> None:
    event = deepcopy(EVENTS[2])
    event["snapshot"][field] = "conv_other" if field == "conversation_id" else 9
    with pytest.raises(ValidationError):
        STREAM_EVENT_ADAPTER.validate_python(event)


@pytest.mark.parametrize("field", ["event_id", "correlation_id", "conversation_id", "cursor"])
def test_status_event_checks_result_correlation(field) -> None:
    event = deepcopy(EVENTS[3])
    event["result"][field] = 9 if field == "cursor" else "wrong"
    with pytest.raises(ValidationError):
        STREAM_EVENT_ADAPTER.validate_python(event)


def test_partial_text_cannot_carry_confirmed_state() -> None:
    for extra in (
        {"provisional": False}, {"snapshot": SNAPSHOTS[1]}, {"delta": ""},
        {"cursor": 0},
    ):
        with pytest.raises(ValidationError):
            STREAM_EVENT_ADAPTER.validate_python({**EVENTS[1], **extra})


@pytest.mark.parametrize(
    "change",
    [
        {"recovery": "none"},
        {"code": "forbidden"},
        {"code": "idempotency_conflict", "recovery": "retry_same_command"},
    ],
)
def test_public_error_cannot_advise_invalid_recovery(change) -> None:
    with pytest.raises(ValidationError):
        PublicError.model_validate({**EXPIRED, **change})


def test_transport_error_is_explicit_and_preserves_correlation() -> None:
    error = BffClientError(PublicError.model_validate(EXPIRED))
    assert str(error) == EXPIRED["message"]
    assert error.error.correlation_id == "corr_reconnect"
    assert error.error.recovery == "fetch_snapshot"


def test_terminal_failure_cannot_advertise_reexecution() -> None:
    error = PublicError(
        code="unavailable",
        message="Connection lost; consult the command result before retrying.",
        correlation_id="corr_other",
        recovery="retry_same_command",
    )
    with pytest.raises(ValidationError):
        COMMAND_RESULT_ADAPTER.validate_python(
            {**RESULTS[2], "error": error.model_dump()}
        )


def test_actor_context_is_strict() -> None:
    for actor in (
        {"actor_id": " ", "authenticated": True},
        {"actor_id": "actor", "authenticated": "true"},
    ):
        with pytest.raises(ValidationError):
            ActorContext.model_validate(actor)


def test_public_package_does_not_import_agent_or_transport_frameworks() -> None:
    subprocess.run(
        [
            sys.executable, "-I", "-c",
            "import sys\n"
            "import restaurant_contracts.application\n"
            "import restaurant_contracts.client\n"
            "for name in sys.modules:\n"
            "    assert not name.startswith(('restaurant_agent', 'agent_framework', "
            "'fastapi', 'streamlit', 'azure')), name\n",
        ],
        check=True,
    )
