"""Middleware that materializes memory-backed customer intent."""

from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware, AgentResponse

from restaurant_agent.contracts import (
    OrderDraft,
    OrderItemDraft,
    WaiterModelResult,
)
from restaurant_agent.memory.contracts import MemoryIntent
from restaurant_agent.memory.intent import (
    IntentClassifier,
    classify_memory_intent,
)


def apply_habitual_order(
    result: WaiterModelResult,
    latest_order_preference: str | None,
) -> None:
    """Apply a model-classified repeat intent to the structured draft."""

    if (
        result.memory_intent is not MemoryIntent.REUSE_LATEST_ORDER
        or not latest_order_preference
    ):
        return

    item_names = [
        item.strip()
        for item in latest_order_preference.split(",")
        if item.strip()
    ]
    if not item_names:
        return

    result.order_draft = OrderDraft(
        items=[OrderItemDraft(name=item_name) for item_name in item_names]
    )
    joined_items = ", ".join(item_names)
    customer_name = result.customer.presented_name
    greeting = f"{customer_name}, " if customer_name else ""
    result.reply = (
        f"Claro, {greeting}he añadido {joined_items} a tu borrador habitual. "
        "Los productos todavía no están verificados ni el pedido confirmado."
    )


class HabitualOrderMiddleware(AgentMiddleware):
    """Turn the model's semantic memory intent into a deterministic draft."""

    def __init__(self, classifier: IntentClassifier) -> None:
        self._classifier = classifier

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.stream:
            async def process_stream_result(
                response: AgentResponse,
            ) -> AgentResponse:
                await self._apply_intent(context, response)
                return response

            context.stream_result_hooks.append(process_stream_result)
            await call_next()
            return

        await call_next()
        if not isinstance(context.result, AgentResponse):
            return
        await self._apply_intent(context, context.result)

    async def _apply_intent(
        self,
        context: AgentContext,
        response: AgentResponse,
    ) -> None:
        result = response.value
        if not isinstance(result, WaiterModelResult):
            return
        latest_order_preference = (
            context.session.state.get("latest_order_preference")
            if context.session is not None
            else None
        )
        if (
            result.memory_intent is MemoryIntent.NONE
            and isinstance(latest_order_preference, str)
        ):
            current_message = context.messages[-1].text if context.messages else None
            if current_message:
                result.memory_intent = await classify_memory_intent(
                    self._classifier,
                    current_message,
                )
        apply_habitual_order(
            result,
            latest_order_preference=(
                latest_order_preference
                if isinstance(latest_order_preference, str)
                else None
            ),
        )
