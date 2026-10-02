"""Bounded kitchen planning performed behind the A2A endpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from restaurant_contracts.kitchen import (
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenPlan,
)

from kitchen_agent.chef import (
    ChefDraft,
    create_chef_agent,
    create_chef_client,
    order_prompt,
)
from kitchen_agent.config import Settings
from kitchen_agent.evidence import EvidenceRecorder
from kitchen_agent.knowledge import (
    KnowledgeToolMiddleware,
    create_knowledge_tool,
    knowledge_base_mcp_url,
)
from kitchen_agent.orchestration import ChefCoordination, InvalidSpecialistAnswer
from kitchen_agent.specialists import SpecialistName
from kitchen_agent.validation import build_plan

logger = logging.getLogger(__name__)

FAILURES = {
    KitchenFailureCode.NOT_CONFIGURED: (
        "Cocina no puede consultar la carta: la base de conocimiento no está configurada."
    ),
    KitchenFailureCode.KNOWLEDGE_UNAVAILABLE: (
        "Cocina no puede consultar la carta ahora mismo."
    ),
    KitchenFailureCode.CARTA_NOT_CONSULTED: (
        "Cocina no ha podido comprobar el pedido en la carta."
    ),
    KitchenFailureCode.TIMEOUT: "Cocina no ha respondido a tiempo.",
    KitchenFailureCode.CHEF_UNAVAILABLE: "Cocina no puede responder ahora mismo.",
    KitchenFailureCode.INVALID_PLAN: (
        "Cocina ha devuelto un plan que no supera la validación."
    ),
}

BACKGROUND: set[asyncio.Task[KitchenPlan | KitchenFailure]] = set()


def kitchen_failure(
    order: KitchenOrder, code: KitchenFailureCode
) -> KitchenFailure:
    return KitchenFailure(
        order_id=order.order_id,
        code=code,
        message=FAILURES[code],
    )


def _leave_in_background(task: asyncio.Task[KitchenPlan | KitchenFailure]) -> None:
    task.cancel()
    BACKGROUND.add(task)
    task.add_done_callback(_forget)


def _forget(task: asyncio.Task[KitchenPlan | KitchenFailure]) -> None:
    BACKGROUND.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning(
            "A stopped kitchen task failed while closing: %s",
            type(task.exception()).__name__,
        )


class InvalidChefAnswer(Exception):
    """The chef's answer cannot become a valid plan."""


class KitchenService:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], Any] = create_chef_client,
        specialist_client_factory: Callable[[Settings, SpecialistName], Any]
        | None = None,
        knowledge_tool_factory: Callable[[Settings], Any] = create_knowledge_tool,
        coordination: ChefCoordination | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._knowledge_tool_factory = knowledge_tool_factory
        self._clock = clock
        self._sleeper = sleeper
        create_specialist_client = specialist_client_factory or (
            lambda configured, _: create_chef_client(configured)
        )
        self._coordination = coordination or ChefCoordination(
            lambda specialist: create_specialist_client(settings, specialist)
        )

    async def plan(
        self, order: KitchenOrder
    ) -> KitchenPlan | KitchenFailure:
        if knowledge_base_mcp_url(self._settings) is None:
            return kitchen_failure(order, KitchenFailureCode.NOT_CONFIGURED)
        started = self._clock()
        task = asyncio.create_task(self._cook(order))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task),
                self._settings.kitchen_timeout_seconds,
            )
        except TimeoutError:
            _leave_in_background(task)
            result = kitchen_failure(order, KitchenFailureCode.TIMEOUT)
        except asyncio.CancelledError:
            _leave_in_background(task)
            raise
        except (InvalidChefAnswer, InvalidSpecialistAnswer) as exc:
            logger.warning("Invalid kitchen answer for %s: %s", order.order_id, exc)
            result = kitchen_failure(order, KitchenFailureCode.INVALID_PLAN)
        except Exception as exc:
            logger.warning(
                "Kitchen failed on %s: %s",
                order.order_id,
                type(exc).__name__,
            )
            result = kitchen_failure(order, KitchenFailureCode.CHEF_UNAVAILABLE)
        outcome = (
            result.code.value
            if isinstance(result, KitchenFailure)
            else result.verdict
        )
        logger.info(
            "Kitchen order %s: %s in %.1f s",
            order.order_id,
            outcome,
            self._clock() - started,
        )
        return result

    async def _cook(
        self, order: KitchenOrder
    ) -> KitchenPlan | KitchenFailure:
        result = await self._plan(order)
        if isinstance(result, KitchenPlan) and result.accepted:
            await self._sleeper(
                max(item.estimated_ready_seconds for item in result.accepted)
            )
        return result

    async def _plan(
        self, order: KitchenOrder
    ) -> KitchenPlan | KitchenFailure:
        knowledge_tool = self._knowledge_tool_factory(self._settings)
        recorder = EvidenceRecorder()
        agent = create_chef_agent(
            client=self._client_factory(self._settings),
            knowledge_tool=knowledge_tool,
            middleware=[recorder, KnowledgeToolMiddleware()],
        )
        async with agent:
            if getattr(knowledge_tool, "unavailable", False):
                return kitchen_failure(
                    order, KitchenFailureCode.KNOWLEDGE_UNAVAILABLE
                )
            response = await agent.run(order_prompt(order))
        evidence = recorder.evidence()
        if evidence.retrievals == 0:
            code = (
                KitchenFailureCode.KNOWLEDGE_UNAVAILABLE
                if evidence.failures
                else KitchenFailureCode.CARTA_NOT_CONSULTED
            )
            return kitchen_failure(order, code)
        try:
            draft = response.value
        except (ValidationError, ValueError) as exc:
            raise InvalidChefAnswer(type(exc).__name__) from exc
        if not isinstance(draft, ChefDraft):
            raise InvalidChefAnswer("no structured answer")
        try:
            plan = build_plan(order, draft, evidence)
        except ValidationError as exc:
            raise InvalidChefAnswer(
                f"{exc.error_count()} validation errors"
            ) from exc
        return await self._coordination.review(order, plan)
