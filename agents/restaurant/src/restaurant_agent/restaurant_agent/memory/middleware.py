"""Validate the waiter's memory selection without another model invocation."""

from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware, AgentResponse

from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.memory.options import (
    apply_habitual_order,
    loaded_habitual_order_options,
)


class HabitualOrderMiddleware(AgentMiddleware):
    """Materialize a source-backed, unverified draft after the waiter responds."""

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.stream:
            async def process_stream_result(response: AgentResponse) -> AgentResponse:
                self._apply_selection(context, response)
                return response

            context.stream_result_hooks.append(process_stream_result)
            await call_next()
            return

        await call_next()
        if isinstance(context.result, AgentResponse):
            self._apply_selection(context, context.result)

    def _apply_selection(
        self, context: AgentContext, response: AgentResponse
    ) -> None:
        try:
            result = response.value
        except (ValueError, TypeError):
            # Seating decisions answer with fixed text, not a structured result.
            if not response.text.lstrip().startswith("{"):
                return
            raise
        if not isinstance(result, WaiterModelResult):
            return
        options = loaded_habitual_order_options(
            context.session.state if context.session is not None else {}
        )
        apply_habitual_order(result, options)
