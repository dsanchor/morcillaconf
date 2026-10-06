from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    FunctionTool,
    Message,
)

from restaurant_contracts.cashier import Bill, BillStage, CashierFailure, CashierFailureCode

from cashier_agent import progress
from cashier_agent.config import Settings
from cashier_agent.knowledge import KNOWLEDGE_TOOL
from cashier_agent.service import CashierService

from carta_fixtures import CARTA, SERVED, TOTAL


def settings(**changes: Any) -> Settings:
    values = {
        "_env_file": None,
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "azure_ai_model_deployment_name": "test-model",
        "azure_search_endpoint": "https://search.example.net",
        "knowledge_base_name": "conocimiento-restaurante",
        "cashier_timeout_seconds": 5,
    }
    values.update(changes)
    return Settings(**values)


class LookupModel(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Asks the knowledge base once, then names what it found; never an amount."""

    def __init__(self, *, delay: float = 0.0) -> None:
        super().__init__()
        self.prompts: list[str] = []
        self._delay = delay

    def _inner_get_response(self, *, messages: list[Message], stream: bool, options: dict[str, Any], **kwargs: Any):
        async def respond() -> ChatResponse:
            if self._delay:
                await asyncio.sleep(self._delay)
            answered = any(
                content.type == "function_result" for message in messages for content in message.contents
            )
            if not answered:
                self.prompts.append(messages[-1].text)
                call = Content.from_function_call(
                    call_id="lookup-1",
                    name=KNOWLEDGE_TOOL,
                    arguments=json.dumps({"query_variants": ["carta: precio de los platos servidos"]}),
                )
                return ChatResponse(messages=[Message(role="assistant", contents=[call])])
            text = json.dumps({"found": ["morcilla-de-burgos-a-la-brasa"], "missing": []})
            return ChatResponse(messages=[Message(role="assistant", contents=[Content.from_text(text)])])

        return respond()


def knowledge(answer: str) -> Any:
    async def retrieve(query_variants: list[str]) -> str:
        return answer

    return lambda _settings: FunctionTool(name=KNOWLEDGE_TOOL, description="Carta", func=retrieve)


async def test_the_bill_is_priced_from_the_retrieved_carta_and_reported_as_a_step() -> None:
    model = LookupModel()
    steps = []

    async def sink(step) -> None:
        steps.append(step)

    progress.install(sink)
    service = CashierService(
        settings(), client_factory=lambda _: model, knowledge_tool_factory=knowledge(CARTA)
    )

    bill = await service.price(SERVED)

    assert isinstance(bill, Bill) and bill.total == TOTAL
    assert "croquetas-de-morcilla" in model.prompts[0]
    assert '"cantidad"' not in model.prompts[0] and "quantity" not in model.prompts[0]
    assert [(step.component, step.label, step.status) for step in steps] == [
        ("caja", "Caja: consulta precios en la carta", "running"),
        ("caja", "Caja: consulta precios en la carta", "done"),
    ]
    assert "total 17,50 €" in (steps[-1].detail or "")


async def test_a_missing_price_fails_explicitly_instead_of_inventing_one() -> None:
    service = CashierService(
        settings(),
        client_factory=lambda _: LookupModel(),
        knowledge_tool_factory=knowledge("no_results: la base de conocimiento no tiene información."),
    )

    result = await service.price(SERVED)

    assert isinstance(result, CashierFailure)
    assert result.code is CashierFailureCode.PRICE_MISSING


async def test_without_a_knowledge_base_caja_cannot_consult_the_carta() -> None:
    service = CashierService(settings(azure_search_endpoint="", knowledge_base_name=""))

    result = await service.price(SERVED)

    assert result.code is CashierFailureCode.NOT_CONFIGURED
    assert result.message.startswith("Caja no puede consultar la carta")


async def test_a_slow_cashier_answers_a_timeout() -> None:
    service = CashierService(
        settings(cashier_timeout_seconds=1),
        client_factory=lambda _: LookupModel(delay=3),
        knowledge_tool_factory=knowledge(CARTA),
    )

    result = await service.price(SERVED)

    assert result.code is CashierFailureCode.TIMEOUT


async def test_a_model_failure_is_an_explicit_unavailable_cashier() -> None:
    class Broken(LookupModel):
        def _inner_get_response(self, **kwargs: Any):
            raise RuntimeError("model down")

    service = CashierService(
        settings(), client_factory=lambda _: Broken(), knowledge_tool_factory=knowledge(CARTA)
    )

    assert (await service.price(SERVED)).code is CashierFailureCode.CASHIER_UNAVAILABLE


async def test_with_staff_review_on_the_new_bill_waits_for_review() -> None:
    service = CashierService(
        settings(cashier_require_review=True),
        client_factory=lambda _: LookupModel(),
        knowledge_tool_factory=knowledge(CARTA),
    )

    bill = await service.price(SERVED)

    assert bill.stage is BillStage.AWAITING_REVIEW and not bill.payment_options
