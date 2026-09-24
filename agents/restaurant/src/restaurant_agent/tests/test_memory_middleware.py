from types import SimpleNamespace

import pytest
from agent_framework import AgentContext, AgentResponse, Message

from restaurant_agent.contracts import (
    CustomerSnapshot,
    WaiterModelResult,
)
from restaurant_agent.memory.contracts import MemoryIntent
from restaurant_agent.memory.intent import MemoryIntentDecision
from restaurant_agent.memory.middleware import HabitualOrderMiddleware, apply_habitual_order


class FakeClassifier:
    def __init__(self, intent: MemoryIntent) -> None:
        self.intent = intent
        self.prompts: list[str] = []

    async def run(
        self,
        messages: str,
        *,
        options: dict[str, object],
    ) -> AgentResponse[MemoryIntentDecision]:
        self.prompts.append(messages)
        return AgentResponse(
            value=MemoryIntentDecision(memory_intent=self.intent)
        )


def test_habitual_order_intent_materializes_latest_memory_as_draft() -> None:
    result = WaiterModelResult(
        reply="Respuesta provisional.",
        customer=CustomerSnapshot(presented_name="David"),
        pending_fields=[],
        memory_intent=MemoryIntent.REUSE_LATEST_ORDER,
    )

    apply_habitual_order(result, "tortilla de patata, agua con gas")

    assert [item.name for item in result.order_draft.items] == [
        "tortilla de patata",
        "agua con gas",
    ]
    assert all(item.quantity == 1 for item in result.order_draft.items)
    assert "David" in result.reply
    assert "todavía no están verificados" in result.reply


def test_no_memory_intent_does_not_change_draft() -> None:
    result = WaiterModelResult(
        reply="¿Qué deseas pedir?",
        customer=CustomerSnapshot(presented_name="David"),
        pending_fields=[],
        memory_intent=MemoryIntent.NONE,
    )

    apply_habitual_order(result, "tortilla de patata, agua con gas")

    assert result.order_draft.items == []
    assert result.reply == "¿Qué deseas pedir?"


@pytest.mark.asyncio
async def test_middleware_uses_semantic_classifier_as_fallback() -> None:
    classifier = FakeClassifier(MemoryIntent.REUSE_LATEST_ORDER)
    middleware = HabitualOrderMiddleware(classifier)
    result = WaiterModelResult(
        reply="¿Qué deseas pedir?",
        customer=CustomerSnapshot(presented_name="David"),
        pending_fields=[],
        memory_intent=MemoryIntent.NONE,
    )
    session = SimpleNamespace(
        state={"latest_order_preference": "tortilla de patata, agua con gas"}
    )
    context = AgentContext(
        agent=object(),
        messages=[Message("user", ["Ponme aquello que suelo pedir"])],
        session=session,
        result=AgentResponse(value=result),
    )

    async def call_next() -> None:
        return None

    await middleware.process(context, call_next)

    assert classifier.prompts
    assert result.memory_intent is MemoryIntent.REUSE_LATEST_ORDER
    assert [item.name for item in result.order_draft.items] == [
        "tortilla de patata",
        "agua con gas",
    ]
