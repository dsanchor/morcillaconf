"""The bar: the waiter checks a round of drinks against the carta, from code.

No model takes part. The service asks the knowledge base for the carta's
drinks through the waiter's own connection: one retrieval, and a second one
only when the first did not bring the whole drinks section. The whole round
is bounded by ``BAR_TIMEOUT_SECONDS``. Without a knowledge base, when it
cannot answer or returns no drinks, and when the drinks section never comes
back whole, the round is an explicit failure and nothing is served: a name is
only resolved against every drink of the carta.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from pydantic import ValidationError

from restaurant_contracts.bar import (
    BarFailure,
    BarFailureCode,
    BarRequest,
    BarRound,
    bar_failure,
)

from restaurant_agent.activity import tracked
from restaurant_agent.bar.evidence import BarEvidence, parse
from restaurant_agent.bar.validation import build_round
from restaurant_agent.knowledge import KNOWLEDGE_TOOL, UNAVAILABLE, KnowledgeBaseTool

logger = logging.getLogger(__name__)

DRINKS_QUERY = (
    "Carta de la casa, Barra: bebidas. Partida: barra, precio y alérgenos de cada bebida"
)
SECTION_QUERY = "Barra: bebidas de la carta de la casa (documento: carta)"
# The asked names also go to the knowledge base, so a dish asked as a drink
# brings its carta entry back and the bar can say it is not a drink.
QUERY_NAMES = 5


class CartaRetrieval(Protocol):
    """Where the bar reads the carta: one knowledge base retrieval as summarized text."""

    async def retrieve(self, query_variants: list[str]) -> str:
        """Never raises: ``UNAVAILABLE`` when the knowledge base cannot answer."""
        ...


class KnowledgeCarta:
    """The waiter's own knowledge base connection, called from code, not by the model."""

    def __init__(self, tool: KnowledgeBaseTool) -> None:
        self._tool = tool

    async def retrieve(self, query_variants: list[str]) -> str:
        try:
            if not self._tool.is_connected:
                await self._tool.connect()
            if self._tool.unavailable or not self._tool.is_connected:
                return UNAVAILABLE
            result = await self._tool.call_tool(KNOWLEDGE_TOOL, query_variants=query_variants)
        except Exception as exc:
            logger.warning("The bar could not read the carta: %s", type(exc).__name__)
            return UNAVAILABLE
        return result if isinstance(result, str) else "\n".join(
            text for item in result if isinstance(text := getattr(item, "text", None), str)
        )


class BarService:
    def __init__(self, carta: CartaRetrieval | None, *, timeout_seconds: float) -> None:
        self._carta = carta
        self._timeout = timeout_seconds

    async def serve(self, request: BarRequest) -> BarRound | BarFailure:
        """The round as the carta allows it, or why the bar cannot check it."""

        if self._carta is None:
            return bar_failure(request.round_id, BarFailureCode.NOT_CONFIGURED)
        names = list(dict.fromkeys(item.name for item in request.items))[:QUERY_NAMES]
        evidence = BarEvidence()
        timed_out = False
        try:
            async with asyncio.timeout(self._timeout):
                await self._lookup([DRINKS_QUERY, *names], evidence)
                if evidence.retrievals and not (evidence.drinks and evidence.bar_section_complete):
                    await self._lookup([SECTION_QUERY, *names], evidence)
        except TimeoutError:
            timed_out = True
        if timed_out and not (evidence.drinks and evidence.bar_section_complete):
            return bar_failure(request.round_id, BarFailureCode.TIMEOUT)
        if not evidence.retrievals:
            return bar_failure(request.round_id, BarFailureCode.KNOWLEDGE_UNAVAILABLE)
        if not evidence.drinks:
            return bar_failure(request.round_id, BarFailureCode.CARTA_NOT_CONSULTED)
        if not evidence.bar_section_complete:
            # A name is ambiguous or not on the carta only against every drink.
            logger.info("Round %s: the drinks section came back cut", request.round_id)
            return bar_failure(request.round_id, BarFailureCode.CARTA_INCOMPLETE)
        try:
            return build_round(request, evidence)
        except ValidationError as exc:
            logger.warning("Round %s: %s validation errors", request.round_id, exc.error_count())
            return bar_failure(request.round_id, BarFailureCode.BAR_UNAVAILABLE)

    async def _lookup(self, query_variants: list[str], evidence: BarEvidence) -> None:
        with tracked("foundry_iq", "Foundry IQ: bebidas de la carta") as step:
            text = await self._carta.retrieve(query_variants)
            parse([text], evidence)
            if text.startswith(UNAVAILABLE.split(":", 1)[0]):
                step.detail = "Foundry IQ no disponible"
                step.failed = True
            else:
                versions = ", ".join(sorted(evidence.versions)) or "?"
                step.detail = f"{len(evidence.drinks)} bebidas en la carta v{versions}"
