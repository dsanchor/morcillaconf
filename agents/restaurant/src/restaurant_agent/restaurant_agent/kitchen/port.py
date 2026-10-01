"""The chef in this process, behind the ``KitchenPort`` the waiter uses.

Each order gets a fresh chef: its own model client and its own connection to
the knowledge base, nothing shared with the waiter's session. The whole plan
is bounded by ``KITCHEN_TIMEOUT_SECONDS`` and every problem becomes an
explicit ``KitchenFailure``: the waiter never receives an invented plan.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from restaurant_contracts.kitchen import (
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenPlan,
)

from restaurant_agent.config import Settings
from restaurant_agent.kitchen.agent import (
    ChefDraft,
    create_chef_agent,
    create_chef_client,
    order_prompt,
)
from restaurant_agent.kitchen.evidence import EvidenceRecorder
from restaurant_agent.kitchen.validation import build_plan
from restaurant_agent.knowledge import (
    KnowledgeToolMiddleware,
    create_knowledge_tool,
    knowledge_base_mcp_url,
)

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


# Chefs cut short by the timeout, still closing their knowledge base connection:
# an in-flight MCP request keeps its session open until the request ends.
BACKGROUND: set[asyncio.Task[KitchenPlan | KitchenFailure]] = set()


def kitchen_failure(order: KitchenOrder, code: KitchenFailureCode) -> KitchenFailure:
    return KitchenFailure(order_id=order.order_id, code=code, message=FAILURES[code])


def _leave_in_background(task: asyncio.Task[KitchenPlan | KitchenFailure]) -> None:
    task.cancel()
    BACKGROUND.add(task)
    task.add_done_callback(_forget)


def _forget(task: asyncio.Task[KitchenPlan | KitchenFailure]) -> None:
    BACKGROUND.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("A stopped chef failed while closing: %s", type(task.exception()).__name__)


class InvalidChefAnswer(Exception):
    """The chef's answer cannot become a valid plan."""


class InProcessKitchen:
    """Runs the chef for one order at a time, in the waiter's process."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], Any] = create_chef_client,
        knowledge_tool_factory: Callable[[Settings], Any] = create_knowledge_tool,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._knowledge_tool_factory = knowledge_tool_factory

    async def plan(self, order: KitchenOrder) -> KitchenPlan | KitchenFailure:
        if knowledge_base_mcp_url(self._settings) is None:
            return kitchen_failure(order, KitchenFailureCode.NOT_CONFIGURED)
        started = time.monotonic()
        chef = asyncio.create_task(self._plan(order))
        try:
            # Shielded, so the waiter gets the answer at the deadline instead
            # of waiting for the chef's connections to close.
            result = await asyncio.wait_for(
                asyncio.shield(chef), self._settings.kitchen_timeout_seconds
            )
        except TimeoutError:
            _leave_in_background(chef)
            result = kitchen_failure(order, KitchenFailureCode.TIMEOUT)
        except asyncio.CancelledError:
            _leave_in_background(chef)
            raise
        except InvalidChefAnswer as exc:
            logger.warning("Invalid chef answer for %s: %s", order.order_id, exc)
            result = kitchen_failure(order, KitchenFailureCode.INVALID_PLAN)
        except Exception as exc:
            # Only the type: model errors may echo the order.
            logger.warning("The chef failed on %s: %s", order.order_id, type(exc).__name__)
            result = kitchen_failure(order, KitchenFailureCode.CHEF_UNAVAILABLE)
        outcome = result.code.value if isinstance(result, KitchenFailure) else result.verdict
        logger.info(
            "Kitchen order %s: %s in %.1f s", order.order_id, outcome, time.monotonic() - started
        )
        return result

    async def _plan(self, order: KitchenOrder) -> KitchenPlan | KitchenFailure:
        knowledge_tool = self._knowledge_tool_factory(self._settings)
        recorder = EvidenceRecorder()
        agent = create_chef_agent(
            client=self._client_factory(self._settings),
            knowledge_tool=knowledge_tool,
            middleware=[recorder, KnowledgeToolMiddleware()],
        )
        async with agent:
            if getattr(knowledge_tool, "unavailable", False):
                # The knowledge base did not answer the connection: no model call.
                return kitchen_failure(order, KitchenFailureCode.KNOWLEDGE_UNAVAILABLE)
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
            return build_plan(order, draft, evidence)
        except ValidationError as exc:
            raise InvalidChefAnswer(f"{exc.error_count()} validation errors") from exc
