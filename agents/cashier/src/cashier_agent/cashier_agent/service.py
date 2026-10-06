"""Bounded bill pricing performed behind the A2A endpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from restaurant_contracts.cashier import (
    Bill,
    BillRequest,
    BillStage,
    CashierFailure,
    CashierFailureCode,
    cashier_failure,
    euros,
)

from cashier_agent.clerk import (
    PriceLookup,
    create_clerk_agent,
    create_clerk_client,
    lookup_prompt,
)
from cashier_agent.config import Settings
from cashier_agent.evidence import EvidenceRecorder
from cashier_agent.knowledge import (
    KnowledgeToolMiddleware,
    create_knowledge_tool,
    knowledge_base_mcp_url,
)
from cashier_agent.pricing import price_bill
from cashier_agent.progress import Step

logger = logging.getLogger(__name__)

BACKGROUND: set[asyncio.Task[Bill | CashierFailure]] = set()


def _leave_in_background(task: asyncio.Task[Bill | CashierFailure]) -> None:
    task.cancel()
    BACKGROUND.add(task)
    task.add_done_callback(_forget)


def _forget(task: asyncio.Task[Bill | CashierFailure]) -> None:
    BACKGROUND.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning(
            "A stopped cashier task failed while closing: %s",
            type(task.exception()).__name__,
        )


class CashierService:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], Any] = create_clerk_client,
        knowledge_tool_factory: Callable[[Settings], Any] = create_knowledge_tool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._knowledge_tool_factory = knowledge_tool_factory
        self._clock = clock

    @property
    def stage(self) -> BillStage:
        """Where a new bill waits first: the staff review, if on, or the payment."""

        if self._settings.cashier_require_review:
            return BillStage.AWAITING_REVIEW
        return BillStage.AWAITING_PAYMENT

    async def price(self, request: BillRequest) -> Bill | CashierFailure:
        if knowledge_base_mcp_url(self._settings) is None:
            return cashier_failure(request.bill_id, CashierFailureCode.NOT_CONFIGURED)
        started = self._clock()
        task = asyncio.create_task(self._price(request))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), self._settings.cashier_timeout_seconds
            )
        except TimeoutError:
            _leave_in_background(task)
            result = cashier_failure(request.bill_id, CashierFailureCode.TIMEOUT)
        except asyncio.CancelledError:
            _leave_in_background(task)
            raise
        except ValidationError as exc:
            logger.warning(
                "Invalid bill for %s: %s validation errors", request.bill_id, exc.error_count()
            )
            result = cashier_failure(request.bill_id, CashierFailureCode.INVALID_BILL)
        except Exception as exc:
            logger.warning(
                "Cashier failed on %s: %s: %s",
                request.bill_id,
                type(exc).__name__,
                str(exc)[:300],
            )
            result = cashier_failure(request.bill_id, CashierFailureCode.CASHIER_UNAVAILABLE)
        outcome = result.code.value if isinstance(result, CashierFailure) else euros(result.total)
        logger.info(
            "Bill %s: %s in %.1f s", request.bill_id, outcome, self._clock() - started
        )
        return result

    async def _price(self, request: BillRequest) -> Bill | CashierFailure:
        knowledge_tool = self._knowledge_tool_factory(self._settings)
        recorder = EvidenceRecorder()
        agent = create_clerk_agent(
            client=self._client_factory(self._settings),
            knowledge_tool=knowledge_tool,
            middleware=[recorder, KnowledgeToolMiddleware()],
        )
        dishes = len({line.carta_id for line in request.lines})
        step = await Step(
            "caja", "Caja: consulta precios en la carta", f"{dishes} platos servidos"
        ).start()
        async with agent:
            if getattr(knowledge_tool, "unavailable", False):
                await step.finish("Foundry IQ no disponible", failed=True)
                return cashier_failure(request.bill_id, CashierFailureCode.KNOWLEDGE_UNAVAILABLE)
            response = await agent.run(lookup_prompt(request))
        try:
            lookup = response.value
        except (ValidationError, ValueError):
            lookup = None
        if isinstance(lookup, PriceLookup) and lookup.missing:
            # Only logged: the bill reads the retrievals, never the model's word.
            logger.info("The cashier model did not find %s", ", ".join(lookup.missing)[:200])
        evidence = recorder.evidence()
        result = price_bill(request, evidence, stage=self.stage)
        versions = ", ".join(sorted(evidence.versions))
        detail = (
            f"{evidence.retrievals} consultas · carta v{versions or '?'}"
            + (
                f" · total {euros(result.total)}"
                if isinstance(result, Bill)
                else f" · {result.code.value}"
            )
        )
        await step.finish(detail, failed=isinstance(result, CashierFailure))
        return result
