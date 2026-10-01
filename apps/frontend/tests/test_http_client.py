"""HttpBffClient against a local HTTP server that mimics the BFF API.

The server wraps FakeRestaurant behind the same routes, status codes and SSE
framing as apps/bff, using only the standard library.
"""

import asyncio
import json
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    ActorContext,
    ErrorCode,
    PublicError,
)
from restaurant_contracts.client import BffClientError

from frontend.config import FrontendSettings, client_connector
from frontend.fake_client import FakeRestaurant
from frontend.http_client import UNREACHABLE, HttpBffClient
from frontend.visit import VisitSession

STATUS = {
    ErrorCode.INVALID_COMMAND: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.CURSOR_EXPIRED: 410,
}


class FakeBff(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.restaurant = FakeRestaurant()
        self.sessions: dict[str, ActorContext] = {}
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.stopping = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    server: FakeBff

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        self._handle()

    def do_GET(self) -> None:
        self._handle()

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        url = urlparse(self.path)
        try:
            if url.path == "/v1/sessions":
                return self._open_session(json.loads(body)["name"])
            identity = self.server.sessions.get(
                self.headers.get("Authorization", "").removeprefix("Bearer ")
            )
            if identity is None:
                raise self._error(ErrorCode.UNAUTHENTICATED)
            restaurant = self.server.restaurant
            parts = url.path.strip("/").split("/")
            if url.path == "/v1/commands":
                result = restaurant.submit(identity, COMMAND_ADAPTER.validate_json(body))
                status = 202 if result.status == "pending" else 200
                return self._json(status, COMMAND_RESULT_ADAPTER.dump_python(result, mode="json"))
            if parts[:2] == ["v1", "commands"]:
                result = restaurant.get_result(identity, parts[2])
                return self._json(200, COMMAND_RESULT_ADAPTER.dump_python(result, mode="json"))
            if parts[-1] == "snapshot":
                snapshot = restaurant.get_snapshot(identity, parts[2])
                return self._json(200, snapshot.model_dump(mode="json"))
            if parts[-1] == "room":
                room = restaurant.get_room(identity, parts[2])
                return self._json(200, room.model_dump(mode="json"))
            if parts[-1] == "events":
                cursor = int(parse_qs(url.query)["after_cursor"][0])
                return self._stream(identity, parts[2], cursor)
            raise self._error(ErrorCode.NOT_FOUND)
        except BffClientError as exc:
            self._json(STATUS[exc.error.code], exc.error.model_dump(mode="json"))

    def _open_session(self, name: str) -> None:
        if name == "boom":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal Server Error")
            return
        token = uuid.uuid4().hex
        identity = ActorContext(actor_id=name.casefold(), authenticated=True)
        self.server.sessions[token] = identity
        self._json(
            201,
            {
                "token": token,
                "token_type": "bearer",
                "identity": identity.model_dump(mode="json"),
                "presented_name": name,
                "active_visit_id": self.server.restaurant.active_visit_id(identity),
                "waiter": "remote" if name == "Real" else "scripted",
                "expires_at": "2026-09-29T00:00:00+00:00",
            },
        )

    def _stream(self, identity: ActorContext, conversation_id: str, cursor: int) -> None:
        if conversation_id == "conv_broken":
            events = []
        else:
            async def collect():
                stream = self.server.restaurant.stream(identity, conversation_id, cursor)
                return [event async for event in stream]

            events = asyncio.run(collect())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(b": ping\n\n")
            for event in events:
                data = STREAM_EVENT_ADAPTER.dump_json(event).decode()
                self.wfile.write(
                    f"id: {event.cursor}\nevent: {event.event_type}\ndata: {data}\n\n".encode()
                )
            if conversation_id == "conv_broken":
                error = PublicError(
                    code=ErrorCode.CURSOR_EXPIRED,
                    message="El cursor ha caducado.",
                    correlation_id="corr_x",
                    recovery="fetch_snapshot",
                )
                self.wfile.write(f"event: error\ndata: {error.model_dump_json()}\n\n".encode())
            self.wfile.flush()
            while not self.server.stopping:
                time.sleep(0.05)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _error(code: ErrorCode) -> BffClientError:
        return BffClientError(
            PublicError(code=code, message="Error.", correlation_id="corr_x", recovery="none")
        )


@pytest.fixture
def bff():
    server = FakeBff()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.stopping = True
    server.shutdown()
    server.server_close()


def test_the_name_is_sent_once_and_then_only_a_bearer_token(bff) -> None:
    client = HttpBffClient.open(bff.url, "Ana")
    visit = VisitSession(client)
    visit.arrive(client.active_visit_id)

    assert client.identity == ActorContext(actor_id="ana", authenticated=True)
    assert client.active_visit_id is None
    assert client.simulated is True
    method, path, headers, body = bff.requests[0]
    assert (method, path, json.loads(body)) == ("POST", "/v1/sessions", {"name": "Ana"})
    for _, _, headers, body in bff.requests[1:]:
        assert headers["Authorization"].startswith("Bearer ")
        assert b"Ana" not in body
    # The BFF reports "remote" for the real waiter: only "scripted" is simulated.
    assert HttpBffClient.open(bff.url, "Real").simulated is False


def test_a_visit_runs_over_http_and_is_resumed_by_name(bff) -> None:
    client = HttpBffClient.open(bff.url, "Ana")
    visit = VisitSession(client)
    visit.arrive(client.active_visit_id)
    visit.send_message("Prefiero la terraza")

    assert [m.role for m in visit.view().messages] == ["assistant", "user", "assistant"]
    assert visit.pending is None
    assert any("/events?after_cursor=" in path for _, path, _, _ in bff.requests)

    again = HttpBffClient.open(bff.url, "Ana")
    resumed = VisitSession(again)
    resumed.arrive(again.active_visit_id)
    assert again.active_visit_id == visit.snapshot.visit_id
    assert resumed.snapshot.conversation_id == visit.snapshot.conversation_id
    assert len(resumed.snapshot.messages) == 3


async def test_http_errors_carry_the_public_error(bff) -> None:
    ana = HttpBffClient.open(bff.url, "Ana")
    luis = HttpBffClient.open(bff.url, "Luis")
    visit = VisitSession(ana)
    await asyncio.to_thread(visit.arrive)

    with pytest.raises(BffClientError) as error:
        await ana.get_result("cmd_unknown")
    assert error.value.error.code == ErrorCode.NOT_FOUND
    with pytest.raises(BffClientError) as error:
        await luis.get_snapshot(visit.snapshot.conversation_id)
    assert error.value.error.code == ErrorCode.FORBIDDEN


async def test_stream_frames_are_parsed_and_heartbeats_ignored(bff) -> None:
    client = HttpBffClient.open(bff.url, "Ana")
    visit = VisitSession(client)
    await asyncio.to_thread(visit.arrive)
    stream = client.events(visit.snapshot.conversation_id, after_cursor=0)

    first = await anext(stream)
    await stream.aclose()

    assert first.cursor == 1
    assert first.conversation_id == visit.snapshot.conversation_id


async def test_error_frames_raise_the_public_error(bff) -> None:
    client = HttpBffClient.open(bff.url, "Ana")
    stream = client.events("conv_broken", after_cursor=0)

    with pytest.raises(BffClientError) as error:
        await anext(stream)

    assert error.value.error.code == ErrorCode.CURSOR_EXPIRED
    assert error.value.error.recovery == "fetch_snapshot"


def test_an_unreachable_bff_is_unavailable_and_retryable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    connect = client_connector(
        FrontendSettings.from_env(
            {"FRONTEND_BFF_CLIENT": "http", "FRONTEND_BFF_URL": f"http://127.0.0.1:{port}"}
        )
    )

    with pytest.raises(BffClientError) as error:
        connect("Ana")

    assert error.value.error.code == ErrorCode.UNAVAILABLE
    assert error.value.error.recovery == "retry_same_command"
    assert error.value.error.message == UNREACHABLE


def test_server_errors_without_a_public_error_are_unavailable(bff) -> None:
    with pytest.raises(BffClientError) as error:
        HttpBffClient.open(bff.url, "boom")
    assert error.value.error.code == ErrorCode.UNAVAILABLE


def test_the_room_and_the_table_decision_travel_over_http(bff) -> None:
    client = HttpBffClient.open(bff.url, "Ana")
    visit = VisitSession(client)
    visit.arrive()
    visit.send_message("Venimos tres")
    assert visit.snapshot.seating.status == "proposed"
    assert visit.refresh_room() is False
    assert any(place.mine and place.state == "held" for place in visit.room.places)
    visit.decide_table("confirmed")
    assert visit.snapshot.seating.status == "seated"
    method, path, headers, _ = bff.requests[-1]
    assert headers["Authorization"].startswith("Bearer ")
    visit.refresh_room()
    assert any(p.path.endswith("/room") for p in [urlparse(r[1]) for r in bff.requests])
