import time

import pytest
from fastapi.testclient import TestClient

from bff.main import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def enter(client, name="Ana") -> dict:
    response = client.post("/v1/sessions", json={"name": name})
    assert response.status_code == 201
    return response.json()


def auth(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['token']}"}


def command(event_id: str, event_type: str, **fields) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "occurred_at": "2026-09-28T12:00:00Z",
        "event_type": event_type,
        **fields,
    }


def arrive(client, session, event_id="cmd_arrive", resume=None) -> dict:
    response = client.post(
        "/v1/commands",
        json=command(event_id, "customer.arrived", payload={"resume_visit_id": resume}),
        headers=auth(session),
    )
    assert response.status_code == 200
    return response.json()


def wait_for(client, session, event_id) -> dict:
    for _ in range(100):
        result = client.get(f"/v1/commands/{event_id}", headers=auth(session)).json()
        if result["status"] != "pending":
            return result
        time.sleep(0.02)
    raise AssertionError("The command did not finish")


def assert_public_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "correlation_id", "recovery"}
    assert body["code"] == code
    return body


def test_health_reports_the_waiter_mode(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok", "waiter": "scripted"}


def test_session_binds_the_normalized_name(client) -> None:
    session = enter(client, "  María   José ")
    assert session["identity"] == {"actor_id": "maria jose", "authenticated": True}
    assert session["presented_name"] == "María José"
    assert session["active_visit_id"] is None
    assert session["token_type"] == "bearer"
    assert session["waiter"] == "scripted"


@pytest.mark.parametrize("body", [{"name": "   "}, {"name": "a" * 41}, {}, {"name": "Ana", "x": 1}])
def test_invalid_names_are_rejected(client, body) -> None:
    assert_public_error(client.post("/v1/sessions", json=body), 422, "invalid_command")


def test_calls_without_a_valid_session_are_unauthenticated(client) -> None:
    response = client.get("/v1/commands/cmd_x")
    assert_public_error(response, 401, "unauthenticated")
    assert response.headers["www-authenticate"] == "Bearer"
    response = client.get("/v1/commands/cmd_x", headers={"Authorization": "Bearer nope"})
    assert_public_error(response, 401, "unauthenticated")


def test_full_message_round_trip(client) -> None:
    session = enter(client)
    arrival = arrive(client, session)
    assert arrival["status"] == "completed"
    assert enter(client, "ana")["active_visit_id"] == arrival["visit_id"]

    response = client.post(
        "/v1/commands",
        json=command(
            "cmd_msg",
            "conversation.message_sent",
            conversation_id=arrival["conversation_id"],
            payload={"message": "Venimos dos"},
        ),
        headers=auth(session),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    result = wait_for(client, session, "cmd_msg")
    assert result["status"] == "completed"

    snapshot = client.get(
        f"/v1/conversations/{arrival['conversation_id']}/snapshot", headers=auth(session)
    ).json()
    assert snapshot["customer"]["party_size"] == 2
    assert [m["role"] for m in snapshot["messages"]] == ["assistant", "user", "assistant"]


def test_invalid_commands_never_echo_their_content(client) -> None:
    session = enter(client)
    for body in (
        command("cmd_1", "conversation.message_sent", conversation_id="c", payload={"message": "  "}),
        command("cmd_2", "customer.arrived", payload={}, actor_id="intruso"),
        command("cmd_3", "memory.consent_granted", conversation_id="c", payload={}),
    ):
        response = client.post("/v1/commands", json=body, headers=auth(session))
        error = assert_public_error(response, 422, "invalid_command")
        assert "intruso" not in error["message"]
    response = client.post("/v1/commands", content=b"{not json", headers=auth(session))
    assert_public_error(response, 422, "invalid_command")


def test_idempotency_conflict_and_ownership(client) -> None:
    ana = enter(client)
    arrival = arrive(client, ana)
    assert arrive(client, ana) == arrival
    response = client.post(
        "/v1/commands",
        json=command("cmd_arrive", "customer.arrived", payload={"resume_visit_id": "visit_x"}),
        headers=auth(ana),
    )
    assert_public_error(response, 409, "idempotency_conflict")

    luis = enter(client, "Luis")
    path = f"/v1/conversations/{arrival['conversation_id']}"
    assert_public_error(client.get(f"{path}/snapshot", headers=auth(luis)), 403, "forbidden")
    assert_public_error(client.get(f"{path}/events", headers=auth(luis)), 403, "forbidden")
    assert_public_error(
        client.get("/v1/commands/cmd_arrive", headers=auth(luis)), 404, "not_found"
    )
    assert_public_error(
        client.get("/v1/conversations/conv_x/snapshot", headers=auth(luis)), 404, "not_found"
    )
    failed = arrive(client, luis, event_id="cmd_steal", resume=arrival["visit_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "forbidden"


def test_stream_cursor_errors(client) -> None:
    session = enter(client)
    arrival = arrive(client, session)
    path = f"/v1/conversations/{arrival['conversation_id']}/events"
    assert_public_error(
        client.get(f"{path}?after_cursor=-1", headers=auth(session)), 422, "invalid_command"
    )
    assert_public_error(
        client.get(f"{path}?after_cursor=99", headers=auth(session)), 409, "conflict"
    )
    for bad in ("abc", " 1a"):
        assert_public_error(
            client.get(path, headers={**auth(session), "Last-Event-ID": bad}),
            422,
            "invalid_command",
        )
    assert_public_error(
        client.get(f"{path}?after_cursor=%C2%B2", headers=auth(session)),
        422,
        "invalid_command",
    )


def test_unknown_routes_answer_with_a_public_error(client) -> None:
    assert_public_error(client.get("/v1/nada"), 404, "not_found")


def test_unexpected_errors_are_public_and_not_re_raised(settings, caplog) -> None:
    with TestClient(create_app(settings), raise_server_exceptions=True) as client:
        session = enter(client)

        def explode(*args, **kwargs):
            raise RuntimeError("secreto de Ana")

        client.app.state.service.get_result = explode
        response = client.get("/v1/commands/cmd_x", headers=auth(session))

    error = assert_public_error(response, 500, "internal_error")
    assert "secreto" not in error["message"]
    assert "secreto" not in caplog.text
