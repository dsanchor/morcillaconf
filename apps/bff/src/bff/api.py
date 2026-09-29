"""Versioned HTTP API of the BFF: sessions, commands, results, snapshot, room and SSE."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    ErrorCode,
)

from bff.service import (
    DemoSession,
    Heartbeat,
    PublicFailure,
    RestaurantService,
    StreamFailure,
)

logger = logging.getLogger(__name__)

HTTP_STATUS = {
    ErrorCode.INVALID_COMMAND: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.TURN_LIMIT_EXCEEDED: 409,
    ErrorCode.CURSOR_EXPIRED: 410,
    ErrorCode.UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}
INVALID_COMMAND = "El comando no es válido."
INVALID_SESSION = "Dinos un nombre válido para entrar."
UNKNOWN_ROUTE = "Esa ruta no existe."
INTERNAL_ERROR = "Algo ha fallado en el restaurante. Vuelve a intentarlo."

router = APIRouter(prefix="/v1")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)


def get_service(request: Request) -> RestaurantService:
    return request.app.state.service


def current_session(
    service: Annotated[RestaurantService, Depends(get_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> DemoSession:
    scheme, _, token = (authorization or "").partition(" ")
    return service.authenticate(token.strip() if scheme.lower() == "bearer" else None)


Service = Annotated[RestaurantService, Depends(get_service)]
Session = Annotated[DemoSession, Depends(current_session)]


@router.post("/sessions", status_code=201)
async def open_session(request: Request, service: Service) -> JSONResponse:
    try:
        body = SessionRequest.model_validate_json(await request.body())
    except ValidationError:
        raise PublicFailure(ErrorCode.INVALID_COMMAND, INVALID_SESSION) from None
    opened = service.open_session(body.name)
    return JSONResponse(
        status_code=201,
        content={
            "token": opened.token,
            "token_type": "bearer",
            "identity": opened.identity.model_dump(mode="json"),
            "presented_name": opened.presented_name,
            "active_visit_id": opened.active_visit_id,
            "waiter": opened.waiter,
            "expires_at": opened.expires_at.isoformat(),
        },
    )


@router.post("/commands")
async def submit_command(request: Request, service: Service, session: Session) -> JSONResponse:
    try:
        command = COMMAND_ADAPTER.validate_json(await request.body())
    except ValidationError:
        raise PublicFailure(ErrorCode.INVALID_COMMAND, INVALID_COMMAND) from None
    result = await service.submit(session, command)
    return JSONResponse(
        status_code=202 if result.status == "pending" else 200,
        content=COMMAND_RESULT_ADAPTER.dump_python(result, mode="json"),
    )


@router.get("/commands/{event_id}")
async def get_result(event_id: str, service: Service, session: Session) -> JSONResponse:
    result = service.get_result(session, event_id)
    return JSONResponse(content=COMMAND_RESULT_ADAPTER.dump_python(result, mode="json"))


@router.get("/conversations/{conversation_id}/snapshot")
async def get_snapshot(
    conversation_id: str, service: Service, session: Session
) -> JSONResponse:
    snapshot = service.get_snapshot(session, conversation_id)
    return JSONResponse(content=snapshot.model_dump(mode="json"))


@router.get("/conversations/{conversation_id}/room")
async def get_room(conversation_id: str, service: Service, session: Session) -> JSONResponse:
    room = await service.room(session, conversation_id)
    return JSONResponse(content=room.model_dump(mode="json"))


@router.get("/conversations/{conversation_id}/events")
async def stream_events(
    conversation_id: str,
    service: Service,
    session: Session,
    after_cursor: str | None = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    raw = last_event_id if last_event_id is not None else after_cursor
    cursor = _parse_cursor(raw)
    service.check_stream(session, conversation_id, cursor)
    return StreamingResponse(
        _sse(service, conversation_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_cursor(raw: str | None) -> int:
    if raw is None:
        return 0
    value = raw.strip()
    if not (value.isascii() and value.isdigit()):
        raise PublicFailure(ErrorCode.INVALID_COMMAND, "El cursor no es válido.")
    return int(value)


async def _sse(
    service: RestaurantService, conversation_id: str, after_cursor: int
) -> AsyncIterator[str]:
    async for item in service.stream(conversation_id, after_cursor):
        if isinstance(item, Heartbeat):
            yield ": ping\n\n"
        elif isinstance(item, StreamFailure):
            yield f"event: error\ndata: {item.error.model_dump_json()}\n\n"
            return
        else:
            data = STREAM_EVENT_ADAPTER.dump_json(item).decode()
            yield f"id: {item.cursor}\nevent: {item.event_type}\ndata: {data}\n\n"


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, str]:
        service = get_service(request)
        return {
            "status": "ok",
            "waiter": service.waiter_mode,
            "seating": "on" if service.seating_enabled else "off",
        }

    @app.exception_handler(PublicFailure)
    async def public_failure(request: Request, exc: PublicFailure) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(PublicFailure(ErrorCode.INVALID_COMMAND, INVALID_COMMAND))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_COMMAND
        message = UNKNOWN_ROUTE if exc.status_code == 404 else INVALID_COMMAND
        response = _error_response(PublicFailure(code, message))
        response.status_code = exc.status_code
        return response

    @app.middleware("http")
    async def unexpected(request: Request, call_next):
        # Handled here, not with an Exception handler: Starlette's
        # ServerErrorMiddleware would re-raise and log the full traceback,
        # which may contain customer content. Only the type is logged.
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                "Unexpected BFF error on %s: %s", request.url.path, type(exc).__name__
            )
            return _error_response(PublicFailure(ErrorCode.INTERNAL_ERROR, INTERNAL_ERROR))


def _error_response(failure: PublicFailure) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if failure.code == ErrorCode.UNAUTHENTICATED else None
    return JSONResponse(
        status_code=HTTP_STATUS[failure.code],
        content=failure.to_error().model_dump(mode="json"),
        headers=headers,
    )
