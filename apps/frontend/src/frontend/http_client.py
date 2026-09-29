"""BffClient over the BFF's HTTP API and SSE stream, using only the standard library.

Blocking ``urllib`` calls run in worker threads (``asyncio.to_thread``) so the
view keeps using the async ``BffClient`` protocol. The door name travels once,
to open the session; every later call carries only the opaque bearer token.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import ValidationError

from restaurant_contracts.application import (
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    ActorContext,
    Command,
    CommandResult,
    ErrorCode,
    PublicError,
    RestaurantSnapshot,
    StreamEvent,
)
from restaurant_contracts.client import BffClientError
from restaurant_contracts.seating import RoomView

UNREACHABLE = "No consigo hablar con el restaurante ahora mismo. Inténtalo de nuevo."
CONNECTION_LOST = "Se ha cortado la conexión con el restaurante. Inténtalo de nuevo."
UNEXPECTED = "El restaurante ha respondido algo que no entiendo."
# The plan refreshes itself; a slow answer must not hold the view.
ROOM_TIMEOUT_SECONDS = 5.0


def _error(code: ErrorCode, message: str, recovery: str = "none") -> BffClientError:
    return BffClientError(
        PublicError(
            code=code,
            message=message,
            correlation_id=f"corr_client_{uuid.uuid4().hex}",
            recovery=recovery,
        )
    )


def _unavailable(message: str = UNREACHABLE) -> BffClientError:
    return _error(ErrorCode.UNAVAILABLE, message, "retry_same_command")


@dataclass(frozen=True)
class _Session:
    token: str
    identity: ActorContext
    presented_name: str
    active_visit_id: str | None
    simulated: bool


class HttpBffClient:
    """BffClient bound to one BFF demo session."""

    def __init__(self, base_url: str, session: _Session, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._timeout = timeout

    @classmethod
    def open(cls, base_url: str, name: str, *, timeout: float = 30.0) -> HttpBffClient:
        """Open a demo session with the door name (the only time it is sent)."""

        body = _call(
            f"{base_url.rstrip('/')}/v1/sessions",
            method="POST",
            payload={"name": name},
            timeout=timeout,
        )
        try:
            session = _Session(
                token=body["token"],
                identity=ActorContext.model_validate(body["identity"]),
                presented_name=body["presented_name"],
                active_visit_id=body.get("active_visit_id"),
                simulated=body.get("waiter") != "foundry",
            )
        except (KeyError, TypeError, ValidationError):
            raise _error(ErrorCode.INTERNAL_ERROR, UNEXPECTED) from None
        return cls(base_url, session, timeout=timeout)

    @property
    def identity(self) -> ActorContext:
        return self._session.identity

    @property
    def active_visit_id(self) -> str | None:
        return self._session.active_visit_id

    @property
    def simulated(self) -> bool:
        return self._session.simulated

    async def submit(self, command: Command) -> CommandResult:
        body = await asyncio.to_thread(
            self._request, "POST", "/v1/commands", command.model_dump(mode="json")
        )
        return self._parse(COMMAND_RESULT_ADAPTER.validate_python, body)

    async def get_result(self, event_id: str) -> CommandResult:
        body = await asyncio.to_thread(
            self._request, "GET", f"/v1/commands/{quote(event_id, safe='')}"
        )
        return self._parse(COMMAND_RESULT_ADAPTER.validate_python, body)

    async def get_snapshot(self, conversation_id: str) -> RestaurantSnapshot:
        body = await asyncio.to_thread(
            self._request,
            "GET",
            f"/v1/conversations/{quote(conversation_id, safe='')}/snapshot",
        )
        return self._parse(RestaurantSnapshot.model_validate, body)

    async def get_room(self, conversation_id: str) -> RoomView:
        body = await asyncio.to_thread(
            self._request,
            "GET",
            f"/v1/conversations/{quote(conversation_id, safe='')}/room",
            None,
            min(self._timeout, ROOM_TIMEOUT_SECONDS),
        )
        return self._parse(RoomView.model_validate, body)

    def events(
        self, conversation_id: str, *, after_cursor: int
    ) -> AsyncIterator[StreamEvent]:
        return self._events(conversation_id, after_cursor)

    async def _events(
        self, conversation_id: str, after_cursor: int
    ) -> AsyncIterator[StreamEvent]:
        query = urlencode({"after_cursor": after_cursor})
        path = f"/v1/conversations/{quote(conversation_id, safe='')}/events?{query}"
        response = await asyncio.to_thread(self._open, "GET", path, None, "text/event-stream")
        try:
            event_type, data = "message", []
            while True:
                try:
                    raw = await asyncio.to_thread(response.readline)
                except OSError:
                    raise _unavailable(CONNECTION_LOST) from None
                if not raw:
                    raise _unavailable(CONNECTION_LOST)
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith(":"):
                    continue
                if line:
                    field, _, value = line.partition(":")
                    value = value.removeprefix(" ")
                    if field == "event":
                        event_type = value
                    elif field == "data":
                        data.append(value)
                    continue
                if not data:
                    event_type = "message"
                    continue
                payload = "\n".join(data)
                if event_type == "error":
                    raise BffClientError(
                        self._parse(PublicError.model_validate_json, payload)
                    )
                yield self._parse(STREAM_EVENT_ADAPTER.validate_json, payload)
                event_type, data = "message", []
        finally:
            response.close()

    def _request(
        self, method: str, path: str, payload: Any = None, timeout: float | None = None
    ) -> Any:
        return _read_json(self._open(method, path, payload, "application/json", timeout))

    def _open(
        self,
        method: str,
        path: str,
        payload: Any,
        accept: str,
        timeout: float | None = None,
    ) -> HTTPResponse:
        return _open(
            f"{self._base_url}{path}",
            method=method,
            payload=payload,
            timeout=timeout or self._timeout,
            headers={"Authorization": f"Bearer {self._session.token}", "Accept": accept},
        )

    @staticmethod
    def _parse(parse: Any, body: Any) -> Any:
        try:
            return parse(body)
        except ValidationError:
            raise _error(ErrorCode.INTERNAL_ERROR, UNEXPECTED) from None


def _call(url: str, *, method: str, payload: Any, timeout: float) -> Any:
    return _read_json(
        _open(url, method=method, payload=payload, timeout=timeout, headers={})
    )


def _open(
    url: str,
    *,
    method: str,
    payload: Any,
    timeout: float,
    headers: dict[str, str],
) -> HTTPResponse:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, method=method, headers=headers)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        return urlopen(request, timeout=timeout)
    except HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            body = b""
        finally:
            exc.close()
        try:
            error = PublicError.model_validate_json(body)
        except ValidationError:
            if exc.code >= 500:
                raise _unavailable() from None
            raise _error(ErrorCode.INTERNAL_ERROR, UNEXPECTED) from None
        raise BffClientError(error) from None
    except (URLError, OSError):
        raise _unavailable() from None


def _read_json(response: HTTPResponse) -> Any:
    try:
        with response:
            return json.loads(response.read())
    except OSError:
        raise _unavailable(CONNECTION_LOST) from None
    except ValueError:
        raise _error(ErrorCode.INTERNAL_ERROR, UNEXPECTED) from None
