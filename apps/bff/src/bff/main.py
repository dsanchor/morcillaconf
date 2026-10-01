"""ASGI application factory: ``uvicorn --factory bff.main:create_app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI

from bff import api
from bff.adapters import create_waiter
from bff.config import BffSettings
from bff.service import RestaurantService
from bff.storage import Database
from bff.waiter import WaiterPort

logger = logging.getLogger(__name__)

WaiterFactory = Callable[[BffSettings], WaiterPort]


def create_app(
    settings: BffSettings | None = None,
    *,
    waiter_factory: WaiterFactory = create_waiter,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings or BffSettings()
        service = RestaurantService(
            database=Database(
                config.bff_database_path,
                event_retention=config.bff_event_retention,
                journal_mode=config.bff_sqlite_journal_mode,
            ),
            waiter=waiter_factory(config),
            max_turns=config.waiter_max_turns,
            session_ttl=timedelta(hours=config.bff_session_ttl_hours),
            heartbeat_seconds=config.bff_sse_heartbeat_seconds,
            clock=clock,
        )
        recovered = service.recover_interrupted_turns()
        if recovered:
            logger.warning("Recovered %d interrupted waiter turns", recovered)
        logger.info("BFF ready with the %s waiter", service.waiter_mode)
        app.state.service = service
        yield
        await service.shutdown()

    app = FastAPI(title="Morcillaconf BFF", version="0.1.0", lifespan=lifespan)
    api.install(app)
    return app
