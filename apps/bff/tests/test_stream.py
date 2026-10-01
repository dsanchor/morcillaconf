"""SSE against a real uvicorn server: replay, heartbeats, live events and recovery."""

import json
import threading
import time

import httpx
import pytest
import uvicorn

from bff.main import create_app


@pytest.fixture
def serve(settings, waiter_factory):
    servers = []

    def start(**overrides) -> str:
        app = create_app(
            settings.model_copy(update=overrides),
            waiter_factory=waiter_factory,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        servers.append((server, thread))
        port = server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)


def enter_and_arrive(base: str, name: str = "Ana") -> tuple[dict, dict]:
    session = httpx.post(f"{base}/v1/sessions", json={"name": name}).json()
    headers = {"Authorization": f"Bearer {session['token']}"}
    arrival = httpx.post(
        f"{base}/v1/commands",
        headers=headers,
        json={
            "schema_version": 1,
            "event_id": "cmd_arrive",
            "occurred_at": "2026-09-28T12:00:00Z",
            "event_type": "customer.arrived",
            "payload": {},
        },
    ).json()
    return headers, arrival


def read_sse(lines, until) -> list[dict]:
    """Collect SSE frames (heartbeats as {"comment": ...}) until the predicate holds."""

    frames, frame = [], {}
    for line in lines:
        if line.startswith(":"):
            frames.append({"comment": line[1:].strip()})
        elif line == "":
            if frame:
                frame["data"] = json.loads(frame["data"])
                frames.append(frame)
                frame = {}
        else:
            key, _, value = line.partition(": ")
            frame[key] = value
        if frames and until(frames):
            return frames
    raise AssertionError("The stream ended early")


def test_replay_heartbeat_and_live_events(serve) -> None:
    base = serve()
    headers, arrival = enter_and_arrive(base)
    url = f"{base}/v1/conversations/{arrival['conversation_id']}/events"

    with httpx.stream("GET", url, params={"after_cursor": 0}, headers=headers, timeout=5) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        frames = read_sse(lines, lambda f: any("comment" in x for x in f))
        assert [(x.get("id"), x.get("event")) for x in frames if "id" in x] == [
            ("1", "snapshot.updated"),
            ("2", "command.status_changed"),
        ]
        assert frames[-1] == {"comment": "ping"}

        sent = httpx.post(
            f"{base}/v1/commands",
            headers=headers,
            json={
                "schema_version": 1,
                "event_id": "cmd_msg",
                "occurred_at": "2026-09-28T12:00:01Z",
                "event_type": "conversation.message_sent",
                "conversation_id": arrival["conversation_id"],
                "payload": {"message": "Prefiero agua con gas"},
            },
        )
        assert sent.status_code == 202

        def finished(items):
            return any(
                x.get("event") == "command.status_changed"
                and x["data"]["result"]["status"] == "completed"
                and x["data"]["command_event_id"] == "cmd_msg"
                for x in items
            )

        live = [x for x in read_sse(lines, finished) if "id" in x]
    assert [int(x["id"]) for x in live] == [3, 4, 5, 6]
    assert [x["data"]["cursor"] for x in live] == [3, 4, 5, 6]
    assert live[1]["data"]["snapshot"]["process_status"] == "processing"
    assert live[2]["data"]["snapshot"]["memory"]["memories"][0]["memory_id"] == "m1"


def test_last_event_id_resumes_after_that_cursor(serve) -> None:
    base = serve()
    headers, arrival = enter_and_arrive(base)
    url = f"{base}/v1/conversations/{arrival['conversation_id']}/events"

    with httpx.stream(
        "GET", url, params={"after_cursor": 0}, headers={**headers, "Last-Event-ID": "1"}
    ) as response:
        frames = read_sse(response.iter_lines(), lambda f: any("id" in x for x in f))
    assert [x["id"] for x in frames if "id" in x] == ["2"]


def test_expired_cursor_asks_for_a_new_snapshot(serve) -> None:
    base = serve(bff_event_retention=2)
    headers, arrival = enter_and_arrive(base)
    for number in range(2):
        httpx.post(
            f"{base}/v1/commands",
            headers=headers,
            json={
                "schema_version": 1,
                "event_id": f"cmd_read_{number}",
                "occurred_at": "2026-09-28T12:00:01Z",
                "event_type": "memory.read_requested",
                "conversation_id": arrival["conversation_id"],
                "payload": {},
            },
        )

    response = httpx.get(
        f"{base}/v1/conversations/{arrival['conversation_id']}/events",
        params={"after_cursor": 0},
        headers=headers,
    )

    assert response.status_code == 410
    assert response.json()["code"] == "cursor_expired"
    assert response.json()["recovery"] == "fetch_snapshot"


def test_another_identity_cannot_subscribe(serve) -> None:
    base = serve()
    _, arrival = enter_and_arrive(base, "Ana")
    luis, _ = enter_and_arrive(base, "Luis")

    response = httpx.get(
        f"{base}/v1/conversations/{arrival['conversation_id']}/events", headers=luis
    )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
